"""
言語判定の共通モジュール

【動画の言語判定ルール】（初回の確認で分かった問題を踏まえた設計）
  問題1: 日本の投稿者がYouTubeの言語設定を英語のままにしていることが多く、
         日本語タイトルの動画が「en」と判定されていた
  問題2: 「zxx（言語なし）」という設定値の動画も、中身は日本語だった
  問題3: ローマ字の日本語タイトル（例: Choshi tsuri vlog）は langdetect が
         アルバニア語など無関係な言語と誤判定する

  → 投稿者の設定よりも「タイトルの文字種」を優先する
    0. 日本語と英語が混ざっていて英語が主 → en
    1. タイトルにかながある          → ja
    2. タイトルにハングルがある        → ko
       タイ文字・キリル文字などがある → th / ru など
    3. タイトルが漢字のみ
         説明文にかながある          → ja
         タイトルに中国語特有の字がある → 説明文より優先して中国語と判定
         中国語特有の文字がある or 長い → zh-tw（繁体字）/ zh-cn（簡体字）/ zh-hk（広東語）/ zh
         それ以外                   → zh_or_ja（判別不能）
       ※ langdetect は漢字だけの文章を韓国語と誤判定しやすいため使わない
         （例: 台湾の銚子電鉄の動画タイトルが ko 71% と判定された）
    4. タイトルがアルファベット等
         投稿者の言語設定あり（zxx以外）→ その言語
         タイトルが英語と判定          → en
         説明文にかながある           → ja（ローマ字の日本語タイトルとみなす）
         それ以外                    → langdetect の結果
"""

import re

from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0  # 毎回同じ結果にする

KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af]")
HAN = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")
# その他の文字体系（タイ語のタイトルが投稿者設定で en と判定されていたため追加）
OTHER_SCRIPTS = [
    (re.compile(r"[\u0e00-\u0e7f]"), "th"),   # タイ文字
    (re.compile(r"[\u0900-\u097f]"), "hi"),   # デーヴァナーガリー（ヒンディー語など）
    (re.compile(r"[\u0400-\u04ff]"), "ru"),   # キリル文字（ロシア語など）
    (re.compile(r"[\u0600-\u06ff]"), "ar"),   # アラビア文字
]


def _other_script(text):
    for pattern, lang in OTHER_SCRIPTS:
        if pattern.search(text):
            return lang
    return None

# 日本語ではほぼ使われず、中国語で頻出する文字（かなのない漢字だけの文章の判定用）
ZH_MARKERS = set("值吃喝的是了這这們们個个嗎吗呢很還还說说麼么吧啊沒没誰谁睇喺嘅咗啲冇佢咁嚟嘢")
# 繁体字にしかない字 / 簡体字にしかない字
# ※「学・号・国・会」のように日本語と簡体字で同じ形の字は、日本語を簡体字と
#   誤判定する原因になった（HEY-SMITHの動画が zh-cn になった）ので含めない
TRAD_ONLY = set("禮價實點雙舊廳臺這們說來鐵縣號邊國會對關灣學專與樂藥發變經歲戰濱燒歸覺觀應廣樣傳賣讀圖轉總勞榮氣驛圓區醬騷")
SIMP_ONLY = set("价实厅这们说还铁县边对关专无华为东车时开门问间电岛业后个乐药发变经岁战滨烧归觉观应广样传卖读图转总劳荣气驿圆酱骚")
# 日本語（新字体）にしかない字。これがあれば中国語とは判定しない
# （例: 「銚子電気鉄道前面展望」が繁体字と誤判定された）
JA_ONLY = set("気鉄駅県円広様伝売読図転対辺関総労栄楽薬発変済経検験険桜歳戦浜沢焼帰覚観応拡巻黒渋畳徳恵歩毎説")
ZH_MIN_HAN = 12  # かなを含まずに漢字がこれだけ続く文章は、日本語ではまず無い
# 広東語（香港）特有の字。香港の動画タイトルが、説明文のかなのせいで日本語と判定されていたため追加
# （例:「隱世美景！浸住溫泉睇日出」の「睇（見る）」）
CANTONESE = set("睇喺嘅咗啲冇佢咁嚟嘢")

# 「海外向け」とみなさない言語ラベル
NON_FOREIGN = {"ja", "zh_or_ja", "unknown"}


def _langdetect(text):
    try:
        return detect(text)
    except LangDetectException:
        return None


def classify_han_only(text):
    """かなもハングルもない漢字の文章を、中国語かどうか判定する（判別不能なら None）"""
    han = HAN.findall(text)
    has_marker = bool(set(han) & ZH_MARKERS)
    ja = sum(ch in JA_ONLY for ch in han)
    trad = sum(ch in TRAD_ONLY for ch in han)
    simp = sum(ch in SIMP_ONLY for ch in han)
    if set(han) & CANTONESE:
        return "zh-hk"
    if not has_marker:
        if ja > 0:
            return None  # 日本語にしかない字がある
        if len(han) < ZH_MIN_HAN or (trad == 0 and simp == 0):
            return None  # 短い、または繁体字・簡体字の決め手がない
    if trad > simp:
        return "zh-tw"
    if simp > trad:
        return "zh-cn"
    return "zh"


def normalize_code(code):
    """YouTubeの言語コードを langdetect と同じ形式にそろえる"""
    code = code.lower()
    if code.startswith("zh"):
        if any(x in code for x in ("tw", "hk", "hant")):
            return "zh-tw"
        return "zh-cn"
    return code.split("-")[0]


# 2か国語のタイトルの判定用（ハッシュタグの英語は数えない）
EN_WORD = re.compile(r"(?<![#\w])[A-Za-z][A-Za-z'’]{2,}")
JA_CHAR = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


# 英単語として数えない語：地名のローマ字と、日本の投稿者がよく添える単語
# （最初の版ではこれらも数えていたため、「銚子 犬吠埼 Drone Inubosaki Chiba Japan」のような
#   日本人向けの動画まで英語と判定し、銚子の海外向けが143本→285本に膨らんだ）
EN_IGNORE = {
    "japan", "japanese", "tokyo", "chiba", "ibaraki", "kanagawa", "choshi", "inubosaki", "inubousaki",
    "byobugaura", "kamakura", "enoshima", "enoden", "oarai", "ooarai", "shonan", "narita",
    "vlog", "travel", "trip", "tour", "drone", "cinematic", "shorts", "short", "video", "movie",
    "electric", "railway", "line", "station", "view",  # 「Choshi Electric Railway」「Cab View」など名前の一部
}


def is_bilingual_english(title):
    """
    日本語と英語が混ざったタイトルで、英語が主なら True
    （人の目での検証で「夕焼けの江ノ電 🌇 A Sunset Moment on the Enoden」のような
      2か国語のタイトルが、かなを含むため日本語と判定されていたため）
    条件: 英単語（3文字以上、ハッシュタグ・地名・よく添えられる単語を除く）が3つ以上あり、
          英語の文字数が日本語の文字数以上
    """
    words = [w for w in EN_WORD.findall(title)
             if re.sub(r"['’]s?$", "", w.lower()) not in EN_IGNORE]
    if len(words) < 3:
        return False
    return sum(len(w) for w in words) >= len(JA_CHAR.findall(title))


def _valid_setting(code):
    return isinstance(code, str) and code and code.lower() != "zxx"


def detect_video_language(title, description, audio_lang=None, default_lang=None):
    """動画の言語を推定して (言語, 判定根拠) を返す"""
    title = title if isinstance(title, str) else ""
    desc = description if isinstance(description, str) else ""

    if (KANA.search(title) or HAN.search(title)) and not HANGUL.search(title) and is_bilingual_english(title):
        return "en", "title_bilingual"
    if KANA.search(title):
        return "ja", "title_script"
    if HANGUL.search(title):
        return "ko", "title_script"
    other = _other_script(title)
    if other:
        return other, "title_script"
    if HAN.search(title):
        # タイトルに中国語特有の字があれば、説明文のかなより優先する
        if set(title) & ZH_MARKERS:
            return classify_han_only(f"{title} {desc[:200]}") or "zh", "han_rule"
        if KANA.search(desc):
            return "ja", "description_script"
        lang = classify_han_only(f"{title} {desc[:200]}")
        if lang:
            return lang, "han_rule"
        return "zh_or_ja", "unresolved"

    for code in (audio_lang, default_lang):
        if _valid_setting(code):
            return normalize_code(code), "youtube_setting"
    if _langdetect(title) == "en":
        return "en", "langdetect_title"
    if KANA.search(desc):
        return "ja", "description_script"
    return _langdetect(f"{title} {desc[:200]}") or "unknown", "langdetect"


def detect_text_language(text, min_letters=4):
    """コメントなど短いテキストの言語を推定する"""
    text = text.strip() if isinstance(text, str) else ""
    if KANA.search(text):
        return "ja"
    if HANGUL.search(text):
        return "ko"
    other = _other_script(text)
    if other:
        return other
    if HAN.search(text):
        return classify_han_only(text) or "zh_or_ja"
    if len(LATIN.findall(text)) < min_letters:
        return "unknown"  # 絵文字だけ・短すぎるものは判定しない
    return _langdetect(text) or "unknown"


def is_foreign(lang):
    return lang not in NON_FOREIGN
