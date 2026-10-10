"""
Google Trends の検索数（手作業でダウンロードしたCSV）を集計する

目的: YouTubeとは別のデータで、「銚子は海外でどれくらい知られていないか」を確かめる。
     戦略案のKPI「海外からの検索数」の現状値と、その後の推移を測るのにも使う。

使い方:
  1. Google Trends で「銚子市」「鎌倉市」「大洗町」を「トピック」で比較し、
     国（全世界・アメリカ・韓国など）ごとに時系列のCSVをダウンロードする
  2. data/trends/ に置く（ファイル名の time_series_<国コード>_... はそのままにする）
  3. python analyze_search_trends.py

読むときの注意:
  - 値は、その国・期間の中で最も検索された時点を100とした相対値。国をまたいで値を比べてはいけない
  - 「全世界」には日本からの検索も含まれる。海外からの関心は、国別のCSVで見る
  - 0 は「検索がない」ではなく「数えられるほど多くない」
"""

import re
from datetime import datetime

import pandas as pd

from region_config import DATA_ROOT

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 200)

BASE, BENCH = "銚子市", "鎌倉市"


def load(path):
    d = pd.read_csv(path)
    d = d.rename(columns={d.columns[0]: "時期"})
    d["時期"] = pd.to_datetime(d["時期"])
    for c in d.columns[1:]:
        d[c] = pd.to_numeric(d[c].astype(str).str.replace("<1", "0.5"), errors="coerce")
    return d


def main():
    files = sorted((DATA_ROOT / "trends").glob("*time_series_*.csv"))
    if not files:
        raise SystemExit("data/trends/ にGoogle TrendsのCSVがありません。")
    rows = []
    for f in files:
        m = re.search(r"time_series_([A-Za-z]+)_", f.name)
        geo = m.group(1) if m else f.stem
        d = load(f)
        names = list(d.columns[1:])
        last12 = d[d["時期"] > d["時期"].max() - pd.DateOffset(months=12)]
        for name in names:
            rows.append({
                "国": geo, "地域": name,
                "5年平均": d[name].mean(),
                "〜2022年": d.loc[d["時期"] < "2023-01-01", name].mean(),
                "2023年〜": d.loc[d["時期"] >= "2023-01-01", name].mean(),
                "直近12か月": last12[name].mean(),
                "数えられない月": int((d[name] == 0).sum()),
                "月数": len(d),
            })
    res = pd.DataFrame(rows)
    # 鎌倉に対する比（同じ国・同じ期間の中なら比べられる）
    bench = res[res["地域"] == BENCH].set_index("国")["直近12か月"]
    res["鎌倉に対する比（直近12か月）"] = res.apply(
        lambda r: r["直近12か月"] / bench.get(r["国"]) if bench.get(r["国"]) else float("nan"), axis=1)

    shown = res.copy()
    for c in ["5年平均", "〜2022年", "2023年〜", "直近12か月"]:
        shown[c] = shown[c].map(lambda x: f"{x:.1f}")
    shown["鎌倉に対する比（直近12か月）"] = shown["鎌倉に対する比（直近12か月）"].map(lambda x: f"{x:.0%}")
    shown["数えられない月"] = shown["数えられない月"].astype(str) + "/" + shown["月数"].astype(str)
    print("\n=== Google Trends：海外からの検索の多さ（国ごとの相対値。国をまたいで比べない） ===")
    print(shown.drop(columns="月数").to_string(index=False))

    out = DATA_ROOT / "comparison" / f"{datetime.now().strftime('%Y%m%d_%H%M')}_search_trends.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n保存しました: {out}")


if __name__ == "__main__":
    main()
