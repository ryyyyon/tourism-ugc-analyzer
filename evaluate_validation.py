"""
人がつけた正解と、分類の結果を突き合わせて精度を出す

使い方:
  python evaluate_validation.py              # 検証を作った時点の分類結果で精度を出す
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
  正解率には95%信頼区間（ウィルソンの方法）を付ける。本数が少ないと区間が広くなる

注意: 検証用の動画は、地域ごとに国内・海外を同じ本数ずつ選んでいる。
     そのため、国内・海外を分けた正解率の方が実態を表している。
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
    from region_config import list_regions
    cols = ["video_id", "is_noise", "primary_theme", "themes", "lang_final", "market"]
    frames = []
    for slug in list_regions():
        region, videos, _, _ = build_themed_videos(slug)
        frames.append(videos[cols].assign(region=region["name"].split("（")[0]))
    cur = pd.concat(frames)
    out = pred[["番号", "video_id", "region", "title"]].merge(cur, on=["region", "video_id"], how="left")
    missing = int(out["primary_theme"].isna().sum())
    if missing:
        print(f"（今のルールでは地域に関連しないと判定された動画: {missing}本。ノイズとして扱う）")
        out["is_noise"] = out["is_noise"].fillna(True)
        out["primary_theme"] = out["primary_theme"].fillna("関連なし")
        out["market"] = out["market"].fillna("不明")
        out["lang_final"] = out["lang_final"].fillna("unknown")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", action="store_true", help="今のルールで分類し直して精度を出す")
    args = parser.parse_args()
    vdir = DATA_ROOT / "validation"
    files = sorted(vdir.glob("labeling_*.xlsx"))
    if not files:
        sys.exit("labeling_*.xlsx がありません。先に make_validation_sample.py を実行してください。")
    xlsx = files[-1]
    stamp = xlsx.stem.replace("labeling_", "")
    pred_path = vdir / f"predictions_{stamp}.csv"
    csv_candidates = [vdir / f"labels_{stamp}.csv", Path.home() / "Downloads" / f"labels_{stamp}.csv"]
    csv_path = next((p for p in csv_candidates if p.exists()), None)
    if csv_path:
        print(f"ラベル: {csv_path}（記入ページから）\n分類結果: {pred_path}\n")
        lab = pd.read_csv(csv_path)
    else:
        print(f"ラベル: {xlsx}\n分類結果: {pred_path}\n")
        lab = pd.read_excel(xlsx, sheet_name="ラベル付け")
    lab = lab[lab["番号"].astype(str) != "例"].copy()
    lab["番号"] = lab["番号"].astype(int)
    labeled = lab.dropna(subset=["正解_観光関連か"])
    print(f"記入済み: {len(labeled)}本 / {len(lab)}本")
    if labeled.empty:
        sys.exit("まだ1本も記入されていません。")

    pred = pd.read_csv(pred_path)
    if args.current:
        print("【今のルールで分類し直した精度】※検証結果を見て直したルールなので、楽観的な見積もり\n")
        pred = current_predictions(pred)
    df = labeled.merge(pred, on="番号", suffixes=("", "_pred"))
    df["人_ノイズ"] = df["正解_観光関連か"] == "観光以外"
    df["分類_ノイズ"] = df["is_noise"].astype(bool)
    df["分類_言語"] = df["lang_final"].map(LANG_GROUP).fillna("その他")
    df["人_市場"] = df["正解_言語"].map(lambda x: "国内" if x == "日本語" else ("海外" if isinstance(x, str) else None))

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

    # 5. 間違えた動画の一覧（辞書の改善に使う）
    print("\n=== 間違えた動画（辞書を直すときの参考に） ===")
    wrong_noise = df[df["人_ノイズ"] != df["分類_ノイズ"]]
    for _, r in wrong_noise.iterrows():
        p = f"ノイズ（{r['primary_theme']}）" if r["分類_ノイズ"] else f"観光（{r['primary_theme']}）"
        print(f"  [観光判定] #{r['番号']} 分類={p} / 正解={r['正解_観光関連か']} | {str(r['title'])[:45]}")
    for _, r in known[known["分類_言語"] != known["正解_言語"]].iterrows():
        print(f"  [言語] #{r['番号']} 分類={r['分類_言語']} / 正解={r['正解_言語']} | {str(r['title'])[:45]}")
    for _, r in both[~strict].iterrows():
        print(f"  [テーマ] #{r['番号']} 分類={r['primary_theme']} / 正解={r['人_テーマ']} | {str(r['title'])[:45]}")

    suffix = "_current" if args.current else ""
    out = vdir / f"result{suffix}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    result.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n保存しました: {out}")


if __name__ == "__main__":
    main()
