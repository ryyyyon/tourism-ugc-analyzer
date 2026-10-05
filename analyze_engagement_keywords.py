"""
ステップ6: スポット・特産品のキーワード集計と、エンゲージメントのパターン分析

使い方:
  python analyze_engagement_keywords.py                  # 全地域で回帰、銚子でキーワード集計
  python analyze_engagement_keywords.py --base choshi

前提: 各地域でステップ1〜3を実行済み。APIは使わない。
必要なライブラリ: statsmodels（pip install statsmodels）

【A. キーワード集計】
  regions/<地域>.json の "spots" に書いたスポット名・特産品名が、
  国内向け・海外向けの動画（タイトル＋説明文の冒頭）にどれくらい出てくるかを数える

【B. エンゲージメントのパターン分析（重回帰分析）】
  「鎌倉の動画だから伸びた」のか「テーマや形式のおかげで伸びた」のかを切り分けるため、
  地域・テーマ・動画の形式・言語・投稿からの経過日数を同時にモデルに入れ、
  他の条件をそろえたときに、それぞれの要素が数値を何倍にするかを推定する
    目的変数1: 再生数（届いた量）                       → log10(再生数 + 1)
    目的変数2: エンゲージメント率（いいね＋コメント）÷再生数 → log10（再生100回以上の動画のみ）
  - 対数をとるのは、再生数が数回〜数百万回と桁違いに散らばっているため
  - 誤差のばらつきが動画ごとに違うことを考え、頑健な標準誤差（HC3）を使う
  - 基準: テーマ「絶景・自然」、地域は --base の地域、言語「英語」
  - 本数が少ないテーマは「その他」にまとめる（推定が不安定になるため）

【C. 頑健性の確認（fetch_channel_stats.py を実行済みのときだけ）】
  チャンネルの登録者数を加えたモデルと比べ、「人気のチャンネルだから伸びた」効果を除いても
  テーマや形式の効果が残るかを確かめる。公平に比べるため、両方とも登録者数が分かる動画だけで推定する

読むときの注意:
  - 検索上位のサンプルであり、全投稿ではない
  - 相関の分析であり、「そのテーマにすれば必ず伸びる」という因果までは言えない
"""

import argparse
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from classify_themes import build_themed_videos
from region_config import DATA_ROOT, data_dir, list_regions, load_region
from theme_rules import normalize

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 220)

BASE_THEME = "絶景・自然"
MIN_THEME_N = 15          # これより少ないテーマは「その他」にまとめる
MIN_VIEWS_FOR_RATE = 100  # エンゲージメント率は再生100回以上の動画だけで見る
FORMAT_COLS = {
    "散歩・車窓（言語不要）": "fmt_walk",
    "ショート動画": "fmt_short",
    "Vlog": "fmt_vlog",
    "ガイド・おすすめ": "fmt_guide",
}
LANG_GROUPS = {"en": "英語", "ko": "韓国語", "zh-tw": "中国語", "zh-cn": "中国語", "zh-hk": "中国語", "zh": "中国語"}


# ===================== A. キーワード集計 =====================

def _patterns(words):
    pats = []
    for w in words:
        w = normalize(w)
        if re.fullmatch(r"[a-z0-9 .'\-]+", w):
            pats.append(re.compile(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])"))
        else:
            pats.append(re.compile(re.escape(w)))
    return pats


def keyword_table(t, spots):
    text = (t["title"].fillna("") + " " + t["description"].fillna("").str[:300]).map(normalize)
    rows = []
    for group, items in spots.items():
        for name, words in items.items():
            pats = _patterns(words)
            hit = text.map(lambda s: any(p.search(s) for p in pats))
            dom = t[hit & (t["market"] == "国内")]
            fo = t[hit & (t["market"] == "海外")]
            rows.append({
                "区分": group, "名前": name,
                "国内_本数": len(dom), "海外_本数": len(fo),
                "海外の割合": len(fo) / (len(dom) + len(fo)) if len(dom) + len(fo) else np.nan,
                "国内_再生合計": dom["view_count"].sum(), "海外_再生合計": fo["view_count"].sum(),
                "海外_再生中央値": fo["view_count"].median(),
            })
    return pd.DataFrame(rows)


# ===================== B. 回帰分析 =====================

def prepare(all_data, base_name):
    df = pd.concat(all_data, ignore_index=True)
    df["view_count"] = df["view_count"].fillna(0)
    df["log_views"] = np.log10(df["view_count"] + 1)
    published = pd.to_datetime(df["published_at"], utc=True, errors="coerce")
    now = datetime.now(timezone.utc)
    df["age_days"] = (now - published).dt.days.clip(lower=1)
    df["log_age"] = np.log10(df["age_days"])
    df["formats"] = df["formats"].fillna("")
    for fmt, col in FORMAT_COLS.items():
        df[col] = df["formats"].str.contains(fmt, regex=False).astype(int)
    df["log_subs"] = np.log10(df["subscribers"] + 1) if "subscribers" in df.columns else np.nan
    df["lang_group"] = df["lang_final"].map(LANG_GROUPS).fillna("その他の言語")
    df.loc[df["market"] == "国内", "lang_group"] = "日本語"
    eng = df["like_count"].fillna(np.nan) + df["comment_count"].fillna(0)
    df["eng_rate"] = (eng / df["view_count"]).where(df["view_count"] >= MIN_VIEWS_FOR_RATE)
    df["log_eng"] = np.log10(df["eng_rate"].where(df["eng_rate"] > 0))
    # 地域の基準を base にする
    regions = [base_name] + sorted(r for r in df["region"].unique() if r != base_name)
    df["region"] = pd.Categorical(df["region"], categories=regions)
    return df.dropna(subset=["log_age"])


def lump_themes(df):
    counts = df["primary_theme"].value_counts()
    small = counts[counts < MIN_THEME_N].index
    theme = df["primary_theme"].where(~df["primary_theme"].isin(small), "その他")
    cats = [BASE_THEME] + sorted(c for c in theme.unique() if c != BASE_THEME)
    return pd.Categorical(theme, categories=cats)


def fit(df, target, with_lang, with_subs=False, subs_rows_only=False):
    d = df.dropna(subset=[target]).copy()
    if with_subs or subs_rows_only:
        d = d.dropna(subset=["log_subs"])
    d["region"] = d["region"].cat.remove_unused_categories()
    d["theme"] = lump_themes(d)
    terms = ["C(theme)", "C(region)", "log_age"] + [c for c in FORMAT_COLS.values() if d[c].sum() >= 5]
    if with_subs:
        terms.append("log_subs")
    if with_lang:
        cats = ["英語"] + sorted(c for c in d["lang_group"].unique() if c != "英語")
        d["lang_group"] = pd.Categorical(d["lang_group"], categories=cats)
        terms.append("C(lang_group)")
    model = smf.ols(f"{target} ~ " + " + ".join(terms), data=d).fit(cov_type="HC3")
    return model, d


def readable(name):
    m = re.match(r"C\((\w+)\)\[T\.(.+)\]", name)
    if m:
        label = {"theme": "テーマ", "region": "地域", "lang_group": "言語"}[m.group(1)]
        return f"{label}: {m.group(2)}"
    inv = {v: k for k, v in FORMAT_COLS.items()}
    if name in inv:
        return f"形式: {inv[name]}"
    if name == "log_age":
        return "経過日数（10倍になると）"
    if name == "log_subs":
        return "登録者数（10倍になると）"
    return name


def effect_table(model, d):
    ci = model.conf_int()
    rows = []
    for name in model.params.index:
        if name == "Intercept":
            continue
        n = None
        m = re.match(r"C\((\w+)\)\[T\.(.+)\]", name)
        if m:
            n = int((d[m.group(1)].astype(str) == m.group(2)).sum())
        elif name in FORMAT_COLS.values():
            n = int(d[name].sum())
        p = model.pvalues[name]
        rows.append({
            "要因": readable(name),
            "効果（倍）": 10 ** model.params[name],
            "95%信頼区間": f"{10 ** ci.loc[name, 0]:.2f}〜{10 ** ci.loc[name, 1]:.2f}",
            "p値": p,
            "判定": "**" if p < 0.01 else ("*" if p < 0.05 else ""),
            "本数": n,
        })
    t = pd.DataFrame(rows).sort_values("効果（倍）", ascending=False)
    return t


def print_model(title, model, d, note):
    t = effect_table(model, d)
    shown = t.assign(**{"効果（倍）": t["効果（倍）"].map(lambda x: f"×{x:.2f}"),
                        "p値": t["p値"].map(lambda x: f"{x:.3f}"),
                        "本数": t["本数"].map(lambda x: "" if pd.isna(x) else int(x))})
    print(f"\n=== {title} ===")
    print(f"（n={int(model.nobs)}本、決定係数R²={model.rsquared:.2f}。{note}）")
    print("（効果 = 他の条件をそろえたとき、基準と比べて何倍か。** p<0.01、* p<0.05）")
    print(shown.to_string(index=False))
    return t


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", nargs="+", default=list_regions(), help="分析に使う地域（複数）")
    parser.add_argument("--base", default="choshi", help="キーワード集計をする地域・回帰の基準の地域")
    args = parser.parse_args()

    all_data, names = [], {}
    for slug in args.region:
        region, videos, _, _ = build_themed_videos(slug)
        name = region["name"].split("（")[0]
        names[slug] = name
        t = videos[~videos["is_noise"] & (videos["market"] != "不明")].copy()
        t["region"] = name
        stats_path = data_dir(slug) / "channel_stats.csv"
        if stats_path.exists():
            stats = pd.read_csv(stats_path)[["video_id", "subscribers"]]
            t = t.merge(stats, on="video_id", how="left")
        all_data.append(t)
    base_name = names.get(args.base, list(names.values())[0])

    # ===== A. キーワード集計 =====
    base_cfg = load_region(args.base)
    if base_cfg.get("spots"):
        bt = next(t for t in all_data if t["region"].iloc[0] == base_name)
        kt = keyword_table(bt, base_cfg["spots"])
        print(f"\n=== A. {base_name}のスポット・特産品の名前が出てくる動画（タイトル＋説明文の冒頭） ===")
        for group, g in kt.groupby("区分", sort=False):
            g = g.sort_values("国内_本数", ascending=False)
            shown = g.assign(**{
                "海外の割合": g["海外の割合"].map(lambda x: "-" if pd.isna(x) else f"{x:.0%}"),
                "国内_再生合計": g["国内_再生合計"].map(lambda x: f"{int(x):,}"),
                "海外_再生合計": g["海外_再生合計"].map(lambda x: f"{int(x):,}"),
                "海外_再生中央値": g["海外_再生中央値"].map(lambda x: "-" if pd.isna(x) else f"{int(x):,}"),
            }).drop(columns="区分")
            print(f"\n[{group}]")
            print(shown.to_string(index=False))
    else:
        kt = None
        print(f"\n（regions/{args.base}.json に spots がないため、キーワード集計は飛ばします）")

    # ===== B. 回帰分析 =====
    df = prepare(all_data, base_name)
    results = {}
    for market, with_lang in [("海外", True), ("国内", False)]:
        d = df[df["market"] == market]
        m1, d1 = fit(d, "log_views", with_lang)
        results[f"{market}_再生数"] = print_model(
            f"B-{market}. {market}向け動画の再生数に効く要因", m1, d1,
            f"基準: テーマ「{BASE_THEME}」、地域「{base_name}」" + ("、言語「英語」" if with_lang else ""))
        m2, d2 = fit(d, "log_eng", with_lang)
        results[f"{market}_エンゲージメント率"] = print_model(
            f"B-{market}. {market}向け動画のエンゲージメント率に効く要因", m2, d2,
            f"再生{MIN_VIEWS_FOR_RATE}回以上の動画のみ")

    # ===== C. 頑健性の確認：登録者数をそろえても効果は残るか =====
    if df["log_subs"].notna().sum() > 50:
        for market, with_lang in [("海外", True), ("国内", False)]:
            d = df[df["market"] == market]
            m0, d0 = fit(d, "log_views", with_lang, subs_rows_only=True)
            m1, d1 = fit(d, "log_views", with_lang, with_subs=True)
            t0, t1 = effect_table(m0, d0), effect_table(m1, d1)
            comp = t0[["要因", "効果（倍）", "判定"]].merge(
                t1[["要因", "効果（倍）", "判定", "本数"]], on="要因", how="outer", suffixes=("_なし", "_あり"))
            comp = comp.sort_values("効果（倍）_あり", ascending=False)
            shown = pd.DataFrame({
                "要因": comp["要因"],
                "登録者数なし": comp["効果（倍）_なし"].map(lambda x: "-" if pd.isna(x) else f"×{x:.2f}") + comp["判定_なし"].fillna(""),
                "登録者数あり": comp["効果（倍）_あり"].map(lambda x: "-" if pd.isna(x) else f"×{x:.2f}") + comp["判定_あり"].fillna(""),
                "本数": comp["本数"].map(lambda x: "" if pd.isna(x) else int(x)),
            })
            print(f"\n=== C-{market}. 登録者数をそろえても効果は残るか（{market}向けの再生数） ===")
            print(f"（登録者数が分かる {int(m1.nobs)}本で比較。R²: なし {m0.rsquared:.2f} → あり {m1.rsquared:.2f}。** p<0.01、* p<0.05）")
            print(shown.to_string(index=False))
            results[f"{market}_再生数_登録者数あり"] = t1
    else:
        print("\n（チャンネルの登録者数がないので、頑健性の確認は飛ばします。fetch_channel_stats.py を実行すると追加されます）")

    # ===== 保存 =====
    out_dir = DATA_ROOT / "comparison"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    if kt is not None:
        kt.to_csv(out_dir / f"{stamp}_{args.base}_keywords.csv", index=False, encoding="utf-8-sig")
    for key, t in results.items():
        t.to_csv(out_dir / f"{stamp}_regression_{key}.csv", index=False, encoding="utf-8-sig")
    print(f"\n保存しました: {out_dir}\\{stamp}_*.csv")


if __name__ == "__main__":
    main()
