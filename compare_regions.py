"""
ステップ4: 複数の地域を並べて比較する

使い方:
  python compare_regions.py                                  # regions/ にある全地域
  python compare_regions.py --region choshi oarai            # 地域を指定
  python compare_regions.py --base choshi                    # 基準の地域（「足りないもの」を探す対象）

前提: 各地域でステップ1〜3（run_pipeline.py）を実行済み。APIは使わない。

比較する指標:
  - 海外向けの割合、再生数の中央値
  - 海外の再生数がどのテーマに集中しているか（HHI: ハーフィンダール・ハーシュマン指数）
      各テーマの再生シェアの2乗の合計。1つのテーマに100%集中なら1.0、分散しているほど0に近い
      ※「未分類」「旅行全般」は除いて計算する
  - 海外向けの言語構成
  - 動画の形式（散歩・車窓などの言語不要な動画）の再生シェア
"""

import argparse
from datetime import datetime

import pandas as pd

from classify_themes import build_themed_videos
from region_config import DATA_ROOT, list_regions

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 220)

EXCLUDE_FROM_CONCENTRATION = ["未分類", "旅行全般"]
LANGUAGE_FREE = "散歩・車窓（言語不要）"


def view_share(df, col):
    total = df["view_count"].sum()
    if not total:
        return pd.Series(dtype=float)
    return df.groupby(col)["view_count"].sum() / total


def summarize(slug):
    region, videos, _, _ = build_themed_videos(slug)
    tourism = videos[~videos["is_noise"] & (videos["market"] != "不明")].copy()
    tourism["formats"] = tourism["formats"].fillna("")
    dom = tourism[tourism["market"] == "国内"]
    fo = tourism[tourism["market"] == "海外"]

    shares = view_share(fo, "primary_theme")
    named = shares.drop(EXCLUDE_FROM_CONCENTRATION, errors="ignore")
    named = named / named.sum() if named.sum() else named
    top_theme = named.idxmax() if len(named) else "-"
    top_share = named.max() if len(named) else 0
    hhi = float((named ** 2).sum()) if len(named) else 0

    langs = fo["lang_final"].value_counts(normalize=True).head(3)
    lf = fo[fo["formats"].str.contains(LANGUAGE_FREE, regex=False)]
    fo_views = fo["view_count"].sum()

    summary = {
        "観光関連の動画": len(tourism),
        "海外向けの本数": len(fo),
        "海外向けの割合": len(fo) / len(tourism) if len(tourism) else 0,
        "海外_再生中央値": fo["view_count"].median(),
        "国内_再生中央値": dom["view_count"].median(),
        "海外_最大テーマ": top_theme,
        "最大テーマの再生シェア": top_share,
        "集中度HHI": hhi,
        "海外の主な言語": ", ".join(f"{k} {v:.0%}" for k, v in langs.items()),
        "言語不要動画_本数": len(lf),
        "言語不要動画_再生シェア": lf["view_count"].sum() / fo_views if fo_views else 0,
        "言語不要動画_再生中央値": lf["view_count"].median(),
    }
    return region, summary, tourism


def pct(x):
    return "-" if pd.isna(x) else f"{x:.0%}"


def num(x):
    return "-" if pd.isna(x) else f"{int(x):,}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", nargs="+", default=list_regions(), help="比較する地域（複数）")
    parser.add_argument("--base", default="choshi", help="基準にする地域")
    args = parser.parse_args()

    names, summaries, tourisms = {}, {}, {}
    for slug in args.region:
        region, summary, tourism = summarize(slug)
        names[slug], summaries[slug], tourisms[slug] = region["name"].split("（")[0], summary, tourism
    cols = [names[s] for s in args.region]

    # ===== 1. 概要 =====
    overview = pd.DataFrame({names[s]: summaries[s] for s in args.region})
    shown = overview.copy()
    for row in ["海外向けの割合", "最大テーマの再生シェア", "言語不要動画_再生シェア"]:
        shown.loc[row] = shown.loc[row].map(pct)
    for row in ["海外_再生中央値", "国内_再生中央値", "言語不要動画_再生中央値"]:
        shown.loc[row] = shown.loc[row].map(num)
    shown.loc["集中度HHI"] = overview.loc["集中度HHI"].map(lambda x: f"{x:.2f}")
    print("\n=== 1. 地域の概要（観光関連の動画、言語不明を除く） ===")
    print("（集中度HHI: 海外の再生数が1つのテーマに集中しているほど1.0に近い。未分類・旅行全般は除く）")
    print(shown.to_string())

    # ===== 2. テーマ別の再生構成比 =====
    def theme_table(market):
        t = pd.DataFrame({names[s]: view_share(tourisms[s][tourisms[s]["market"] == market], "primary_theme")
                          for s in args.region}).fillna(0)
        return t.loc[t.max(axis=1).sort_values(ascending=False).index]

    fo_theme = theme_table("海外")
    print("\n=== 2. 海外向けの再生構成比（テーマ別） ===")
    print(fo_theme.map(pct).to_string())

    dom_theme = theme_table("国内")
    print("\n=== 3. 国内向けの再生構成比（テーマ別） ===")
    print(dom_theme.map(pct).to_string())

    # ===== 4. 海外向けの本数（テーマ別） =====
    fo_count = pd.DataFrame({names[s]: tourisms[s][tourisms[s]["market"] == "海外"]["primary_theme"].value_counts()
                             for s in args.region}).fillna(0).astype(int)
    fo_count = fo_count.loc[fo_count.sum(axis=1).sort_values(ascending=False).index]
    print("\n=== 4. 海外向けの本数（テーマ別） ===")
    print(fo_count.to_string())

    # ===== 5. 動画の形式 =====
    rows = []
    for s in args.region:
        fo = tourisms[s][tourisms[s]["market"] == "海外"]
        total = fo["view_count"].sum()
        for fmt in ["散歩・車窓（言語不要）", "ショート動画", "Vlog", "ガイド・おすすめ"]:
            d = fo[fo["formats"].str.contains(fmt, regex=False)]
            rows.append({"地域": names[s], "形式": fmt, "本数": len(d),
                         "再生シェア": d["view_count"].sum() / total if total else 0,
                         "再生中央値": d["view_count"].median()})
    fmt_df = pd.DataFrame(rows)
    fmt_show = fmt_df.assign(再生シェア=fmt_df["再生シェア"].map(pct), 再生中央値=fmt_df["再生中央値"].map(num))
    print("\n=== 5. 海外向け動画の形式（1本に複数つくことがある） ===")
    print(fmt_show.pivot(index="形式", columns="地域", values=["本数", "再生シェア", "再生中央値"])
          .reindex(columns=cols, level=1).to_string())

    # ===== 6. 海外向けの言語構成 =====
    lang = pd.DataFrame({names[s]: tourisms[s][tourisms[s]["market"] == "海外"]["lang_final"].value_counts()
                         for s in args.region}).fillna(0).astype(int)
    lang = lang.loc[lang.sum(axis=1).sort_values(ascending=False).index].head(8)
    print("\n=== 6. 海外向けの言語（本数、上位8言語） ===")
    print(lang.to_string())

    # ===== 7. 基準の地域に足りないもの =====
    if args.base in names:
        base = names[args.base]
        print(f"\n=== 7. {base}に足りないもの（他の地域では海外の再生の10%以上を占めるのに、{base}では2%未満のテーマ） ===")
        found = False
        for theme, row in fo_theme.iterrows():
            if theme in EXCLUDE_FROM_CONCENTRATION:
                continue
            others = {c: row[c] for c in cols if c != base and row[c] >= 0.10}
            if others and row[base] < 0.02:
                found = True
                detail = "、".join(f"{c} {v:.0%}" for c, v in others.items())
                print(f"  {theme}: {base} {row[base]:.0%} ／ {detail}")
        if not found:
            print("  なし")

    # ===== 保存 =====
    out_dir = DATA_ROOT / "comparison"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    overview.to_csv(out_dir / f"{stamp}_overview.csv", encoding="utf-8-sig")
    fo_theme.to_csv(out_dir / f"{stamp}_foreign_theme_share.csv", encoding="utf-8-sig")
    dom_theme.to_csv(out_dir / f"{stamp}_domestic_theme_share.csv", encoding="utf-8-sig")
    fmt_df.to_csv(out_dir / f"{stamp}_formats.csv", index=False, encoding="utf-8-sig")
    print(f"\n保存しました: {out_dir}\\{stamp}_*.csv")


if __name__ == "__main__":
    main()
