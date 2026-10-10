"""
主要キーワードの分析：タイトルと説明文によく出てくる言葉を、データから取り出して数える

使い方:
  python analyze_keywords.py --region narita                 # 国内向け・海外向けそれぞれの上位20語
  python analyze_keywords.py --region narita --top 30
  python analyze_keywords.py --region narita --field title   # タイトルだけで数える
  python analyze_keywords.py --region narita --lang en       # 海外向けは英語の動画だけ

前提: ステップ1〜3を実行済み。APIは使わない。
推奨: janome（pip install janome）。日本語の文を単語に分けるのに使う。
     入っていない場合は、カタカナと漢字のまとまりで代わりに切り出す（精度は下がる）

【なぜ作ったか】
  これまでの「スポット・特産品の名前」の集計は、自分で決めた名前のリスト（regions/<地域>.json の spots）を
  数えるだけで、データから新しい言葉を見つけることはできなかった。
  ここでは、決め打ちのリストを使わずに、動画のタイトルと説明文に実際に出てくる言葉を数える。

【数え方】
  - 1本の動画に同じ言葉が何回出ても1回と数える（何本の動画に出てくるか）
  - 言語ごとに切り方を変える
      日本語: janomeで名詞を取り出し、続いている名詞はつなげる（「成田山」「新勝寺」→「成田山新勝寺」）
      英語など: 空白で区切った単語（the、travel などの一般的な語は除く）
      韓国語: 空白で区切り、語尾の助詞（에서、의 など）を外す
      中国語・タイ語: 空白がないので、文字のまとまりをそのまま使う（参考程度）
  - 地域の名前そのもの（成田、Narita、나리타など）と、旅行・日本・Vlogのような一般的な語は除く
  - 指標: 本数、その市場の動画のうちの割合、再生の合計と中央値、エンゲージメント率の中央値
    （エンゲージメント率 =（いいね＋コメント）÷ 再生数。再生100回以上の動画のみ）

読むときの注意:
  - 国内向けは日本語、海外向けは主に英語なので、国内と海外で同じ言葉が並ぶことは少ない。
    「何が話題になっているか」を市場ごとに見るためのもので、言葉の数を国内と海外で直接比べない
  - 検索キーワードに入れた言葉は、多く出やすい（集め方の影響）
"""

import argparse
import re
import unicodedata
from datetime import datetime

import pandas as pd

from region_config import data_dir

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 200)

try:
    from janome.tokenizer import Tokenizer
    _JANOME = Tokenizer()
except Exception:  # janome が入っていない環境でも動くようにする
    _JANOME = None

DESC_CHARS = 150   # 説明文は冒頭だけ使う（後半はSNSのリンクや定型文が多いため）
MAX_COMPOUND = 10  # これより長い複合語は、つなげずに部品ごとに数える

URL = re.compile(r"https?://\S+|www\.\S+|\S+\.(?:com|jp|net|org)\S*")
LATIN = re.compile(r"[a-z][a-z0-9'’\-]*[a-z0-9]")
HANGUL = re.compile(r"[가-힣]+")
THAI = re.compile(r"[\u0E00-\u0E7F]+")
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff々〆ー]+")
HAN_KATA = re.compile(r"[\u30a0-\u30ff\u3400-\u9fff\uf900-\ufaff々ー]{2,}")
HAS_KANA = re.compile(r"[\u3040-\u30ff]")
ZH_SPLIT = re.compile(r"[的了是在到和與与從从去最有也很就都及或跟把被給给讓让這这那個个們们嗎吗呢吧啊]")

# 一般的すぎて、どの地域でも出てくる言葉（地域の特徴を表さない）
STOP_LATIN = set("""
a an the and or but if of to in on at by for with from into onto over under about after before as is are was were be been
being am do does did done have has had will would can could should may might must not no yes it its it's this that these those
there here i me my we our you your he she they them their his her who whom whose what which when where why how all any some
more most much many very so too just only also than then now new one two three first last best top ultimate complete full
things thing stuff way ways day days night hour hours minute minutes time times part ep episode vol near around inside
vlog vlogs video videos short shorts youtube channel subscribe like comment share follow instagram tiktok facebook twitter
http https www com official music bgm song sub subs subtitle subtitles eng english
japan japanese nihon travel travels traveling travelling trip trips tourism tourist tourists vacation holiday holidays
de la el en los las del y que un una por con para es di dan ke yang ini itu untuk dengan da e o do dos das em na no um uma
""".split())
STOP_JA = set("""
観光 旅行 日本 動画 チャンネル 登録 チャンネル登録 概要 詳細 公式 今回 本日 今日 前回 次回 最後 最初 皆さん みなさん
こと もの ため よう それ これ あれ どこ ところ 感じ 自分 お願い ブログ インスタ 撮影 編集 音楽 使用 素材 提供 協力
旅 国内旅行 一人旅 ひとり旅 vlog ブイログ 日本旅行 観光地 観光スポット スポット おすすめ オススメ 紹介 情報 まとめ
方法 高評価 コメント 概要欄
""".split())
# この文字列を含む言葉は、説明文の定型文なので除く（「チャンネル登録お願いします」など）
BOILERPLATE = ["チャンネル登録", "高評価", "登録お願い", "subscribe", "구독", "訂閱", "订阅"]
STOP_KO = set(("일본 여행 일본여행 브이로그 영상 구독 좋아요 알림 채널 오늘 추천 정보 투어 방법 "
                "가는 하는 있는 없는 같은 정말 진짜 너무 이번 처음 함께 그리고 가기 하기").split())
STOP_ZH = set("日本 旅遊 旅游 旅行 自由行 日本旅遊 日本旅游 日本自由行 影片 訂閱 订阅 頻道 频道 推薦 推荐".split())
STOP_TH = set("เที่ยว ญี่ปุ่น เที่ยวญี่ปุ่น".split())
KO_PARTICLES = ["에서는", "에서도", "에서", "으로", "에게", "까지", "부터", "하고", "이랑", "에는", "에도",
                "랑", "에", "의", "은", "는", "이", "가", "을", "를", "와", "과", "도", "로", "만"]
NAME_SUFFIXES = ["市", "町", "村", "区", "県", "시", "군", "구", "city", "-shi"]


def normalize(text):
    return unicodedata.normalize("NFKC", text if isinstance(text, str) else "").lower()


def name_set(cfg):
    """地域の名前そのもの（成田、narita、나리타など）。キーワードから除く
    place_terms には成田山（naritasan）のような見どころの名前も入っているので使わず、町の名前（name_terms）だけを使う"""
    raw = list(cfg.get("name_terms") or cfg.get("place_terms", [])) + list(cfg.get("name_variants", []))
    base = cfg.get("name", "").split("（")[0]
    if base:
        raw.append(base)
    names = set()
    for t in raw:
        t = normalize(t)
        if not t:
            continue
        names.add(t)
        names.update(p for p in re.split(r"[ -]+", t) if len(p) >= 2)  # 「kawasaki daishi」→ kawasaki, daishi
    return names


def _is_name(term, names, stop=frozenset()):
    """地域の名前そのものか、名前＋一般的な語（成田市、成田観光、나리타여행など）なら True"""
    if term in names:
        return True
    for suf in NAME_SUFFIXES:
        if term.endswith(suf) and term[: -len(suf)].strip("- ") in names:
            return True
    for n in names:
        if len(n) >= 2 and term.startswith(n) and term[len(n):] in stop:
            return True
    return False


def _ja_terms(text):
    """janomeで名詞を取り出し、続いている名詞はつなげる"""
    out, buf = [], []

    def flush():
        if buf:
            word = "".join(buf)
            if len(word) <= MAX_COMPOUND:
                out.append(word)
            else:
                out.extend(p for p in buf if len(p) >= 2)
            buf.clear()

    for tok in _JANOME.tokenize(text):
        pos = tok.part_of_speech.split(",")
        if pos[0] == "名詞" and pos[1] in ("一般", "固有名詞", "サ変接続", "形容動詞語幹"):
            buf.append(tok.surface)
        elif pos[0] == "名詞" and pos[1] == "接尾" and buf and pos[2] in ("一般", "地域"):
            buf.append(tok.surface)
        else:
            flush()
    flush()
    return out


def _cjk_terms(text, japanese):
    if japanese and _JANOME is not None:
        return _ja_terms(text)
    # janome がない、または中国語：ひらがなと記号で区切った、漢字・カタカナのまとまり（長すぎるものは文なので除く）
    if not japanese:
        text = ZH_SPLIT.sub(" ", text)  # 中国語は「的」「到」などの字で区切る
    return [w for w in HAN_KATA.findall(text) if len(w) <= 6]


def _strip_particle(word):
    for p in KO_PARTICLES:
        if word.endswith(p) and len(word) - len(p) >= 2:
            return word[: -len(p)]
    return word


def extract_terms(text, lang="", names=frozenset()):
    """1本の動画のテキストから、言葉の集合を返す"""
    t = URL.sub(" ", normalize(text)).replace("#", " ")
    terms = set()

    for w in LATIN.findall(t):
        w = w.strip("'’-")
        if len(w) >= 3 and w not in STOP_LATIN and not w.isdigit():
            terms.add(w)
    for w in HANGUL.findall(t):
        w = _strip_particle(w)
        if len(w) >= 2 and w not in STOP_KO:
            terms.add(w)
    for w in THAI.findall(t):
        if 2 <= len(w) <= 20 and w not in STOP_TH:
            terms.add(w)

    japanese = lang == "ja" or bool(HAS_KANA.search(t))
    cjk_text = " ".join(CJK.findall(t))
    if cjk_text:
        stop = STOP_JA if japanese else STOP_ZH
        for w in _cjk_terms(cjk_text, japanese):
            if len(w) >= 2 and w not in stop and not re.fullmatch(r"[ー々]+", w):
                terms.add(w)

    stop = STOP_JA | STOP_ZH | STOP_KO | STOP_TH | STOP_LATIN
    return {w for w in terms
            if not _is_name(w, names, stop) and not any(b in w for b in BOILERPLATE)}


def video_texts(df, field="title+desc"):
    title = df["title"].fillna("")
    if field == "title":
        return title
    return title + " " + df["description"].fillna("").str[:DESC_CHARS]


def terms_per_video(df, cfg, field="title+desc"):
    """動画ごとの言葉の集合（df と同じ順番の Series）"""
    names = frozenset(name_set(cfg))
    texts = video_texts(df, field)
    langs = df["lang_final"] if "lang_final" in df.columns else pd.Series("", index=df.index)
    return pd.Series([extract_terms(x, l, names) for x, l in zip(texts, langs)], index=df.index)


def keyword_stats(df, terms, min_count=1):
    """言葉ごとに、本数・割合・再生・エンゲージメント率をまとめた表"""
    if df.empty:
        return pd.DataFrame(columns=["言葉", "本数", "割合", "再生合計", "再生中央値", "エンゲージメント率"])
    views = df["view_count"].fillna(0)
    likes = df["like_count"].fillna(0) if "like_count" in df.columns else 0
    comments = df["comment_count"].fillna(0) if "comment_count" in df.columns else 0
    eng = ((likes + comments) / views).where(views >= 100)
    rows = pd.DataFrame({"言葉": terms.map(sorted), "views": views, "eng": eng}).explode("言葉").dropna(subset=["言葉"])
    if rows.empty:
        return pd.DataFrame(columns=["言葉", "本数", "割合", "再生合計", "再生中央値", "エンゲージメント率"])
    g = rows.groupby("言葉")
    out = pd.DataFrame({
        "本数": g.size(),
        "再生合計": g["views"].sum(),
        "再生中央値": g["views"].median(),
        "エンゲージメント率": g["eng"].median(),
    }).reset_index()
    out["割合"] = out["本数"] / len(df)
    out = out[out["本数"] >= min_count]
    return out[["言葉", "本数", "割合", "再生合計", "再生中央値", "エンゲージメント率"]] \
        .sort_values(["本数", "再生合計"], ascending=False).reset_index(drop=True)


def market_keywords(tourism, cfg, field="title+desc", foreign_lang=None):
    """国内向け・海外向けそれぞれの言葉の表を返す {"国内": DataFrame, "海外": DataFrame}"""
    terms = terms_per_video(tourism, cfg, field)
    out = {}
    for market in ["国内", "海外"]:
        mask = tourism["market"] == market
        if market == "海外" and foreign_lang:
            mask &= tourism["lang_final"].astype(str).str.startswith(foreign_lang)
        out[market] = keyword_stats(tourism[mask], terms[mask])
    return out


def fmt(t):
    out = t.copy()
    out["割合"] = out["割合"].map(lambda x: f"{x:.0%}")
    out["再生合計"] = out["再生合計"].map(lambda x: f"{int(x):,}")
    out["再生中央値"] = out["再生中央値"].map(lambda x: f"{int(x):,}")
    out["エンゲージメント率"] = out["エンゲージメント率"].map(lambda x: "-" if pd.isna(x) else f"{x:.1%}")
    return out


def run(region_slug, top=20, field="title+desc", foreign_lang=None, min_count=5):
    from compare_regions import summarize
    cfg, _, tourism = summarize(region_slug)
    print(f"\n【{cfg['name']}】主要キーワード（{'タイトル' if field == 'title' else 'タイトル＋説明文の冒頭'}から）")
    if _JANOME is None:
        print("（janome が入っていないため、日本語はカタカナと漢字のまとまりで切り出しています。"
              "pip install janome で精度が上がります）")
    tables = market_keywords(tourism, cfg, field, foreign_lang)
    frames = []
    for market, t in tables.items():
        n = int((tourism["market"] == market).sum())
        print(f"\n=== {market}向け（{n}本）：よく出てくる言葉 上位{top} ===")
        print(fmt(t.head(top)).to_string(index=False) if len(t) else "  なし")
        hot = t[t["本数"] >= min_count].sort_values("再生中央値", ascending=False).head(10)
        print(f"\n--- {market}向け：1本あたりの再生が多い言葉（{min_count}本以上に出てくるもの） ---")
        print(fmt(hot).to_string(index=False) if len(hot) else "  なし")
        frames.append(t.assign(市場=market))

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    path = data_dir(region_slug) / f"keywords_{stamp}.csv"
    pd.concat(frames).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n保存しました: {path}")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="narita", help="regions/ にある地域名")
    parser.add_argument("--top", type=int, default=20, help="上位何語を表示するか")
    parser.add_argument("--field", choices=["title", "title+desc"], default="title+desc",
                        help="title: タイトルだけ / title+desc: タイトル＋説明文の冒頭")
    parser.add_argument("--lang", help="海外向けをこの言語の動画に絞る（例: en, ko, zh, th）")
    parser.add_argument("--min-count", type=int, default=5, help="再生の多い言葉を出すときの最低本数")
    args = parser.parse_args()
    run(args.region, args.top, args.field, args.lang, args.min_count)


if __name__ == "__main__":
    main()
