"""
テーマ分類の辞書とルール

【辞書の構成】
  - このファイル: どの地域にも共通する辞書（「電車」「海鮮」「温泉」など）
  - regions/<地域>.json: その地域だけの追加キーワード（「銚子電鉄」「江ノ電」など）と追加ノイズ
  ※ 何がノイズかは地域によって違う。銚子ではアニメの動画はキャラクター名が
    一致しただけのノイズだったが、大洗（ガルパン）や鎌倉（スラムダンク）では
    アニメの聖地巡礼そのものが観光の柱になっている

【分類の流れ】
  0. 量産型のホテル紹介動画（再生数がほぼ0の、同じ型のタイトルの動画）は対象外
     （銚子の海外向け「温泉・宿」16本がすべてこれだったため）
  1. タイトルに「優先テーマ」（アニメ聖地）のキーワードがあれば、そのテーマに決定
     （「アニメ」がノイズ判定に引っかかる前に拾うため）
  1'. 地域のアニメ作品名（regions/*.json の anime_works）がタイトルにあれば
       - 「聖地・巡礼」など強い訪問の語があれば → アニメ聖地（観光）
       - 「劇場版・本編・edit」などファン向けの語があれば → 作品ファン向けとしてノイズ
       - 「旅・観光・town」など弱い訪問の語か、共通辞書の観光の語があれば → アニメ聖地
       - どれもなければ → 作品ファン向けとしてノイズ
     （大洗の「アニメ聖地」に、実際の聖地巡礼と、戦闘シーン・音楽などの
       ファン向け動画が混ざっていたため）
  2. タイトルに「ノイズ」のキーワードがあれば、観光分析の対象外にする
     ただし「anime-like」「ジブリみたい」のように景色のたとえとしての言い方は除く
     （初回データで、津波速報・演歌・アニメ・回転寿司チェーン等が混ざっていたため）
  3. 観光テーマごとに「タイトルでの一致×2 + 説明文での一致×1」で点数をつけ、
     一番点数の高いテーマを主テーマにする（同点なら辞書の上にあるテーマを優先）
  3'. 色々な場所を回る旅行を示す語（「1日プラン」「日帰り」「당일치기」など）がタイトルにあれば「旅行全般」
       ただし、1つのテーマの語がタイトルに2つ以上ある動画は、そのテーマのままにする
     （人の目での検証で、間違いの半分が「人は旅行全般と判断したのに、分類はグルメや鉄道にした」
       ものだったため。特に鎌倉の、複数の場所を回る旅行動画で多かった）
  4. どの観光テーマにも当てはまらないが「旅行・vlog」等の語があれば「旅行全般」
  5. それも無ければ「未分類」→ 未分類の動画を見て辞書を改善していく

【キーワードの書き方】
  - アルファベットだけのキーワードは単語単位で一致させる
    （"house" が "lighthouse" に一致しないように）
  - 照合の前に文字を標準の形にそろえる（NFKC正規化）
    （装飾文字「𝙃𝙤𝙢𝙖𝙧𝙚」や全角英数がキーワードをすり抜けていたため）
  - 末尾に * を付けると前方一致（"akiya*" → "#AkiyaHub" のようなハッシュタグにも一致）
  - 日本語・中国語・韓国語は部分一致
"""

import re
import unicodedata

# ===== 共通の観光テーマ（上にあるほど同点時に優先される） =====
TOURISM_THEMES = {
    "鉄道": [
        "電鉄", "鉄道", "電車", "列車", "前面展望", "車掌", "特急", "ローカル線",
        "train", "trains", "railway", "railroad", "conductor", "cabview", "cab view", "tetsudou", "tetsudo",
        "電鐵", "电铁", "鐵道", "铁道", "鐵路", "铁路", "列车", "电车",
        "철도", "기차", "전철", "열차", "รถไฟ",  # タイ語「電車」
    ],
    "灯台・日の出": [
        "灯台", "日の出", "初日の出", "ご来光", "朝日",
        "lighthouse", "sunrise",
        "燈塔", "灯塔", "日出", "등대", "일출",
    ],
    "絶景・自然": [
        "絶景", "海岸", "展望台", "展望館", "ジオパーク", "富士山",  # 「展望」だけだと「展望風呂」に一致する "夕日", "夕陽", "星空", "海食崖", "崖", "ドローン", "空撮",
        "nature", "scenery", "scenic", "viewpoint", "ocean view", "cliff", "cliffs", "coast", "beach",
        "geopark", "sunset", "drone", "aerial", "mount fuji", "mt fuji",
        "praia", "praias", "bãi biển", "playa", "pantai",  # ポルトガル語・ベトナム語・スペイン語・インドネシア語の「ビーチ」
        "絕景", "绝景", "風景", "风景", "懸崖", "悬崖",
        "절경", "풍경", "해안", "절벽", "후지산",
    ],
    "グルメ": [
        "海鮮", "丼", "寿司", "すし", "鮨", "刺身", "市場", "イワシ", "いわし", "鰯", "金目", "キンメ",
        "サバ", "鯖", "マグロ", "鮪", "しらす", "カニ", "蟹", "イクラ", "明太", "食堂", "ランチ", "グルメ", "食べ", "ラーメン", "醤油",
        "食べ歩き", "食べ放題", "飲み放題", "バイキング", "抹茶", "アイス", "クレープ",
        "カフェ", "珈琲", "コーヒー", "プリン", "スイーツ", "パン屋", "ベーカリー",  # 「パン」だと「ジャパン」に一致する
        "food", "seafood", "sushi", "eat", "eating", "restaurant", "fish market", "soy sauce",
        "cafe", "coffee", "dessert", "matcha", "ice cream", "crepe", "street food",
        "美食", "醬油", "酱油", "壽司", "海鲜", "咖啡",
        "맛집", "먹방", "초밥", "해산물", "카페", "말차", "아이스크림", "크레페",  # 「회(刺身)」は「회사(会社)」等に一致するので入れない
    ],
    "温泉・宿": [
        "温泉", "旅館", "ホテル", "民宿", "宿泊", "露天", "泊まる", "泊まって", "お宿",
        "onsen", "hot spring", "ryokan", "hotel",
        "溫泉", "飯店", "旅馆", "住宿",
        "온천", "료칸", "호텔", "숙소",
    ],
    "漁港・釣り": [
        "漁港", "漁船", "船", "水揚げ", "釣り", "釣果", "釣り船", "遊漁船", "出船", "凪", "荒れ",
        "波予報", "m予報", "遊覧船", "イルカ",
        "fishing", "fisherman", "fishermen", "port", "harbor", "harbour", "dolphin",
        "釣魚", "钓鱼", "港口", "낚시", "항구",
    ],
    "アクティビティ": [
        "サイクリング", "自転車", "ロードバイク", "ツーリング", "サーフィン", "サーフ", "キャンプ",
        "グランピング", "海水浴", "ヨット", "セーリング",
        "cycling", "cyclist", "bike", "bicycle", "touring", "surfing", "surf", "camping", "sup",
        "glamping", "sailing",
        "單車", "骑行", "自行车", "衝浪", "冲浪", "자전거", "서핑", "캠핑",
    ],
    "水族館・施設": [
        "水族館", "博物館", "美術館", "資料館", "科学館",
        "aquarium", "museum",
        "水族馆", "博物馆", "수족관", "박물관",
    ],
    "町歩き・レトロ": [
        "散歩", "さんぽ", "散策", "街歩き", "町歩き", "昭和", "レトロ", "路地", "商店街", "遊里", "廃墟",
        "現状", "寂れ", "さびれ", "廃れ", "復活", "シャッター", "デート", "青春", "人力車",
        "walk", "walking", "retro", "showa", "declining", "countryside",
        "散步", "懷舊", "怀旧", "산책", "레트로", "쇠퇴", "망해", "데이트",
    ],
    "歴史・寺社": [
        "神社", "寺", "陣屋", "城跡", "史跡", "歴史", "大仏",
        "shrine", "temple", "history", "historic",
        "寺廟", "寺庙", "歷史", "历史", "신사", "사찰", "역사",
    ],
    "祭り・イベント": [
        "祭り", "まつり", "花火", "神輿", "みこし", "トライアスロン", "イベント", "カーニバル", "旧車",
        "festival", "fireworks", "matsuri", "carnival", "event",
        "祭典", "煙火", "烟花", "축제", "불꽃",
    ],
    "空き家・移住": [
        "空き家", "空家", "移住", "不動産", "物件", "古民家",
        "akiya*", "house", "real estate", "property", "cheap house",
        "房子", "房產", "房产", "빈집", "이주",
    ],
    "アニメ聖地": [
        # 「聖地」「巡礼」だけだと「サーフィンの聖地」やお寺の巡礼まで拾うので、アニメに限定した言い方にする
        "聖地巡礼", "アニメの舞台", "舞台探訪", "anime pilgrimage", "anime location", "anime spot",
        "圣地巡礼", "聖地巡禮", "성지순례",
    ],
}

# ノイズ判定より先に見るテーマ（アニメの聖地巡礼は「アニメ」でノイズにされないように）
# 地域の設定にこのテーマがあるときだけ、ほかのテーマより優先する
# （乗り継ぎ・待ち時間は成田の追加。乗り継ぎ客の動画は、戦略の一番の相手になる人たちの動画なので）
PRIORITY_THEMES = ["乗り継ぎ・待ち時間", "アニメ聖地"]

# アニメ作品名と一緒に出てきたら「現地を訪れている」とみなす語
# 強い語: これがあれば、ファン向けの語があっても聖地巡礼とみなす
STRONG_PILGRIMAGE_CUES = [
    "聖地", "巡礼", "舞台探訪", "ロケ地", "pilgrimage", "real life", "irl", "巡禮", "打卡", "성지순례",
]
# 弱い語: ファン向けの語がなければ、聖地巡礼とみなす
# ※「街」「町」だけだと「大洗市街戦（劇中の戦闘シーン）」に一致するので使わない
PILGRIMAGE_CUES = [
    "旅", "観光", "散策", "歩く", "歩いて", "巡り", "行って", "訪れ", "街歩き", "町歩き", "まち歩き", "街並み",
    "visit", "visiting", "trip", "travel", "tour", "walk", "walking", "vlog",
    "location", "locations", "town", "city", "spot", "spots", "crossing", "saved",
    "旅行", "旅游", "여행", "다녀왔", "방문", "일차",  # 韓国語「行ってきた」「訪問」「〇日目」
]
# 作品ファン向けの動画によく出る語（劇中映像・音楽・ゲームなど）
ANIME_FAN_WORDS = [
    "劇場版", "本編", "最終章", "ending", "opening", "official", "trailer", "edit", "amv",
    "war thunder", "vs", "soundtrack", "hymn", "theme", "感想", "考察", "キャラ", "声優",
    "explained", "ambush", "쟁탈전",  # 韓国語「争奪戦」（ゲームの対戦動画）
]
# 話数の表記（「第十二話」「episode 3」など）。作品名があるときだけファン向けの印として使う
EPISODE_PATTERN = re.compile(r"第[0-9一二三四五六七八九十百]+話|episode\s*\d+|\bep\.?\s*\d+")

# ノイズ判定を取り消す言い方（景色のたとえとしての「アニメ」）
NOISE_EXCEPTIONS = [
    "anime-like", "like an anime", "like anime", "anime vibes", "ghibli", "ジブリ",
    "アニメみたい", "アニメのよう", "アニメの世界",
]

# 量産型のホテル紹介動画の判定（タイトルに "hotel" があり、チャンネル名がホテル紹介系）
FARM_CHANNEL_WORDS = ["hotel", "hotels", "stay", "stays", "suite", "suites", "nest", "nests",
                      "resort", "room", "rooms", "inn", "lodging"]
FARM_TITLE_WORDS = ["hotel review", "hotel reviews"]

# 動画の形式（テーマとは別の軸。1本に複数つくこともある）
FORMATS = {
    "散歩・車窓（言語不要）": [
        "4k", "8k", "60fps", "walk", "walking", "散歩", "さんぽ", "散策", "車窓", "前面展望",
        "cabview", "cab view", "drive", "driving", "ドライブ", "ambient", "asmr", "no talking",
        "relaxing", "binaural", "산책", "散步",
    ],
    "ショート動画": ["#shorts", "#short", "shorts"],
    "Vlog": ["vlog", "ブイログ", "브이로그"],
    "ガイド・おすすめ": [
        "top 5", "top 10", "best", "guide", "things to do", "must visit", "day trip", "itinerary",
        "おすすめ", "まとめ", "ランキング", "ガイド", "モデルコース", "1日プラン",
        "攻略", "推薦", "推荐", "추천", "코스",
    ],
}

# 色々な場所を回る旅行であることを示す語（タイトルにあれば「旅行全般」を優先する）
ITINERARY_CUES = [
    "1日", "一日", "日帰り", "プラン", "モデルコース", "総まとめ", "満喫", "巡り",
    "day trip", "day trips", "itinerary", "things to do", "must visit",
    "一日遊", "自由行", "당일치기", "코스", "총정리",
]

# どの観光テーマにも当てはまらない場合の受け皿
GENERIC_TRAVEL = [
    "เที่ยว", "ทริป", "viaje", "excursión", "du lịch", "wisata",  # タイ語・スペイン語・ベトナム語・インドネシア語の「旅行」
    "旅行", "観光", "旅", "日帰り", "vlog", "一人旅", "ひとり旅", "ドライブ", "帰省",
    "1日プラン", "穴場", "隠れスポット",
    "travel", "trip", "trips", "day trip", "day trips", "tour", "visit", "japan travel", "sightseeing", "underrated",
    "旅遊", "旅游", "여행", "브이로그", "방문", "다녀왔",
]

# ===== 共通のノイズ（タイトルだけで判定） =====
NOISE_THEMES = {
    "ニュース・災害": [
        "津波", "地震", "速報", "ニュース", "台風", "大雨", "警報", "火事", "火災", "事故", "緊急", "異変",
        "会見", "謝罪", "被災", "復興",
        "news", "tsunami", "earthquake", "typhoon", "breaking",
        "新聞", "쓰나미", "지진", "뉴스",
    ],
    "事件": [
        "行方不明", "事件", "逮捕", "殺人", "遺体", "失踪",
        "missing", "murder", "crime",
    ],
    "音楽": [
        "演歌", "歌謡", "歌詞", "カラオケ", "歌ってみた", "ライブ映像", "歌唱", "篠笛", "尺八",
        "music video", "mv", "live at", "lyrics", "song", "cover", "shinobue", "shakuhachi", "flute",
        "の唄", "のうた", "sung by", "soundtrack", "ost", "hymn", "dtm",
        "翻唱", "노래",
    ],
    "アニメ・ゲーム": [
        "アニメ", "声優", "ゲーム", "ゲーム実況",  # 「実況」だけだと「ゆっくり実況」の旅行動画まで除外していた
        "anime", "amv", "edit", "game", "gameplay",
        "动漫", "動漫", "애니",
    ],
    "暮らし": [
        "田舎暮らし",  # 生活の記録は観光とは別（人の目での検証より）
    ],
    "学校・スポーツ": [
        "高校", "高等学校", "甲子園", "部活", "吹奏楽", "野球部", "サッカー部", "千葉大会", "全イニング", "秋季", "春季",
        "high school",
    ],
}


def normalize(text):
    return unicodedata.normalize("NFKC", text if isinstance(text, str) else "").lower()


_ASCII = re.compile(r"^[a-z0-9 .'\-]+$")


class _Keyword:
    """元のキーワード（表示用）と、照合用の正規表現をまとめたもの"""

    def __init__(self, word):
        self.word = word.rstrip("*")
        kw = normalize(word)
        if kw.endswith("*"):
            self.pattern = re.compile(r"(?<![a-z])" + re.escape(kw[:-1]))
        elif _ASCII.match(kw):
            self.pattern = re.compile(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])")
        else:
            self.pattern = re.compile(re.escape(kw))

    def search(self, text):
        return self.pattern.search(text)


def _compile(keywords):
    return [_Keyword(w) for w in keywords]


def _merge(base, extra):
    """共通の辞書に地域の追加キーワードを足す（新しいテーマは末尾に追加）"""
    merged = {theme: list(words) for theme, words in base.items()}
    for theme, words in extra.items():
        merged.setdefault(theme, [])
        merged[theme] = list(words) + merged[theme]
    return merged


class Classifier:
    """地域設定（regions/<地域>.json の中身）を受け取って、関連判定とテーマ分類を行う"""

    def __init__(self, region=None):
        region = region or {}
        tourism = _merge(TOURISM_THEMES, region.get("extra_tourism", {}))
        noise = _merge(NOISE_THEMES, region.get("extra_noise", {}))
        self.place_terms = [normalize(t) for t in region.get("place_terms", [])]
        # new_region.py で作った地域は、英字の地名を単語の頭からだけ探す（「gero」が「dangerous」に一致しないように）
        self._strict_place = bool(region.get("strict_place_terms"))
        self._place_pats = [re.compile(r"(?<![a-z])" + re.escape(t)) if _ASCII.match(t) else re.compile(re.escape(t))
                            for t in self.place_terms]
        self._tourism = {t: _compile(k) for t, k in tourism.items()}
        self._noise = {t: _compile(k) for t, k in noise.items()}
        self._priority = {t: self._tourism[t] for t in PRIORITY_THEMES if t in self._tourism}
        self._generic = _compile(GENERIC_TRAVEL)
        self._itinerary = _compile(ITINERARY_CUES)
        self._anime_works = _compile(region.get("anime_works", []))
        self._strong_cues = _compile(STRONG_PILGRIMAGE_CUES)
        self._cues = _compile(PILGRIMAGE_CUES)
        self._fan_words = _compile(ANIME_FAN_WORDS)
        # 聖地巡礼かの判定には共通辞書だけを使う（地域の追加辞書は地名が多く、
        # キャラクター名「屏風ヶ浦帆稀」の地名部分に一致してしまうため）
        self._base_tourism = [p for theme, words in TOURISM_THEMES.items()
                              if theme != "アニメ聖地" for p in _compile(words)]
        self._noise_exceptions = _compile(NOISE_EXCEPTIONS)
        self._farm_channel = _compile(FARM_CHANNEL_WORDS)
        self._farm_title = _compile(FARM_TITLE_WORDS)
        self._hotel = _compile(["hotel"])
        self._formats = {f: _compile(k) for f, k in FORMATS.items()}

    def formats(self, title):
        """動画の形式（散歩・車窓、ショートなど）を判定して、当てはまるもののリストを返す"""
        t = normalize(title)
        return [f for f, pats in self._formats.items() if any(p.search(t) for p in pats)]

    def _is_content_farm(self, t, c):
        if not any(p.search(t) for p in self._hotel):
            return False
        return any(p.search(t) for p in self._farm_title) or any(p.search(c) for p in self._farm_channel)

    def is_relevant(self, title, description):
        """地名がタイトルか説明文に入っていれば、その地域の動画とみなす"""
        text = normalize(f"{title} {description}")
        if self._strict_place:
            return any(p.search(text) for p in self._place_pats)
        return any(term in text for term in self.place_terms)

    def classify(self, title, description, channel=""):
        """
        返り値: dict
          is_noise      : 観光分析の対象外なら True
          primary_theme : 主テーマ
          themes        : 当てはまったテーマ全部（点数の高い順）
          matched       : 主テーマで一致したキーワード（なぜその分類かの説明用）
        """
        t = normalize(title)
        d = normalize(description)[:300]
        c = normalize(channel)

        if self._is_content_farm(t, c):
            return {"is_noise": True, "primary_theme": "量産型の宿紹介", "themes": ["量産型の宿紹介"],
                    "matched": ["hotel"]}

        for theme, pats in self._priority.items():
            hits = [p.word for p in pats if p.search(t)]
            if hits:
                return {"is_noise": False, "primary_theme": theme, "themes": [theme], "matched": hits}

        works = [p.word for p in self._anime_works if p.search(t)]
        if works:
            strong = [p.word for p in self._strong_cues if p.search(t)]
            fan = [p.word for p in self._fan_words if p.search(t)]
            if EPISODE_PATTERN.search(t):
                fan.append("話数")
            cues = [p.word for p in self._cues if p.search(t)]
            tourism_hit = [p.word for p in self._base_tourism if p.search(t)]
            if strong or (not fan and (cues or tourism_hit)):
                return {"is_noise": False, "primary_theme": "アニメ聖地", "themes": ["アニメ聖地"],
                        "matched": works + (strong or cues or tourism_hit)}
            return {"is_noise": True, "primary_theme": "アニメ作品（ファン向け）",
                    "themes": ["アニメ作品（ファン向け）"], "matched": works + fan}

        if not any(p.search(t) for p in self._noise_exceptions):
            for theme, pats in self._noise.items():
                hits = [p.word for p in pats if p.search(t)]
                if hits:
                    return {"is_noise": True, "primary_theme": theme, "themes": [theme], "matched": hits}

        scores, matched, title_hits = {}, {}, {}
        for theme, pats in self._tourism.items():
            in_title = [p for p in pats if p.search(t)]
            in_desc = [p for p in pats if p.search(d)]
            score = 2 * len(in_title) + len(in_desc)
            if score:
                scores[theme] = score
                matched[theme] = sorted({p.word for p in in_title + in_desc})
                title_hits[theme] = len(in_title)
        itinerary = [p.word for p in self._itinerary if p.search(t)]
        if itinerary and not any(n >= 2 for n in title_hits.values()):
            ordered = sorted(scores, key=scores.get, reverse=True)
            return {"is_noise": False, "primary_theme": "旅行全般", "themes": ["旅行全般"] + ordered,
                    "matched": itinerary}
        if scores:
            primary = max(scores, key=scores.get)
            ordered = sorted(scores, key=scores.get, reverse=True)
            return {"is_noise": False, "primary_theme": primary, "themes": ordered, "matched": matched[primary]}

        if any(p.search(t) or p.search(d) for p in self._generic):
            return {"is_noise": False, "primary_theme": "旅行全般", "themes": ["旅行全般"], "matched": []}
        return {"is_noise": False, "primary_theme": "未分類", "themes": [], "matched": []}
