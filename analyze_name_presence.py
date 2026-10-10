"""
動画のタイトルに「町の名前」が入っているかを数える

目的: 海外で最も見られた銚子の動画「Day in the Life of a Japanese Train Conductor」のように、
     物語は広まっても、タイトルに町の名前がなければ、見た人は銚子という町を覚えない。
     そこで、海外向けの動画のうち、タイトルに町の名前がある割合を測る（戦略案のKPIの1つ）。

使い方:
  python analyze_name_presence.py                # regions/ にある全地域
  python analyze_name_presence.py --base choshi  # 詳しく見る地域（上位動画の一覧を表示）

町の名前の表記は regions/<地域>.json の "name_terms" に書く
（例: 銚子 / 铫子 / Choshi / 쵸시 / 조시）。説明文やハッシュタグではなく、タイトルだけを見る。
前提: 各地域でステップ1〜3を実行済み。APIは使わない。
"""

import argparse
import re
from datetime import datetime

import pandas as pd

from classify_themes import build_themed_videos
from region_config import DATA_ROOT, list_regions, load_region
from theme_rules import normalize

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 200)


def name_patterns(region_cfg):
    """町の名前の表記を正規表現にする（英字は単語の区切りで判定）"""
    terms = region_cfg.get("name_terms") or [region_cfg["name"].split("（")[0]]
    pats = []
    for t in terms:
        t = normalize(t)
        if re.fullmatch(r"[a-zāēīōū]+", t):
            pats.append(re.compile(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])"))
        else:
            pats.append(re.compile(re.escape(t)))
    return pats


def has_name(titles, pats):
    """タイトルの列に対して、町の名前があるかどうか（True/False）を返す"""
    return titles.fillna("").map(lambda s: any(p.search(normalize(s)) for p in pats))


def summarize(t, pats):
    rows = {}
    for market in ["海外", "国内"]:
        d = t[t["market"] == market]
        named = has_name(d["title"], pats)
        views = d["view_count"].fillna(0)
        top10 = d.nlargest(10, "view_count")
        rows[market] = {
            "本数": len(d),
            "名前あり_本数": int(named.sum()),
            "名前あり_割合": named.mean() if len(d) else float("nan"),
            "名前ありの再生シェア": views[named].sum() / views.sum() if views.sum() else float("nan"),
            "上位10本のうち名前あり": int(has_name(top10["title"], pats).sum()),
        }
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", nargs="+", default=list_regions())
    parser.add_argument("--base", default="choshi")
    args = parser.parse_args()

    results, detail = [], None
    for slug in args.region:
        region, videos, _, _ = build_themed_videos(slug)
        name = region["name"].split("（")[0]
        t = videos[~videos["is_noise"] & (videos["market"] != "不明")].copy()
        pats = name_patterns(load_region(slug))
        for market, r in summarize(t, pats).items():
            results.append({"地域": name, "市場": market, **r})
        if slug == args.base:
            fo = t[t["market"] == "海外"].nlargest(15, "view_count").copy()
            fo["名前"] = has_name(fo["title"], pats).map({True: "あり", False: "なし"})
            detail = (name, fo)

    res = pd.DataFrame(results)
    shown = res.assign(**{
        "名前あり_割合": res["名前あり_割合"].map(lambda x: "-" if pd.isna(x) else f"{x:.0%}"),
        "名前ありの再生シェア": res["名前ありの再生シェア"].map(lambda x: "-" if pd.isna(x) else f"{x:.0%}"),
    })
    print("\n=== タイトルに町の名前がある動画（観光関連、言語不明を除く） ===")
    print("（名前ありの再生シェア: その市場の再生のうち、タイトルに町の名前がある動画が占める割合）")
    print(shown.to_string(index=False))

    if detail:
        name, fo = detail
        print(f"\n=== {name}の海外向け動画 再生数トップ15：タイトルに町の名前があるか ===")
        for _, r in fo.iterrows():
            v = int(r["view_count"]) if pd.notna(r["view_count"]) else 0
            print(f"  [{r['名前']}] {v:>10,}回 | {r['lang_final']:<5} | {str(r['title'])[:55]} | {str(r['channel_title'])[:18]}")

    out_dir = DATA_ROOT / "comparison"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{datetime.now().strftime('%Y%m%d_%H%M')}_name_presence.csv"
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n保存しました: {out}")


if __name__ == "__main__":
    main()
