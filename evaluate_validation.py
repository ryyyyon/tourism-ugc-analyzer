"""
人がつけた正解と、分類の結果を突き合わせて精度を出す

使い方:
  python evaluate_validation.py              # 最新の検証で、検証を作った時点の分類結果の精度を出す
  python evaluate_validation.py --all        # これまでのすべての検証（最初の90本、成田30本など）をまとめて精度を出す
  python evaluate_validation.py --all --themes グルメ 祭り・イベント 歴史・寺社   # テーマ別の精度を出すテーマを指定
  python evaluate_validation.py --current    # 同じ動画を、今のルールで分類し直して精度を出す
     ※ --current は、検証の結果を見てルールを直した後に使う。同じ動画を見ながら直しているので、
        精度は実際より良く出やすい（楽観的な見積もり）ことに注意
  正解は、次のどちらかから読み込む（CSVがあればそちらを優先）
    - 記入ページ（make_label_gallery.py）でダウンロードした labels_<日時>.csv
      （data/validation/ か、パソコンのダウンロードフォルダを探す）
    - Excelの labeling_<日時>.xlsx

出す指標:
  1. 観光／観光以外（ノイズ）の判定の正解率
  2. 言語の正解率、国内／海外の判定の正解率
  3. テーマの正解率
       厳密: 主テーマが正解と一致
       ゆるめ: 正解のテーマが、当てはまったテーマのどれかに入っている
  4. テーマ別の精度（主張に使うテーマ：グルメ、祭り・イベント、歴史・寺社）
       適合率: 分類でそのテーマとした動画のうち、人も同じテーマとした割合
               （「グルメの動画は○倍見られる」の「グルメの動画」が、本当にグルメの動画か）
       再現率: 人がそのテーマとした動画のうち、分類も同じテーマだった割合
               （ランダムに選んだ検証だけで計算。テーマを指定して選んだ検証は、再現率には使わない）
  正解率には95%信頼区間（ウィルソンの方法）を付ける。本数が少ないと区間が広くなる

注意: 検証用の動画は、地域ごとに国内・海外を同じ本数ずつ選んでいる。
     そのため、国内・海外を分けた正解率の方が実態を表している。
     テーマ別の適合率を確かめたいときは、make_validation_sample.py --themes で
     そのテーマに分類された動画を選んで記入すると、本数を増やせる。
"""

import argparse
import math
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from region_config import DATA_ROOT

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 200)

FOCUS_THEMES = ["グルメ", "祭り・イベント", "歴史・寺社"]  # 企画書の主張（食と祭りは寺の約4倍）に使うテーマ

LANG_GROUP = {"ja": "日本語", "en": "英語", "ko": "韓国語", "zh-tw": "中国語", "zh-cn": "中国語",
              "zh-hk": "中国語", "zh": "中国語", "th": "タイ語", "es": "スペイン語",
              "zh_or_ja": "不明", "unknown": "不明"}


def wilson(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return center - half, center + half


def score(label, ok):
    k, n = int(ok.sum()), int(len(ok))
    lo, hi = wilson(k, n)
    rate = f"{k / n:.0%}" if n else "-"
    ci = f"{lo:.0%}〜{hi:.0%}" if n else "-"
    return {"項目": label, "正解": k, "本数": n, "正解率": rate, "95%信頼区間": ci}


def current_predictions(pred):
    """検証用の動画を、今のルールで分類し直す"""
    from classify_themes import build_themed_videos
    from region_config import latest, list_regions
    cols = ["video_id", "is_noise", "primary_theme", "themes", "lang_final", "market"]
    frames = []
    for slug in list_regions():
        if latest(slug, "videos_check_*.csv") is None:
            continue  # まだデータを集めていない地域
        region, videos, _, _ = build_themed_videos(slug)
        frames.append(videos[cols].assign(region=region["name"].split("（")[0]))
    cur = pd.concat(frames)
    keep = [c for c in ["set", "sample", "番号", "video_id", "region", "title"] if c in pred.columns]
    out = pred[keep].merge(cur, on=["region", "video_id"], how="left")
    missing = int(out["primary_theme"].isna().sum())
    if missing:
        print(f"（今のルールでは地域に関連しないと判定された動画: {missing}本。ノイズとして扱う）")
        out["is_noise"] = out["is_noise"].fillna(True)
        out["primary_theme"] = out["primary_theme"].fillna("関連なし")
        out["market"] = out["market"].fillna("不明")
        out["lang_final"] = out["lang_final"].fillna("unknown")
    return out


def find_labels(vdir, xlsx):
    """1回分の検証の正解を読む（記入ページのCSVがあればそちらを優先）"""
    stamp = xlsx.stem.replace("labeling_", "")
    csv_candidates = [vdir / f"labels_{stamp}.csv", Path.home() / "Downloads" / f"labels_{stamp}.csv"]
    csv_path = next((p for p in csv_candidates if p.exists()), None)
    if csv_path:
        lab, src = pd.read_csv(csv_path), csv_path
    else:
        lab, src = pd.read_excel(xlsx, sheet_name="ラベル付け"), xlsx
    lab = lab[lab["番号"].astype(str) != "例"].copy()
    lab["番号"] = lab["番号"].astype(int)
    return lab.dropna(subset=["正解_観光関連か"]), len(lab), src


def load_sets(vdir, use_all, current):
    """検証の正解と分類結果を突き合わせた表（--all なら、これまでの検証をすべてまとめる）"""
    files = sorted(vdir.glob("labeling_*.xlsx"))
    if not files:
        sys.exit("labeling_*.xlsx がありません。先に make_validation_sample.py を実行してください。")
    frames = []
    for xlsx in (files if use_all else files[-1:]):
        stamp = xlsx.stem.replace("labeling_", "")
        pred_path = vdir / f"predictions_{stamp}.csv"
        if not pred_path.exists():
            print(f"（{pred_path.name} がないので {xlsx.name} は飛ばします）")
            continue
        labeled, total, src = find_labels(vdir, xlsx)
        print(f"検証 {stamp}: 記入済み {len(labeled)}本 / {total}本（ラベル: {src.name}）")
        if labeled.empty:
            continue
        pred = pd.read_csv(pred_path).assign(set=stamp)
        if "sample" not in pred.columns:
            pred["sample"] = "random"  # 以前の検証は、すべてランダムに選んでいる
        if current:
            pred = current_predictions(pred)
        frames.append(labeled.merge(pred, on="番号", suffixes=("", "_pred")))
    if not frames:
        sys.exit("まだ1本も記入されていません。")
    return pd.concat(frames, ignore_index=True)


def theme_metrics(both, themes):
    """テーマ別の適合率・再現率（全体と、海外向けの動画だけ）"""
    rows = []
    rand = both[both["sample"] == "random"]
    for th in themes:
        for label, d, r in [("全体", both, rand), ("海外向け", both[both["market"] == "海外"],
                                                  rand[rand["market"] == "海外"])]:
            pred_t = d["primary_theme"] == th
            rows.append({**score(f"{th}（{label}）：適合率", d.loc[pred_t, "人_テーマ"] == th), "テーマ": th})
            human_t = r["人_テーマ"] == th
            rows.append({**score(f"{th}（{label}）：再現率", r.loc[human_t, "primary_theme"] == th), "テーマ": th})
    out = pd.DataFrame(rows)
    return out[out["本数"] > 0].reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", action="store_true", help="今のルールで分類し直して精度を出す")
    parser.add_argument("--all", action="store_true", help="これまでのすべての検証をまとめて精度を出す")
    parser.add_argument("--themes", nargs="+", default=FOCUS_THEMES, help="テーマ別の精度を出すテーマ")
    args = parser.parse_args()
    vdir = DATA_ROOT / "validation"
    if args.current:
        print("【今のルールで分類し直した精度】※検証結果を見て直したルールなので、楽観的な見積もり\n")
    df = load_sets(vdir, args.all, args.current)
    print(f"\n合計: {len(df)}本（うちテーマを指定して選んだ動画 {int((df['sample'] != 'random').sum())}本）")
    randoms = df["sample"] == "random"
    df["人_ノイズ"] = df["正解_観光関連か"] == "観光以外"
    df["分類_ノイズ"] = df["is_noise"].astype(bool)
    df["分類_言語"] = df["lang_final"].map(LANG_GROUP).fillna("その他")
    df["人_市場"] = df["正解_言語"].map(lambda x: "国内" if x == "日本語" else ("海外" if isinstance(x, str) else None))

    themed = df[~randoms]
    df = df[randoms]  # 全体の精度は、ランダムに選んだ動画だけで出す（テーマを指定して選んだ動画は偏っているため）

    result = None
    both = pd.DataFrame(columns=list(df.columns) + ["人_テーマ"])
    if df.empty:
        print("\n（ランダムに選んだ検証がないので、全体の精度は出しません。これまでの検証とまとめるなら --all を付けてください）")
    else:
        rows = []
        # 1. 観光／観光以外
        rows.append(score("観光/観光以外の判定（全体）", df["人_ノイズ"] == df["分類_ノイズ"]))
        for m in ["国内", "海外", "不明"]:
            d = df[df["market"] == m]
            if len(d):
                rows.append(score(f"　うち分類で{m}とした動画", d["人_ノイズ"] == d["分類_ノイズ"]))

        # 2. 言語
        d = df.dropna(subset=["正解_言語"])
        known = d[d["分類_言語"] != "不明"]
        rows.append(score("言語（分類が「不明」以外）", known["分類_言語"] == known["正解_言語"]))
        rows.append(score("国内/海外の判定（分類が「不明」以外）", known["market"] == known["人_市場"]))
        for m in ["国内", "海外"]:
            dm = known[known["market"] == m]
            if len(dm):
                rows.append(score(f"　うち分類で{m}とした動画", dm["market"] == dm["人_市場"]))
        n_unknown = int((d["分類_言語"] == "不明").sum())

        # 3. テーマ（人も分類も「観光」とした動画）
        both = df[~df["人_ノイズ"] & ~df["分類_ノイズ"]].dropna(subset=["正解_テーマ"]).copy()
        both["人_テーマ"] = both["正解_テーマ"].replace({"どれにも当てはまらない": "未分類"})
        strict = both["primary_theme"] == both["人_テーマ"]
        loose = both.apply(lambda r: r["人_テーマ"] in str(r["themes"]).split("|") or strict[r.name], axis=1)
        rows.append(score("テーマ（厳密：主テーマが一致）", strict))
        rows.append(score("テーマ（ゆるめ：当てはまったテーマに含まれる）", loose))

        result = pd.DataFrame(rows)
        print("\n=== 分類の精度 ===")
        print(result.to_string(index=False))
        print(f"（言語の分類が「不明」だった動画: {n_unknown}本。上の言語の正解率からは除いている）")

        # 4. 地域別のテーマ正解率
        print("\n=== 地域別：テーマの正解率（厳密） ===")
        for region, d in both.groupby("region"):
            s = score(region, d["primary_theme"] == d["人_テーマ"])
            print(f"  {region}: {s['正解率']}（{s['正解']}/{s['本数']}本、95%信頼区間 {s['95%信頼区間']}）")

    # 4-2. テーマ別の精度（主張に使うテーマ）
    both_all = both
    if len(themed):
        t2 = themed.copy()
        t2["人_ノイズ"] = t2["正解_観光関連か"] == "観光以外"
        t2["分類_ノイズ"] = t2["is_noise"].astype(bool)
        t2 = t2[~t2["人_ノイズ"] & ~t2["分類_ノイズ"]].dropna(subset=["正解_テーマ"]).copy()
        t2["人_テーマ"] = t2["正解_テーマ"].replace({"どれにも当てはまらない": "未分類"})
        both_all = pd.concat([both, t2], ignore_index=True)
    tm = theme_metrics(both_all, args.themes)
    print("\n=== テーマ別の精度（企画書の主張に使うテーマ） ===")
    print("（適合率: 分類でそのテーマとした動画のうち、人も同じテーマとした割合。"
          "再現率: 人がそのテーマとした動画のうち、分類も同じだった割合。再現率はランダムに選んだ動画だけで計算）")
    print(tm.drop(columns="テーマ").to_string(index=False))
    for th in args.themes:
        wrong = both_all[(both_all["primary_theme"] == th) & (both_all["人_テーマ"] != th)]
        if len(wrong):
            print(f"  分類で{th}としたが、人は別のテーマとした動画の正解: " +
                  "、".join(f"{k} {v}本" for k, v in wrong["人_テーマ"].value_counts().items()))
    print("（読み方：分類の誤りが偶然に起きているなら、テーマの効果は実際より小さめ＝1倍に近い方に出やすい）")

    # 5. 間違えた動画の一覧（辞書の改善に使う）
    print("\n=== 間違えた動画（辞書を直すときの参考に） ===")
    if result is not None:
        wrong_noise = df[df["人_ノイズ"] != df["分類_ノイズ"]]
        for _, r in wrong_noise.iterrows():
            p = f"ノイズ（{r['primary_theme']}）" if r["分類_ノイズ"] else f"観光（{r['primary_theme']}）"
            print(f"  [観光判定] #{r['番号']} 分類={p} / 正解={r['正解_観光関連か']} | {str(r['title'])[:45]}")
        for _, r in known[known["分類_言語"] != known["正解_言語"]].iterrows():
            print(f"  [言語] #{r['番号']} 分類={r['分類_言語']} / 正解={r['正解_言語']} | {str(r['title'])[:45]}")
    for _, r in both_all[both_all["primary_theme"] != both_all["人_テーマ"]].iterrows():
        print(f"  [テーマ] #{r['番号']} 分類={r['primary_theme']} / 正解={r['人_テーマ']} | {str(r['title'])[:45]}")

    suffix = ("_current" if args.current else "") + ("_all" if args.all else "")
    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    if result is not None:
        out = vdir / f"result{suffix}_{stamp}.csv"
        result.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n保存しました: {out}")
    out_t = vdir / f"result_themes{suffix}_{stamp}.csv"
    tm.to_csv(out_t, index=False, encoding="utf-8-sig")
    print(f"保存しました: {out_t}")


if __name__ == "__main__":
    main()
