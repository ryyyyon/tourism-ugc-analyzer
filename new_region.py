"""
新しい観光地の設定ファイル（regions/<地域名>.json）を、地名から自動で作る

使い方:
  python new_region.py --ja 下呂 --en Gero                       # 日本語と英語の名前から作る
  python new_region.py --ja 下呂 --en Gero --auto                # 韓国語・中国語・タイ語の名前をWikidataから補う
  python new_region.py --ja 下呂 --en Gero --ko 게로 --zh 下呂 --th เกะโระ   # 名前を自分で指定する
  python new_region.py --ja 下呂 --en Gero --auto --spot 下呂温泉 --spot 合掌村
  python new_region.py --ja 下呂 --en Gero --auto --run          # 作ったあと、そのままデータを集めて分析する

  --run を付けると、動画・コメント・登録者数の取得とテーマ分類まで続けて実行する
  （APIキーが必要。クォータは1地域あたり約2,000ユニット。1日の無料枠は10,000）
  その後 python -m streamlit run dashboard.py で、サイドバーの「分析の対象」に新しい地域が出てくる

作るもの:
  - 検索キーワード（多言語、最大10個）: 日本語3、英語4、韓国語・中国語（繁体字）・タイ語 各1
  - 地域に関係する動画かを判定するための地名（place_terms、name_terms）
  - スポット名（--spot で指定したもの）
  - 除く言葉（extra_noise）は空。同じ名前の人や作品が混ざる場合は、
    classify_themes.py --region <地域名> の結果（上位の動画や未分類の動画）を見て足す

Wikidata（ウィキペディアの姉妹プロジェクト。地名の多言語の表記がそろっている）は、
--auto を付けたときだけ使う。通信に失敗しても、指定した名前だけで設定ファイルを作る。
"""

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date

from region_config import REGIONS_DIR, list_regions

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "tourism-ugc-analyzer/1.0 (tourism UGC analysis; student project)"
JAPAN_HINTS = ("日本", "県", "都", "府", "北海道", "市", "町", "村")
MAX_KEYWORDS = 10


# ===================== Wikidata から多言語の名前を取る =====================

def _get_json(params):
    url = WIKIDATA_API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode("utf-8"))


def find_place(ja):
    """日本語の地名から、Wikidataの項目（日本の市町村）を探す。見つからなければ None"""
    for query in (f"{ja}市", f"{ja}町", f"{ja}村", ja):
        res = _get_json({"action": "wbsearchentities", "search": query, "language": "ja",
                         "uselang": "ja", "type": "item", "limit": 7})
        for item in res.get("search", []):
            desc = item.get("description", "")
            if item.get("label") == query and any(h in desc for h in JAPAN_HINTS):
                return item["id"], item.get("label"), desc
    return None


def fetch_labels(qid):
    res = _get_json({"action": "wbgetentities", "ids": qid, "props": "labels",
                     "languages": "en|ko|zh-tw|zh-hant|zh|th|ja"})
    labels = res.get("entities", {}).get(qid, {}).get("labels", {})
    return {lang: v["value"] for lang, v in labels.items()}


def clean_label(lang, label):
    """「Narita, Chiba」「나리타시」「成田市」から、町の呼び名の部分だけを取り出す"""
    if not label:
        return None
    s = re.sub(r"\s*[（(].*?[)）]\s*", "", label).strip()
    if lang == "en":
        s = s.split(",")[0]
        s = re.sub(r"(?i)[\s\-](city|town|village|shi|machi|cho|mura)$", "", s).strip()
    elif lang == "ko":
        s = re.sub(r"(시|군|정|촌)$", "", s) if len(s) >= 3 else s
    elif lang in ("zh", "ja"):
        s = re.sub(r"[市町村]$", "", s) if len(s) >= 3 else s
    elif lang == "th":
        s = re.sub(r"^เมือง", "", s).strip()
    return s or None


def names_from_wikidata(ja):
    found = find_place(ja)
    if not found:
        print(f"  Wikidataで「{ja}」の市町村が見つかりませんでした。指定した名前だけで作ります。")
        return {}
    qid, label, desc = found
    labels = fetch_labels(qid)
    zh = labels.get("zh-tw") or labels.get("zh-hant") or labels.get("zh")
    out = {"en": clean_label("en", labels.get("en")), "ko": clean_label("ko", labels.get("ko")),
           "zh": clean_label("zh", zh), "th": clean_label("th", labels.get("th"))}
    print(f"  Wikidata: {label}（{desc}、{qid}）")
    for lang, v in out.items():
        print(f"    {lang}: {v or '（なし）'}")
    return {k: v for k, v in out.items() if v}


# ===================== 設定ファイルを組み立てる =====================

def make_slug(en):
    s = re.sub(r"[^a-z0-9]+", "_", en.lower()).strip("_")
    return s or "region"


def build_keywords(ja, en, ko=None, zh=None, th=None, spots=()):
    """多言語の検索キーワード。スポットを足して10個を超えるときは、一般的なものから減らす"""
    keywords = [f"{ja} 観光", f"{ja} グルメ", f"{ja} 旅行"]
    if en:
        keywords += [f"{en} Japan", f"{en} travel", f"things to do in {en}", f"{en} food"]
    if ko:
        keywords.append(f"{ko} 여행")
    if zh:
        keywords.append(f"{zh} 旅遊")
    if th:
        keywords.append(f"เที่ยว {th}")
    keywords += [s for s in spots if s not in keywords]
    droppable = [f"{ja} 旅行", f"{en} food", f"{en} travel"]
    while len(keywords) > MAX_KEYWORDS and droppable:
        k = droppable.pop(0)
        if k in keywords:
            keywords.remove(k)
    return keywords


def build_config(ja, en, ko=None, zh=None, th=None, spots=(), full=None, note=None):
    keywords = build_keywords(ja, en, ko, zh, th, spots)

    names = [ja]
    for n in (en, ko, zh, th):
        if n:
            v = n.lower() if n.isascii() else n
            if v not in names:
                names.append(v)
    place_terms = names + [s for s in spots if ja not in s and s not in names]

    cfg = {
        "name": full or ja,
        "note": note or (f"new_region.py で {date.today():%Y-%m-%d} に自動作成。"
                         f"同じ名前の人や作品が混ざる場合は、classify_themes.py の結果を見て extra_noise に足す"),
        "search_keywords": keywords,
        "place_terms": place_terms,
        "name_terms": names,
        # 英字の地名は、単語の途中に出てくるもの（gero → dangerous など）を数えない
        "strict_place_terms": True,
        "extra_tourism": {},
        "extra_noise": {},
    }
    if spots:
        cfg["spots"] = {"観光スポット": {s: [s] for s in spots}}
    return cfg


def main():
    parser = argparse.ArgumentParser(description="地名から regions/<地域名>.json を作る")
    parser.add_argument("--ja", required=True, help="日本語の地名（例: 下呂）。「市」は付けない")
    parser.add_argument("--en", help="英語（ローマ字）の地名（例: Gero）。--auto なら省略できる")
    parser.add_argument("--ko", help="韓国語の地名（例: 게로）")
    parser.add_argument("--zh", help="中国語（繁体字）の地名（例: 下呂）")
    parser.add_argument("--th", help="タイ語の地名")
    parser.add_argument("--auto", action="store_true", help="足りない言語の地名をWikidataから補う")
    parser.add_argument("--spot", action="append", default=[], help="スポット名（何度でも指定できる。検索キーワードにも足す）")
    parser.add_argument("--full", help="表示用の名前（例: 下呂（岐阜県下呂市））。省略すると日本語の地名")
    parser.add_argument("--slug", help="地域名（ファイル名）。省略すると英語の地名から作る（例: gero）")
    parser.add_argument("--force", action="store_true", help="同じ名前の設定ファイルがあっても上書きする")
    parser.add_argument("--dry-run", action="store_true", help="ファイルを作らずに中身だけ表示する")
    parser.add_argument("--run", action="store_true", help="作ったあと、データの取得と分析まで実行する（APIキーが必要）")
    args = parser.parse_args()

    names = {"en": args.en, "ko": args.ko, "zh": args.zh, "th": args.th}
    if args.auto and not all(names.values()):
        print("Wikidataで多言語の地名を調べています…")
        try:
            found = names_from_wikidata(args.ja)
        except Exception as e:  # 通信できない場合など
            print(f"  Wikidataに接続できませんでした（{e}）。指定した名前だけで作ります。")
            found = {}
        for k, v in found.items():
            names[k] = names[k] or v
    if not names["en"]:
        sys.exit("英語の地名が分かりません。--en Gero のように指定してください（--auto でも見つからなかった場合）。")

    slug = args.slug or make_slug(names["en"])
    path = REGIONS_DIR / f"{slug}.json"
    if path.exists() and not args.force and not args.dry_run:
        sys.exit(f"{path} はもうあります。上書きするなら --force を付けてください。")

    cfg = build_config(args.ja, names["en"], names["ko"], names["zh"], names["th"], args.spot, args.full)
    missing = [lang for lang in ("ko", "zh", "th") if not names[lang]]
    text = json.dumps(cfg, ensure_ascii=False, indent=2)

    print(f"\n地域名: {slug}")
    print(f"検索キーワード {len(cfg['search_keywords'])}個（推定クォータ 約{len(cfg['search_keywords']) * 200:,}ユニット）:")
    for k in cfg["search_keywords"]:
        print(f"  - {k}")
    if missing:
        print(f"（{', '.join(missing)} の地名がないので、その言語の検索キーワードは入れていません。--auto か --{missing[0]} で足せます）")
    if len(cfg["search_keywords"]) > MAX_KEYWORDS:
        print(f"※ キーワードが{MAX_KEYWORDS}個を超えています。1日の無料枠（10,000ユニット）で分析できる地域数が減ります。")

    if args.dry_run:
        print("\n--dry-run なのでファイルは作りません。中身:\n" + text)
        return
    REGIONS_DIR.mkdir(exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    print(f"\n作りました: {path}（いま使える地域: {', '.join(list_regions())}）")

    if not args.run:
        print(f"\n次に実行するコマンド:\n  python run_pipeline.py --region {slug}\n"
              f"  python fetch_channel_stats.py --region {slug}\n  python -m streamlit run dashboard.py")
        return

    import check_youtube_volume
    import classify_themes
    import fetch_channel_stats
    import fetch_comments
    youtube = check_youtube_volume.get_youtube()
    check_youtube_volume.run(slug, youtube)
    fetch_comments.run(slug, youtube=youtube)
    classify_themes.run(slug)
    fetch_channel_stats.run(slug, youtube)
    print(f"\n完了しました。python -m streamlit run dashboard.py で、サイドバーの「分析の対象」から {cfg['name']} を選べます。"
          f"\n同じ名前の人や作品が混ざっていたら、regions/{slug}.json の extra_noise に足して "
          f"python classify_themes.py --region {slug} をやり直してください（APIは使いません）。")


if __name__ == "__main__":
    main()
