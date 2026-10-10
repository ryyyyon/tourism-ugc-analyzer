"""
戦略の根拠にしている数字が、集め方や少ない本数に左右されていないかを確かめる（APIは使わない）

使い方:
  python check_robustness.py --region narita

確かめること:
  1. 空港の割合の偏り
     空港・乗り継ぎのキーワード（"airport", "layover" などを含むもの）だけで見つかった動画を除いて、
     海外の再生のテーマ別の割合を計算し直す。空港と関係ない旅行のキーワードで見つかった動画だけでも見る
  2. 少ない本数の中身
     スポット（regions/<地域>.json の spots）のうち --spots で指定したものについて、
     海外向けの動画を1本ずつ表示し、名前がタイトルにあるか、説明文だけかを確かめる
  3. 乗り継ぎの動画が町に触れているか
     テーマ「乗り継ぎ・待ち時間」の海外向けの動画のうち、町の言葉（--town-words）に触れている本数
"""

import argparse
import re

import pandas as pd

from analyze_engagement_keywords import _patterns
from classify_themes import build_themed_videos
from region_config import load_region
from theme_rules import normalize

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 200)

AIRPORT_KEYWORD_CUES = ["airport", "layover", "空港", "乗り継ぎ", "haneda", "羽田"]
DEFAULT_SPOTS = ["さくらの山・空の駅さくら館", "御護摩・写経"]
DEFAULT_TOWN = ["成田山", "新勝寺", "naritasan", "narita-san", "shinshoji", "表参道", "omotesando", "参道",
                "うなぎ", "鰻", "unagi", "eel", "temple", "寺", "town", "downtown", "city center", "さくらの山",
                "sakura no yama", "street food", "food tour", "나리타산", "장어", "วัด", "ปลาไหล"]


def share_table(fo):
    total = fo["view_count"].sum()
    s = fo.groupby("primary_theme")["view_count"].sum() / total if total else pd.Series(dtype=float)
    return s.sort_values(ascending=False)


def summarize_shares(label, fo):
    s = share_table(fo)
    airport = s.get("空港", 0)
    layover = s.get("乗り継ぎ・待ち時間", 0)
    print(f"  {label}：{len(fo)}本 | 空港 {airport:.0%} | 乗り継ぎ {layover:.0%} | 空港まわり計 {airport + layover:.0%}"
          f" | それ以外（町など）{1 - airport - layover:.0%}")
    return s


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="narita")
    parser.add_argument("--spots", nargs="*", default=DEFAULT_SPOTS)
    parser.add_argument("--town-words", nargs="*", default=DEFAULT_TOWN)
    args = parser.parse_args()

    region, videos, _, _ = build_themed_videos(args.region)
    name = region["name"].split("（")[0]
    t = videos[~videos["is_noise"] & (videos["market"] != "不明")].copy()
    t["view_count"] = t["view_count"].fillna(0)
    t["kw"] = t["keywords"].fillna("").map(lambda s: [k for k in s.split("|") if k])
    is_airport_kw = lambda k: any(c in k.lower() for c in AIRPORT_KEYWORD_CUES)
    t["only_airport_kw"] = t["kw"].map(lambda ks: bool(ks) and all(is_airport_kw(k) for k in ks))
    t["any_neutral_kw"] = t["kw"].map(lambda ks: any(not is_airport_kw(k) for k in ks))
    fo = t[t["market"] == "海外"]

    # ===== 1. 空港の割合の偏り =====
    airport_kws = sorted({k for ks in t["kw"] for k in ks if is_airport_kw(k)})
    print(f"\n=== 1. {name}：空港の割合は、集め方の偏りで大きくなっていないか ===")
    print(f"（空港・乗り継ぎのキーワード: {', '.join(airport_kws) or 'なし'}）")
    summarize_shares("すべての海外向けの動画", fo)
    summarize_shares("空港のキーワード「だけ」で見つかった動画を除く", fo[~fo["only_airport_kw"]])
    print("\n  キーワード別（そのキーワードで見つかった海外向けの動画の中での、空港の動画の割合）")
    rows = []
    for k in sorted({k for ks in fo["kw"] for k in ks}):
        d = fo[fo["kw"].map(lambda ks: k in ks)]
        s = share_table(d)
        n_air = int((d["primary_theme"] == "空港").sum())
        rows.append({"キーワード": k, "空港のキーワードか": "はい" if is_airport_kw(k) else "",
                     "本数": len(d), "うち空港の動画": n_air,
                     "空港の本数の割合": f"{n_air / len(d):.0%}" if len(d) else "-",
                     "空港の再生の割合": f"{s.get('空港', 0):.0%}"})
    print(pd.DataFrame(rows).to_string(index=False))

    # ===== 2. 少ない本数の中身 =====
    spots = {}
    for group in load_region(args.region).get("spots", {}).values():
        spots.update(group)
    print(f"\n=== 2. 少ない本数の中身（海外向けの動画を1本ずつ） ===")
    for spot in args.spots:
        words = spots.get(spot)
        if not words:
            print(f"\n[{spot}] 設定ファイルに見つかりません")
            continue
        pats = _patterns(words)
        title_hit = fo["title"].fillna("").map(lambda s: any(p.search(normalize(s)) for p in pats))
        desc_hit = fo["description"].fillna("").str[:300].map(lambda s: any(p.search(normalize(s)) for p in pats))
        d = fo[title_hit | desc_hit].assign(場所=lambda x: title_hit[x.index].map({True: "タイトル", False: "説明文だけ"}))
        print(f"\n[{spot}] {len(d)}本（タイトルにある {int(title_hit[d.index].sum())}本 / 説明文だけ {int((~title_hit[d.index]).sum())}本）")
        for _, r in d.sort_values("view_count", ascending=False).iterrows():
            print(f"  {int(r['view_count']):>9,}回 | {r['場所']:<5} | テーマ {r['primary_theme']:<8} | {str(r['title'])[:55]}")

    # ===== 3. 乗り継ぎの動画は町に触れているか =====
    lay = fo[fo["primary_theme"] == "乗り継ぎ・待ち時間"].copy()
    pats = _patterns(args.town_words)
    text = (lay["title"].fillna("") + " " + lay["description"].fillna("").str[:300]).map(normalize)
    lay["町に触れる"] = text.map(lambda s: any(p.search(s) for p in pats))
    n, k = len(lay), int(lay["町に触れる"].sum())
    print(f"\n=== 3. 乗り継ぎの動画は、町に触れているか ===")
    if n:
        v_all, v_town = lay["view_count"].sum(), lay.loc[lay["町に触れる"], "view_count"].sum()
        print(f"  海外向けの乗り継ぎの動画 {n}本のうち、町に触れているもの {k}本（{k / n:.0%}）"
              f"、再生では {v_town / v_all:.0%}" if v_all else "")
        print(f"  町に触れている動画の再生中央値 {lay.loc[lay['町に触れる'], 'view_count'].median():,.0f}回 ／ "
              f"触れていない動画 {lay.loc[~lay['町に触れる'], 'view_count'].median():,.0f}回")
        print("\n  再生数の上位10本")
        for _, r in lay.sort_values("view_count", ascending=False).head(10).iterrows():
            print(f"  {int(r['view_count']):>9,}回 | {'町あり' if r['町に触れる'] else '町なし'} | {str(r['title'])[:60]}")
    else:
        print("  乗り継ぎの動画がありません")


if __name__ == "__main__":
    main()
