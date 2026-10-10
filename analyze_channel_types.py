"""
競合のプロモーション手法：誰が発信しているか（公式・企業・メディア・個人の作り手）を地域ごとに比べる

使い方:
  python analyze_channel_types.py                                   # データのある全地域
  python analyze_channel_types.py --region narita kawagoe sawara    # 地域を指定
  python analyze_channel_types.py --region narita --show 公式        # その種類のチャンネル一覧も出す（判定の確認）

前提: 各地域でステップ1〜3を実行済み。APIは使わない。

【分け方】チャンネル名で4つに分ける（上から順に当てはめる）
  公式       : 自治体・観光協会・観光局など（「観光協会」「市公式」「Visit ○○」「tourism board」など）
  メディア   : テレビ・新聞・ニュース（「テレビ」「NHK」「news」など）
  企業・施設 : 空港・鉄道・航空・ホテル・寺社など（「空港」「電鉄」「airlines」「hotel」、名前が「寺」「神社」で終わるなど）
  個人の作り手: 上のどれにも当てはまらないチャンネル
  チャンネル名だけで判定するので、間違いもある。地域の設定ファイルに
    "channel_types": {"チャンネル名": "公式"}
  と書くと、そのチャンネルの種類を指定できる（判定の結果は --show で確かめる）

【見るもの】
  - 種類ごとの本数・チャンネル数・海外向けの再生のシェア
  - 公式の発信が、検索の上位にどれくらい出てくるか（出てこないこと自体が「届いていない」という結果）

読むときの注意:
  - 検索上位のサンプルなので、公式チャンネルが動画を出していても、検索の上位に出てこなければ数に入らない
"""

import argparse
import re

import pandas as pd

from region_config import latest, list_regions
from theme_rules import normalize

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 200)

TYPES = ["公式", "メディア", "企業・施設", "個人の作り手"]
FACILITY_NAME = re.compile(r"(寺|神社|神宮|大師|水族館|博物館|美術館)$")  # 寺社や施設そのもののチャンネル

RULES = {
    "公式": [
        "観光協会", "観光局", "観光課", "観光推進", "観光連盟", "観光振興", "観光物産", "観光コンベンション",
        "観光ナビ", "市役所", "町役場", "村役場", "県庁", "都庁", "府庁", "市公式", "町公式", "村公式", "県公式",
        "シティプロモーション", "広報", "観光公式",
        "tourism board", "tourism bureau", "tourism association", "tourism organization", "tourism office",
        "tourist association", "tourist information", "city tourism", "city of", "city office", "city official",
        "prefecture", "jnto", "japan national tourism", "convention", "관광공사", "관광청",
    ],
    "メディア": [
        "テレビ", "ニュース", "新聞", "放送", "ラジオ", "日テレ", "テレ東", "テレ朝", "フジテレビ", "チバテレ",
        "tv", "news*", "nhk", "ann", "tbs", "fnn", "jnn", "nnn", "cna", "reuters", "bbc", "cnn", "nippon.com",
        "japan times", "kbs", "sbs", "mbc", "jtbc", "ytn", "tvbs", "東森", "三立", "中天", "thai pbs", "ch3", "ch7",
    ],
    "企業・施設": [
        "空港", "鉄道", "電鉄", "航空", "ホテル", "旅館", "株式会社", "商店", "本舗", "公式チャンネル",
        "airport", "airlines", "airline", "airways", "railway", "railways", "hotel", "hotels", "resort",
        "inc", "co.", "ltd", "corp", "keisei", "keikyu", "jr east", "narita international airport",
        "ana official", "japan airlines", "all nippon airways",
    ],
}
# 「Visit Chiba」のような「Visit＋地名」は公式（観光局・DMO）。「Visit Japan」は個人のチャンネル名にもよく使われるので除く
VISIT_PLACE = re.compile(r"(?<![a-z])visit (?!japan)[a-z]+")
# 「○○市」「○○町」だけの名前、「○○市公式」「○○市チャンネル」なども公式とみなす
PUBLIC_NAME = re.compile(r"^.{1,6}[市町村県]$|[市県](公式|役所|チャンネル|広報|pr)|[町村](公式|役場|広報)")
_ASCII = re.compile(r"^[a-z0-9 .&'\-]+$")


def _compile(words):
    pats = []
    for w in words:
        w = normalize(w)
        if w.endswith("*"):  # 末尾の * は、単語の途中でも一致させる（annnewsch の news など）
            pats.append(re.compile(re.escape(w[:-1])))
        elif _ASCII.match(w):
            pats.append(re.compile(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])"))
        else:
            pats.append(re.compile(re.escape(w)))
    return pats


_PATS = {k: _compile(v) for k, v in RULES.items()}


def classify_channel(title, overrides=None):
    """チャンネル名から、公式・メディア・企業・個人の作り手のどれかを返す"""
    if overrides and title in overrides:
        return overrides[title]
    t = normalize(title)
    if PUBLIC_NAME.search(t) or VISIT_PLACE.search(t) or any(p.search(t) for p in _PATS["公式"]):
        return "公式"
    if any(p.search(t) for p in _PATS["メディア"]):
        return "メディア"
    if FACILITY_NAME.search(t) or any(p.search(t) for p in _PATS["企業・施設"]):
        return "企業・施設"
    return "個人の作り手"


def add_types(tourism, cfg):
    t = tourism.copy()
    overrides = cfg.get("channel_types", {})
    t["発信者"] = t["channel_title"].fillna("").map(lambda x: classify_channel(x, overrides))
    return t


def type_summary(tourism, cfg, market="海外"):
    """種類ごとの本数・チャンネル数・再生のシェア"""
    t = add_types(tourism, cfg)
    if market:
        t = t[t["market"] == market]
    total = t["view_count"].fillna(0).sum()
    rows = []
    for kind in TYPES:
        d = t[t["発信者"] == kind]
        rows.append({
            "発信者": kind,
            "本数": len(d),
            "チャンネル数": d["channel_title"].nunique(),
            "再生のシェア": d["view_count"].fillna(0).sum() / total if total else 0.0,
            "再生中央値": d["view_count"].median(),
        })
    return pd.DataFrame(rows)


def region_overview(data, market="海外"):
    """地域ごとに、公式・メディア・企業・個人の再生のシェアと、公式の本数を並べる
    data: {地域名: (tourism, cfg)}"""
    rows = []
    for name, (tourism, cfg) in data.items():
        s = type_summary(tourism, cfg, market).set_index("発信者")
        row = {"地域": name, "公式の本数": int(s.loc["公式", "本数"]),
               "公式のチャンネル数": int(s.loc["公式", "チャンネル数"])}
        for kind in TYPES:
            row[f"{kind}_再生シェア"] = s.loc[kind, "再生のシェア"]
        rows.append(row)
    return pd.DataFrame(rows)


def channel_list(tourism, cfg, kind="公式", market=None):
    """その種類のチャンネルの一覧（本数と再生の合計）"""
    t = add_types(tourism, cfg)
    t = t[t["発信者"] == kind]
    if market:
        t = t[t["market"] == market]
    if t.empty:
        return pd.DataFrame(columns=["チャンネル", "本数", "海外向けの本数", "再生合計"])
    g = t.groupby("channel_title")
    out = pd.DataFrame({
        "本数": g.size(),
        "海外向けの本数": g["market"].apply(lambda m: int((m == "海外").sum())),
        "再生合計": g["view_count"].sum(),
    }).reset_index().rename(columns={"channel_title": "チャンネル"})
    return out.sort_values("再生合計", ascending=False).reset_index(drop=True)


def main():
    from compare_regions import summarize
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", nargs="+", default=None, help="地域（省略するとデータのある全地域）")
    parser.add_argument("--market", default="海外", choices=["海外", "国内", "すべて"])
    parser.add_argument("--show", choices=TYPES, help="この種類のチャンネル一覧を地域ごとに出す")
    args = parser.parse_args()
    regions = args.region or [s for s in list_regions() if latest(s, "videos_check_*.csv") is not None]
    market = None if args.market == "すべて" else args.market

    data = {}
    for slug in regions:
        cfg, _, tourism = summarize(slug)
        data[cfg["name"].split("（")[0]] = (tourism, cfg)

    ov = region_overview(data, market)
    shown = ov.copy()
    for kind in TYPES:
        shown[f"{kind}_再生シェア"] = shown[f"{kind}_再生シェア"].map(lambda x: f"{x:.0%}")
    print(f"\n=== 発信者の種類（{args.market}向けの動画） ===")
    print("（チャンネル名で判定。公式＝自治体・観光協会など、企業・施設＝空港・鉄道・ホテル・寺社など）")
    print(shown.to_string(index=False))

    kind = args.show or "公式"
    for name, (tourism, cfg) in data.items():
        cl = channel_list(tourism, cfg, kind)
        print(f"\n--- {name}：{kind}と判定したチャンネル（国内・海外の合計） ---")
        if cl.empty:
            print("  なし（検索の上位に出てこない）")
        else:
            cl["再生合計"] = cl["再生合計"].map(lambda x: f"{int(x):,}")
            print(cl.head(15).to_string(index=False))


if __name__ == "__main__":
    main()
