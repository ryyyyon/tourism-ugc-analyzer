"""
ステップ5: 投稿の年別トレンドと、アカウント（チャンネル）単位の分析

使い方:
  python analyze_trends_accounts.py                  # regions/ にある全地域
  python analyze_trends_accounts.py --base choshi    # 詳しく見る地域（チャンネル一覧などを表示）

前提: 各地域でステップ1〜3を実行済み。APIは使わない。

読むときの注意:
  - 検索結果は、キーワードごとに関連度の高い上位100件までの「サンプル」。全投稿の推移ではない
  - 古い動画ほど再生数がたまっているので、年をまたいだ再生数の比較はせず、本数と割合で見る
  - 今年（2026年）は途中までの数字
"""

import argparse
from datetime import datetime

import pandas as pd

from classify_themes import build_themed_videos
from region_config import DATA_ROOT, list_regions

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 220)

START_YEAR = 2015
# チャンネル名にこれらが入っていれば「公式系」とみなす（結果の一覧を見て人が確認する前提）
OFFICIAL_WORDS = ["観光協会", "公式", "official", "市役所", "町役場", "観光課", "観光局", "観光連盟",
                  "観光推進", "tourism"]
PERIODS = [(None, 2018, "〜2018"), (2019, 2020, "2019-20"), (2021, 2022, "2021-22"),
           (2023, 2024, "2023-24"), (2025, None, "2025-26")]


def period_of(year):
    for lo, hi, label in PERIODS:
        if (lo is None or year >= lo) and (hi is None or year <= hi):
            return label
    return None


def load(slug):
    region, videos, _, _ = build_themed_videos(slug)
    t = videos[~videos["is_noise"] & (videos["market"] != "不明")].copy()
    t["view_count"] = t["view_count"].fillna(0)
    t["year"] = pd.to_datetime(t["published_at"], utc=True, errors="coerce").dt.year
    t = t.dropna(subset=["year"])
    t["year"] = t["year"].astype(int)
    t["period"] = t["year"].map(period_of)
    t["channel_title"] = t["channel_title"].fillna("（不明）")
    return region["name"].split("（")[0], t


def channel_table(t):
    """チャンネル単位の集計"""
    g = t.groupby("channel_title")
    table = pd.DataFrame({
        "本数": g["video_id"].count(),
        "合計再生": g["view_count"].sum(),
        "再生中央値": g["view_count"].median(),
        "主な言語": g["lang_final"].agg(lambda s: s.mode().iat[0]),
        "主なテーマ": g["primary_theme"].agg(lambda s: s.mode().iat[0]),
    })
    top_idx = g["view_count"].idxmax()
    table["代表動画"] = t.loc[top_idx.values, "title"].str[:40].values
    table["公式系"] = [any(w.lower() in name.lower() for w in OFFICIAL_WORDS) for name in table.index]
    return table.sort_values("合計再生", ascending=False)


def fmt_int(x):
    return "-" if pd.isna(x) else f"{int(x):,}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", nargs="+", default=list_regions(), help="比較する地域（複数）")
    parser.add_argument("--base", default="choshi", help="詳しく見る地域")
    args = parser.parse_args()

    names, data = {}, {}
    for slug in args.region:
        names[slug], data[slug] = load(slug)
    cols = [names[s] for s in args.region]
    this_year = datetime.now().year
    years = list(range(START_YEAR, this_year + 1))

    # ===== 1. 年別トレンド =====
    total = pd.DataFrame({names[s]: data[s]["year"].value_counts() for s in args.region}).reindex(years).fillna(0).astype(int)
    foreign = pd.DataFrame({names[s]: data[s].loc[data[s]["market"] == "海外", "year"].value_counts()
                            for s in args.region}).reindex(years).fillna(0).astype(int)
    share = foreign / total.where(total > 0)
    trend = pd.concat({"全体の本数": total, "海外向けの本数": foreign,
                       "海外向けの割合": share.map(lambda x: "-" if pd.isna(x) else f"{x:.0%}")}, axis=1)
    print("\n=== 1. 投稿年ごとの本数と海外向けの割合（観光関連の動画） ===")
    print(f"（検索上位のサンプルでの推移。{this_year}年は途中まで）")
    print(trend.to_string())

    # ===== 2. 基準地域：海外向けのテーマ別の推移 =====
    base = args.base
    if base in data:
        fo = data[base][data[base]["market"] == "海外"]
        labels = [p[2] for p in PERIODS]
        theme_trend = pd.crosstab(fo["primary_theme"], fo["period"]).reindex(columns=labels, fill_value=0)
        theme_trend = theme_trend.loc[theme_trend.sum(axis=1).sort_values(ascending=False).index]
        print(f"\n=== 2. {names[base]}の海外向け動画：テーマ別の本数の推移 ===")
        print(theme_trend.to_string())

    # ===== 3. チャンネルへの依存度（地域比較） =====
    rows = {}
    for s in args.region:
        fo = data[s][data[s]["market"] == "海外"]
        ch = channel_table(fo) if len(fo) else pd.DataFrame()
        fo_views = fo["view_count"].sum()
        rows[names[s]] = {
            "海外向けのチャンネル数": len(ch),
            "上位1チャンネルの再生シェア": ch["合計再生"].iloc[:1].sum() / fo_views if fo_views else 0,
            "上位3チャンネルの再生シェア": ch["合計再生"].iloc[:3].sum() / fo_views if fo_views else 0,
            "最大のチャンネル": ch.index[0] if len(ch) else "-",
        }
    dep = pd.DataFrame(rows)
    shown = dep.copy()
    for r in ["上位1チャンネルの再生シェア", "上位3チャンネルの再生シェア"]:
        shown.loc[r] = dep.loc[r].map(lambda x: f"{x:.0%}")
    print("\n=== 3. 海外の再生がどれだけ一部のチャンネルに依存しているか ===")
    print(shown.to_string())

    # ===== 4. 基準地域：人気チャンネル =====
    if base in data:
        for market in ["海外", "国内"]:
            d = data[base][data[base]["market"] == market]
            ch = channel_table(d).head(10)
            show = ch.assign(合計再生=ch["合計再生"].map(fmt_int), 再生中央値=ch["再生中央値"].map(fmt_int))
            print(f"\n=== 4. {names[base]}の{market}向け 人気チャンネル トップ10（合計再生順） ===")
            print(show.drop(columns=["公式系"]).to_string())

    # ===== 5. 公式系チャンネル =====
    print("\n=== 5. 公式系のチャンネル（チャンネル名で判定。人の目で確認すること） ===")
    for s in args.region:
        ch = channel_table(data[s])
        off = ch[ch["公式系"]]
        dom_median = data[s].loc[data[s]["market"] == "国内", "view_count"].median()
        print(f"\n[{names[s]}] 公式系 {len(off)}チャンネル（参考: 国内向け動画全体の再生中央値 {fmt_int(dom_median)}回）")
        if off.empty:
            print("  なし")
        for name, r in off.iterrows():
            print(f"  {name[:30]} | {r['本数']}本 | 合計 {fmt_int(r['合計再生'])}回 | 中央値 {fmt_int(r['再生中央値'])}回"
                  f" | 主な言語 {r['主な言語']} | {r['代表動画']}")

    # ===== 保存 =====
    out_dir = DATA_ROOT / "comparison"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    total.to_csv(out_dir / f"{stamp}_yearly_total.csv", encoding="utf-8-sig")
    foreign.to_csv(out_dir / f"{stamp}_yearly_foreign.csv", encoding="utf-8-sig")
    dep.to_csv(out_dir / f"{stamp}_channel_dependence.csv", encoding="utf-8-sig")
    if base in data:
        channel_table(data[base]).to_csv(out_dir / f"{stamp}_{base}_channels.csv", encoding="utf-8-sig")
    print(f"\n保存しました: {out_dir}\\{stamp}_*.csv")


if __name__ == "__main__":
    main()
