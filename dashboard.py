"""
分析結果を見るためのダッシュボード（面接のデモ用）

使い方:
  python -m streamlit run dashboard.py
  （PowerShellで仮想環境を有効にできない場合: .venv\\Scripts\\python -m streamlit run dashboard.py）
  → ブラウザで http://localhost:8501 が開く

前提: 各地域で run_pipeline.py と fetch_channel_stats.py を実行済み（data/ にデータがあること）
     ダッシュボード自体はAPIを使わないので、面接中に通信やクォータで止まる心配はない
     地域ごとの違い（比べる地域、戦略のカード、KPI）は regions/<地域>.json の "dashboard" に書く

タブ:
  1. まとめ         … 分析の対象（サイドバーで選ぶ）の課題を、データから計算した数字で1画面に。戦略の方向性と分類の精度
  2. 地域の概要     … 海外向けの割合、集中度HHI、チャンネル依存度、海外で見られているテーマと上位動画
  3. 国内と海外     … テーマ別の比較、空白地帯、主要キーワード（データから抽出）、スポット・特産品の名前
  4. 地域の比較    … 比較表、年別トレンド、HHI、「足りないもの」、発信者の種類（競合のプロモーション手法）
  5. 伸びる要因     … 回帰分析の効果と信頼区間。登録者数をそろえると効果がどう変わるか
  6. コメント       … 海外の視聴者のコメントの言語、いいねの多いコメント
  7. 判定を試す     … タイトルを入れると、言語・観光かどうか・テーマと、その理由が出る
  8. 費用対効果     … クリエイターを招いた場合の見込み再生数、広告と同じ効率になる謝礼の上限、KPI
"""

import math
import re
from datetime import datetime

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

import analyze_engagement_keywords as aek
import analyze_channel_types as act
import analyze_keywords as akw
from analyze_name_presence import has_name, name_patterns
from compare_regions import EXCLUDE_FROM_CONCENTRATION, summarize, view_share
from lang_utils import detect_video_language, is_foreign
from region_config import DATA_ROOT, data_dir, latest, list_regions, load_region
from theme_rules import Classifier, normalize

st.set_page_config(page_title="観光地のUGC分析", page_icon="✈️", layout="wide")

SEA = "#17635a"
STONE = "#9aa5ad"
AMBER = "#b5832f"
SLATE = "#5b7186"
INK_MUTED = "#5d6b75"
POLICY_DAYS = 30  # YouTube API の開発者ポリシー：取得したデータは30日以内に削除か取り直し

LANG_LABEL = {"ja": "日本語", "en": "英語", "ko": "韓国語", "zh-tw": "中国語（繁体字）", "zh-cn": "中国語（簡体字）",
              "zh-hk": "広東語", "zh": "中国語", "th": "タイ語", "es": "スペイン語", "id": "インドネシア語",
              "pt": "ポルトガル語", "fr": "フランス語", "de": "ドイツ語", "vi": "ベトナム語", "hi": "ヒンディー語"}
SOURCE_LABEL = {
    "title_bilingual": "日本語と英語が混ざったタイトルで、英語が主",
    "title_script": "タイトルの文字の種類（かな・ハングルなど）",
    "han_rule": "漢字だけのタイトル。中国語特有の字や、繁体字・簡体字の字形で判定",
    "description_script": "タイトルでは決まらず、説明文にかながあるため日本語",
    "youtube_setting": "投稿者がYouTubeで設定した言語",
    "english_words": "英語特有の語（the、to など）がタイトルにある",
    "langdetect_title": "言語判定ライブラリでタイトルが英語と判定",
    "langdetect": "言語判定ライブラリ",
    "unresolved": "漢字だけで、中国語か日本語か判別できない",
}
# 例：（タイトル, 説明文, 判定に使う地域）
EXAMPLES = {
    "（例を選ぶ）": ("", "", None),
    "成田の乗り継ぎ観光": ("12 Hour Layover in Narita: Naritasan Temple & Eel", "", "narita"),
    "成田空港から東京への案内（空港）": ("Narita Airport to Tokyo - Complete Japan Travel Guide", "", "narita"),
    "表参道のうなぎ（グルメ）": ("上うなぎ重 成田 川豊！！成田山新勝寺の門前町にある行列店", "", "narita"),
    "タイの家族チャンネル（ノイズ）": ("เก่งจัง‼️น้องนาริตะ เรียนเต้น‼️ #ผู้ใหญ่บ้านฟินแลนด์", "", "narita"),
    "人名の成田（ノイズ）": ("成田悠輔が語る日本の未来", "", "narita"),
    "羽田の乗り継ぎと川崎大師": ("Haneda layover: Kawasaki Daishi & kuzumochi", "", "kawasaki_daishi"),
    "台湾の銚子電鉄の動画": ("連年虧損,全部是被淘汰的三手車,靠賣煎餅支撐了80%收入的千葉縣最東的地方鐵道--銚子電鐵", "", "choshi"),
    "英語の車掌さんの密着動画": ("Day in the Life of a Japanese Train Conductor", "", "choshi"),
    "アマガミの聖地巡礼": ("アマガミSS 聖地巡礼 銚子 君ヶ浜", "", "choshi"),
    "津波速報（ノイズ）": ("【津波速報】千葉県銚子市 カムチャツカ半島沖地震", "", "choshi"),
    "日本語と英語のタイトル": ("夕焼けの江ノ電 🌇 A Sunset Moment on the Enoden | Kamakura", "", "enoshima_kamakura"),
    "鎌倉の1日旅行プラン": ("【鎌倉観光】鎌倉を100%満喫するおすすめ1日旅行プランを紹介！", "", "enoshima_kamakura"),
    "ガルパンの聖地巡礼": ("Anime Pilgrimage to Oarai, Ibaraki", "", "oarai"),
    "ガルパンの劇中歌（ファン向け）": ("Girls und Panzer Official Hymn | Ooarai High School", "", "oarai"),
}


# ===================== データの読み込み =====================

def stamp_date(path):
    m = re.search(r"(\d{8})_\d{4}", path.name) if path else None
    return datetime.strptime(m.group(1), "%Y%m%d").date() if m else None


@st.cache_data(show_spinner="データを読み込んでいます…")
def load_all():
    data = {}
    for slug in list_regions():
        if latest(slug, "videos_check_*.csv") is None:
            continue  # 設定ファイルだけ作って、まだデータを集めていない地域は飛ばす
        region, summary, tourism = summarize(slug)
        comment_csv = latest(slug, "comments_*.csv")
        comments = pd.read_csv(comment_csv) if comment_csv else None
        stats_path = data_dir(slug) / "channel_stats.csv"
        if stats_path.exists():
            stats = pd.read_csv(stats_path)[["video_id", "subscribers"]]
            tourism = tourism.merge(stats, on="video_id", how="left")
        tourism["year"] = pd.to_datetime(tourism["published_at"], utc=True, errors="coerce").dt.year
        data[slug] = {"name": region["name"].split("（")[0], "full": region["name"], "summary": summary,
                      "tourism": tourism, "comments": comments, "config": region,
                      "collected": stamp_date(latest(slug, "videos_check_*.csv"))}
    return data


@st.cache_data(show_spinner="言葉を数えています…")
def keyword_tables(slug, field, foreign_lang):
    """主要キーワード：国内向け・海外向けそれぞれの言葉の表"""
    d = load_all()[slug]
    return akw.market_keywords(d["tourism"], d["config"], field, foreign_lang)


@st.cache_data
def load_validation():
    """最初の検証（検証を作った時点の分類）での精度。なければ None"""
    files = sorted((DATA_ROOT / "validation").glob("result_[0-9]*.csv"))
    if not files:
        return None
    r = pd.read_csv(files[-1])
    r["項目"] = r["項目"].str.strip("　 ")
    out = dict(zip(r["項目"], r["正解率"]))
    row = r[r["項目"] == "観光/観光以外の判定（全体）"]
    out["__n__"] = int(row["本数"].iloc[0]) if len(row) else None
    return out


@st.cache_resource(show_spinner="回帰分析のモデルを作っています…")
def fit_models(base_slug, regions, base_theme=aek.BASE_THEME):
    """海外・国内それぞれで、登録者数なし／ありのモデルを作る（サイドバーで選んだ地域だけで）
    base_theme: テーマの基準（1倍とするテーマ）。基準を変えても、テーマどうしの差は変わらない"""
    data = {k: v for k, v in load_all().items() if k in regions}
    frames = [d["tourism"].assign(region=d["name"]) for d in data.values()]
    df = aek.prepare(frames, data[base_slug]["name"])
    has_subs = df["log_subs"].notna().sum() > 50
    models = {}
    for market, with_lang in [("海外", True), ("国内", False)]:
        d = df[df["market"] == market]
        models[(market, False)] = aek.fit(d, "log_views", with_lang, base_theme=base_theme)
        if has_subs:
            models[(market, True)] = aek.fit(d, "log_views", with_lang, with_subs=True, base_theme=base_theme)
            # 比較用：登録者数が分かる同じ動画だけで、登録者数を入れないモデル
            models[(market, "same_rows")] = aek.fit(d, "log_views", with_lang, subs_rows_only=True,
                                                    base_theme=base_theme)
    return models


# ===================== 計算の小道具 =====================

def foreign_of(t):
    return t[t["market"] == "海外"]


def pct(x):
    return "-" if pd.isna(x) else f"{x:.0%}"


def num(x):
    return "-" if pd.isna(x) else f"{int(round(x)):,}"


def top3_share(fo):
    ch = fo.groupby("channel_title")["view_count"].sum().sort_values(ascending=False)
    return ch.iloc[:3].sum() / ch.sum() if ch.sum() else np.nan


def foreign_share_since(t, year):
    d = t[t["year"] >= year]
    return (d["market"] == "海外").mean() if len(d) else np.nan


def name_view_share(fo, cfg):
    """海外向けの再生のうち、タイトルに町の名前がある動画が占める割合"""
    if fo.empty:
        return np.nan, np.nan
    named = has_name(fo["title"], name_patterns(cfg))
    views = fo["view_count"].fillna(0)
    return (views[named].sum() / views.sum() if views.sum() else np.nan), named.mean()


def spot_mask(fo, cfg, spot_names):
    """タイトルか説明文の冒頭に、指定したスポット・特産品の名前が出てくる動画（どれか1つでも）"""
    spots = {}
    for group in cfg.get("spots", {}).values():
        spots.update(group)
    words = [w for n in spot_names for w in spots.get(n, [])]
    if not words or fo.empty:
        return pd.Series(False, index=fo.index)
    pats = aek._patterns(words)
    text = (fo["title"].fillna("") + " " + fo["description"].fillna("").str[:300]).map(normalize)
    return text.map(lambda x: any(p.search(x) for p in pats))


def airport_share(fo):
    """海外の再生のうち、空港と乗り継ぎの動画が占める割合（そのテーマがない地域は NaN）"""
    if fo.empty or not fo["primary_theme"].isin(["空港", "乗り継ぎ・待ち時間"]).any():
        return np.nan
    v = fo["view_count"].fillna(0)
    return v[fo["primary_theme"].isin(["空港", "乗り継ぎ・待ち時間"])].sum() / v.sum() if v.sum() else np.nan


AIRPORT_KEYWORD_CUES = ["airport", "layover", "空港", "乗り継ぎ", "haneda", "羽田"]


def airport_share_robust(fo):
    """空港・乗り継ぎのキーワード「だけ」で見つかった動画を除いた、空港まわりの割合（集め方の偏りを除く）"""
    if "keywords" not in fo.columns:
        return np.nan
    kws = fo["keywords"].fillna("").map(lambda x: [k for k in x.split("|") if k])
    only_air = kws.map(lambda ks: bool(ks) and all(any(c in k.lower() for c in AIRPORT_KEYWORD_CUES) for k in ks))
    return airport_share(fo[~only_air])


def theme_share(t, market, theme):
    return view_share(t[t["market"] == market], "primary_theme").get(theme, 0.0)


def effects_df(model):
    ci = model.conf_int()
    rows = []
    for name in model.params.index:
        if name == "Intercept":
            continue
        rows.append({"要因": aek.readable(name), "効果": 10 ** model.params[name],
                     "下限": 10 ** ci.loc[name, 0], "上限": 10 ** ci.loc[name, 1], "p値": model.pvalues[name]})
    df = pd.DataFrame(rows)
    df["判定"] = np.where(df["p値"] < 0.05, "偶然とは考えにくい（p<0.05）", "偶然の範囲")
    return df


def bar_share(df, value, label, color=SEA, title=""):
    return (alt.Chart(df).mark_bar(color=color)
            .encode(x=alt.X(f"{value}:Q", axis=alt.Axis(format="%"), title=title),
                    y=alt.Y(f"{label}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200, labelOverlap=False)),
                    tooltip=[label, alt.Tooltip(f"{value}:Q", format=".1%")])
            .properties(height=alt.Step(30)))


def show_video_table(df, n=10):
    t = df.nlargest(n, "view_count")
    table = pd.DataFrame({
        "サムネイル": "https://i.ytimg.com/vi/" + t["video_id"] + "/mqdefault.jpg",
        "タイトル": t["title"],
        "言語": t["lang_final"].map(lambda x: LANG_LABEL.get(x, x)),
        "再生数": t["view_count"].astype(int),
        "動画": "https://www.youtube.com/watch?v=" + t["video_id"],
    })
    st.dataframe(table, hide_index=True, width="stretch", row_height=60, column_config={
        "サムネイル": st.column_config.ImageColumn(width="small"),
        "タイトル": st.column_config.TextColumn(width="large"),
        "言語": st.column_config.TextColumn(width="small"),
        "再生数": st.column_config.NumberColumn(format="localized", width="small"),
        "動画": st.column_config.LinkColumn(display_text="開く", width="small"),
    })


def region_colors(names, highlight):
    others = [n for n in names if n != highlight]
    palette = [AMBER, SLATE, STONE]
    return alt.Scale(domain=[highlight] + others, range=[SEA] + palette[:len(others)])


# ===================== 画面 =====================

data_all = load_all()
all_slugs = list(data_all.keys())

with st.sidebar:
    st.markdown("### 観光地のUGC分析")
    st.caption("YouTubeの観光動画を国内向け・海外向けに分けて分析し、インバウンドを増やす発信戦略を考えるためのダッシュボード")
    default_base = "narita" if "narita" in all_slugs else all_slugs[0]
    base = st.selectbox("分析の対象", all_slugs, index=all_slugs.index(default_base),
                        format_func=lambda s: data_all[s]["full"])
    dash_cfg = data_all[base]["config"].get("dashboard", {})
    default_comp = [k for k in dash_cfg.get("compare_with", []) if k in all_slugs] or [k for k in all_slugs if k != base]
    compare = st.multiselect("比べる地域", [k for k in all_slugs if k != base], default=default_comp,
                             format_func=lambda s: data_all[s]["name"])
    data = {k: data_all[k] for k in [base] + compare}
    slugs = list(data.keys())
    names = {k: v["name"] for k, v in data.items()}
    slug = st.selectbox("詳しく見る地域", slugs, index=0, format_func=lambda s: data[s]["full"])
    st.divider()
    st.caption("データ: YouTube Data API（検索上位のサンプル）。数字は観光関連の動画のみで、ノイズと言語不明を除く。")
    dates = [d["collected"] for d in data.values() if d["collected"]]
    if dates:
        oldest = min(dates)
        days = (datetime.now().date() - oldest).days
        msg = f"データの取得日: {oldest:%Y/%m/%d}（{days}日前）"
        if days >= POLICY_DAYS:
            st.error(msg + "。YouTube APIの規約の30日を過ぎているので、取り直すか削除してください。")
        elif days >= POLICY_DAYS - 7:
            st.warning(msg + f"。規約の30日まであと{POLICY_DAYS - days}日です。")
        else:
            st.caption(msg + "。規約に従い、30日を過ぎる前に取り直すか削除する。")

cur = data[slug]
t = cur["tourism"]
fo = foreign_of(t)
s = cur["summary"]

tabs = st.tabs(["まとめ", "地域の概要", "国内と海外", "地域の比較", "伸びる要因", "コメント", "判定を試す", "費用対効果"])

# ---------- 1. まとめ ----------
with tabs[0]:
    b = data[base]
    bt, bs = b["tourism"], b["summary"]
    others = [k for k in slugs if k != base]
    fo_b = foreign_of(bt)
    hhi_b, top_b, share_b = bs["集中度HHI"], bs["海外_最大テーマ"], bs["最大テーマの再生シェア"]
    top3_b = top3_share(fo_b)
    ko_b = int((fo_b["lang_final"] == "ko").sum())
    ko_o = {k: int((foreign_of(data[k]["tourism"])["lang_final"] == "ko").sum()) for k in others}
    hhi_o = {k: data[k]["summary"]["集中度HHI"] for k in others}
    since = {k: foreign_share_since(data[k]["tourism"], 2023) for k in slugs}
    dom_food, fo_food = theme_share(bt, "国内", "グルメ"), theme_share(bt, "海外", "グルメ")

    air_b = airport_share(fo_b)
    # 見出しも、データが条件を満たすときだけ断定する
    if not pd.isna(air_b) and air_b >= 0.5:
        st.subheader(f"{b['name']}は、海外では「空港」の町としてしか見られていない")
    elif share_b >= 0.7:
        st.subheader(f"{b['name']}は、海外から「{top_b}」の町としてしか見られていない")
    else:
        st.subheader(f"{b['name']}は、海外からどう見られているか")
    st.caption("数字はすべて分析したデータから自動で計算し、「突出している」などの判断の言葉も、"
               "データが条件を満たすときだけ表示しています（データを取り直して数字が変われば、文章も変わります）。")

    c = st.columns(4)
    air_rb = airport_share_robust(fo_b)
    if not pd.isna(air_b):
        c[0].metric("空港まわりの割合", pct(air_rb if not pd.isna(air_rb) else air_b),
                    help=f"海外の再生のうち、空港と乗り継ぎの動画の割合。空港・乗り継ぎのキーワードだけで見つかった動画を除いた値（除かなければ{pct(air_b)}）")
    else:
        c[0].metric("海外の再生の集中度HHI", f"{hhi_b:.2f}", help="海外の再生が1つのテーマに集中しているほど1.0に近い")
    if not pd.isna(air_b):
        c[1].metric(f"空港を除いた最大：{top_b}", pct(share_b),
                    help="空港・乗り継ぎ・旅行全般・未分類を除いたテーマの中での割合（海外の再生全体の割合ではない）")
    else:
        c[1].metric(f"海外で最大のテーマ（{top_b}）", pct(share_b), help="旅行全般・未分類を除いたテーマの中で")
    c[2].metric("上位3チャンネルへの依存", pct(top3_b), help="海外の再生のうち、上位3チャンネルの動画が占める割合")
    c[3].metric("海外向けのチャンネル数", f"{fo_b['channel_title'].nunique()}個", help="海外向けの動画を作ったチャンネルの数")

    lines = []
    if not pd.isna(air_b) and air_b >= 0.5:
        robust = f"（空港のキーワードだけで見つかった動画を除いても{pct(air_rb)}）" if not pd.isna(air_rb) else ""
        lines.append(f"**海外では「空港」の町**：海外の再生の{pct(air_b)}が、空港と乗り継ぎの動画{robust}。"
                     "町の観光の動画は、残りの一部にとどまる。")
    hl = dash_cfg.get("highlight_spots", [])
    if hl:
        parts = []
        for n in hl:
            m = spot_mask(fo_b, b["config"], [n])
            if m.sum():
                parts.append(f"{n} {num(fo_b.loc[m, 'view_count'].median())}回（{int(m.sum())}本）")
        if parts:
            lines.append("**資源ごとの届き方（海外向けの動画1本あたりの再生の中央値）**：" + "、".join(parts) + "。")
    if others:
        comp = "、".join(f"{names[k]} {v:.2f}" for k, v in hhi_o.items())
        scope = "空港・乗り継ぎ・旅行全般を除いたテーマの中では、" if not pd.isna(air_b) else "海外の再生の"
        if not pd.isna(air_b):
            lines.append(f"**町の動画の中身**：{scope}{top_b}が{pct(share_b)}。集中度HHIは{hhi_b:.2f}（{comp}）。")
        elif hhi_b > max(hhi_o.values()):
            lines.append(f"**入口が1つに偏っている**：海外の再生の{pct(share_b)}が{top_b}。"
                         f"集中度HHIは{hhi_b:.2f}で、比較した地域（{comp}）の中で最も高い。")
        else:
            lines.append(f"**入口の偏り**：海外の再生の{pct(share_b)}が{top_b}。集中度HHIは{hhi_b:.2f}（{comp}）。")
    if top3_b >= 0.6:
        lines.append(f"**一部の作り手に頼っている**：海外の再生の{pct(top3_b)}が、上位3チャンネルの動画。"
                     "取り上げてくれたクリエイターがいなくなれば、海外での存在感も消える。")
    else:
        lines.append(f"**作り手への依存度**：海外の再生のうち上位3チャンネルの動画は{pct(top3_b)}。")
    town_b = fo_b[~fo_b["primary_theme"].isin(["空港", "乗り継ぎ・待ち時間"])] if not pd.isna(air_b) else fo_b
    nv, nc = name_view_share(town_b, b["config"])
    if not pd.isna(air_b):
        nv = np.nan  # 空港の町では、タイトルの名前の多くが「空港」の名前なので、この指標は使わない
    if not pd.isna(nv):
        if nv < 0.5:
            lines.append(f"**物語は届いても、町の名前が残っていない**：海外の再生のうち、タイトルに{b['name']}の名前がある動画は"
                         f"{pct(nv)}だけ（本数では{pct(nc)}）。見た人は、それがどこの話かを覚えていない可能性が高い。")
        else:
            lines.append(f"**町の名前の届き方**：海外の再生のうち、タイトルに{b['name']}の名前がある動画は{pct(nv)}（本数では{pct(nc)}）。")
    food_fo = fo_b[fo_b["primary_theme"] == "グルメ"]
    food_med, all_med = food_fo["view_count"].median(), fo_b["view_count"].median()
    if dom_food >= 0.2 and fo_food < dom_food * 0.5 and len(food_fo) >= 5 and food_med >= all_med:
        lines.append(f"**グルメは、海外では本数が少ないだけ**：国内の再生の{pct(dom_food)}を占めるグルメが、海外では{pct(fo_food)}。"
                     f"ただし海外のグルメの動画は1本あたり{num(food_med)}回と、海外向け全体の{num(all_med)}回よりよく見られている。")
    elif dom_food >= 0.2 and fo_food < dom_food * 0.5:
        lines.append(f"**国内で強いグルメが、海外に届いていない**：国内の再生の{pct(dom_food)}を占めるグルメが、"
                     f"海外では{pct(fo_food)}。")
    else:
        lines.append(f"**グルメの届き方**：国内の再生の{pct(dom_food)}、海外の再生の{pct(fo_food)}。")
    if others:
        comp = "、".join(f"{names[k]} {pct(since[k])}" for k in others)
        if since[base] < min(since[k] for k in others):
            lines.append(f"**訪日客回復の波に乗れていない**：2023年以降に投稿された観光動画のうち海外向けは"
                         f"{pct(since[base])}で、比較した地域（{comp}）より低い。")
        else:
            lines.append(f"**2023年以降の海外向けの割合**：{pct(since[base])}（{comp}）。")
        comp = "、".join(f"{names[k]} {v}本" for k, v in ko_o.items())
        if ko_b < 0.5 * min(ko_o.values()):
            lines.append(f"**韓国がほぼ手つかず**：海外向けの韓国語の動画は{ko_b}本で、比較した地域（{comp}）の半分にも満たない。")
        else:
            lines.append(f"**韓国語の動画**：{ko_b}本（{comp}）。")
    st.markdown("#### 分かったこと")
    for ln in lines:
        st.markdown(f"- {ln}")

    if dash_cfg.get("cards"):
        st.markdown("#### " + dash_cfg.get("strategy_title", "戦略の方向性"))
        cols = st.columns(len(dash_cfg["cards"]))
        cards = dash_cfg["cards"]
        for col, (head, title, body) in zip(cols, cards):
            with col:
                with st.container(border=True):
                    st.caption(head)
                    st.markdown(f"**{title}**")
                    st.caption(body)

    v = load_validation()
    if v:
        n_v = v.get("__n__")
        st.markdown(f"#### 分類の精度（人の目で検証した{n_v or '-'}本、分類の結果を見せずに判定）")
        cols = st.columns(3)
        cols[0].metric("観光かどうかの判定", v.get("観光/観光以外の判定（全体）", "-"))
        cols[1].metric("国内／海外の判定", v.get("国内/海外の判定（分類が「不明」以外）", "-"))
        cols[2].metric("テーマの判定", v.get("テーマ（厳密：主テーマが一致）", "-"))
        st.caption("最新の検証の結果（data/validation）。テーマの誤りの多くは、似たテーマの境目（空港と乗り継ぎなど）のあいまいさ。")

# ---------- 2. 地域の概要 ----------
with tabs[1]:
    st.subheader(f"{cur['full']}：海外からどう見られているか")
    c = st.columns(5)
    c[0].metric("観光関連の動画", f"{s['観光関連の動画']:,}本")
    c[1].metric("海外向けの割合", pct(s["海外向けの割合"]))
    c[2].metric("海外向けの再生中央値", f"{num(s['海外_再生中央値'])}回")
    c[3].metric("集中度HHI", f"{s['集中度HHI']:.2f}",
                help="海外の再生が1つのテーマに集中しているほど1.0に近い（未分類・旅行全般は除く）")
    c[4].metric("上位3チャンネルへの依存", pct(top3_share(fo)),
                help="海外の再生のうち、上位3チャンネルの動画が占める割合")
    st.markdown(f"海外で最も見られているテーマは **{s['海外_最大テーマ']}**（海外の再生の{pct(s['最大テーマの再生シェア'])}）")

    left, right = st.columns([2, 3])
    with left:
        st.markdown("**海外向けの再生構成比（テーマ別）**")
        share = view_share(fo, "primary_theme").reset_index()
        share.columns = ["テーマ", "再生シェア"]
        st.altair_chart(bar_share(share, "再生シェア", "テーマ"), width="stretch")
    with right:
        st.markdown("**海外で見られている動画 トップ10**")
        show_video_table(fo)

# ---------- 3. 国内と海外 ----------
with tabs[2]:
    st.subheader("テーマ別：国内と海外で何が見られているか")
    rows = []
    for market, d in t.groupby("market"):
        for theme, v in view_share(d, "primary_theme").items():
            rows.append({"テーマ": theme, "市場": market, "再生シェア": v})
    comp = pd.DataFrame(rows)
    order = comp[comp["市場"] == "国内"].sort_values("再生シェア", ascending=False)["テーマ"].tolist()
    order += [x for x in comp["テーマ"].unique() if x not in order]
    xmax = float(comp["再生シェア"].max()) if len(comp) else 1.0
    left, right = st.columns(2)
    for col, market, color in [(left, "国内", STONE), (right, "海外", SEA)]:
        with col:
            st.markdown(f"**{market}向けの再生に占める割合**")
            d = comp[comp["市場"] == market]
            st.altair_chart(alt.Chart(d).mark_bar(color=color)
                            .encode(x=alt.X("再生シェア:Q", axis=alt.Axis(format="%"), title=None,
                                            scale=alt.Scale(domain=[0, xmax])),
                                    y=alt.Y("テーマ:N", sort=order, title=None,
                                            axis=alt.Axis(labelLimit=200, labelOverlap=False)),
                                    tooltip=["テーマ", alt.Tooltip("再生シェア:Q", format=".1%")])
                            .properties(height=alt.Step(30)), width="stretch")
    st.caption("左右で横軸の目盛りをそろえている。同じテーマを左右で見比べると、国内と海外のずれが分かる。")

    pivot = comp.pivot(index="テーマ", columns="市場", values="再生シェア").fillna(0)
    if {"国内", "海外"} <= set(pivot.columns):
        gap = pivot[(pivot["国内"] >= 0.05) & (pivot["海外"] < pivot["国内"] * 0.5)]
        gap = gap.drop(EXCLUDE_FROM_CONCENTRATION, errors="ignore").sort_values("国内", ascending=False)
        st.markdown("**空白地帯：国内ではよく見られているのに、海外にはほとんど届いていないテーマ**")
        if gap.empty:
            st.caption("該当なし")
        for theme, r in gap.iterrows():
            st.info(f"**{theme}**　国内の再生の{r['国内']:.0%} ／ 海外の再生の{r['海外']:.0%}")

    st.divider()
    st.markdown("#### 主要キーワード：タイトルと説明文によく出てくる言葉（データから自動で抽出）")
    st.caption("決めた名前のリストではなく、動画に実際に出てくる言葉を数える。1本に何回出ても1回。"
               "地域の名前そのものと、旅行・日本・Vlogのような一般的な語、説明文の定型文は除く。")
    kc1, kc2, kc3 = st.columns(3)
    kw_field = kc1.radio("数える範囲", ["タイトル＋説明文の冒頭", "タイトルだけ"], horizontal=True, key="kw_field")
    kw_order = kc2.radio("並べ方", ["本数が多い順", "1本あたりの再生が多い順（5本以上）"], horizontal=True, key="kw_order")
    kw_langs = {"すべて": None, "英語": "en", "韓国語": "ko", "中国語": "zh", "タイ語": "th"}
    kw_lang = kc3.selectbox("海外向けの言語", list(kw_langs), key="kw_lang")
    kw_tables = keyword_tables(slug, "title" if kw_field == "タイトルだけ" else "title+desc", kw_langs[kw_lang])
    by_views = kw_order.startswith("1本")
    kw_x = "再生中央値" if by_views else "本数"
    kcols = st.columns(2)
    for col, market, color in [(kcols[0], "国内", STONE), (kcols[1], "海外", SEA)]:
        with col:
            kt = kw_tables[market]
            if by_views:
                kt = kt[kt["本数"] >= 5].sort_values("再生中央値", ascending=False)
            kt = kt.head(15)
            note = f"（{kw_lang}の動画）" if market == "海外" and kw_lang != "すべて" else ""
            st.markdown(f"**{market}向け{note}**")
            if kt.empty:
                st.caption("該当なし")
                continue
            st.altair_chart(alt.Chart(kt).mark_bar(color=color)
                            .encode(x=alt.X(f"{kw_x}:Q", title=kw_x),
                                    y=alt.Y("言葉:N", sort="-x", title=None,
                                            axis=alt.Axis(labelLimit=220, labelOverlap=False)),
                                    tooltip=["言葉", "本数", alt.Tooltip("割合:Q", format=".0%"),
                                             alt.Tooltip("再生合計:Q", format=","),
                                             alt.Tooltip("再生中央値:Q", format=","),
                                             alt.Tooltip("エンゲージメント率:Q", format=".1%")])
                            .properties(height=alt.Step(26)), width="stretch")
            with st.expander("表で見る"):
                st.dataframe(akw.fmt(kt), hide_index=True, width="stretch")
    st.caption("国内向けは日本語、海外向けは主に英語なので、同じ言葉が左右に並ぶことは少ない。市場ごとに何が話題かを見るためのもの。"
               "検索キーワードに入れた言葉は多く出やすい。エンゲージメント率 =（いいね＋コメント）÷再生数（再生100回以上の動画）。")

    spots = cur["config"].get("spots")
    if spots:
        st.divider()
        st.markdown("**設定したスポット・特産品の名前が出てくる動画（タイトル＋説明文の冒頭）**")
        kt = aek.keyword_table(t, spots)
        kt = kt.assign(**{"海外の割合": kt["海外の割合"].map(pct),
                          "海外_再生合計": kt["海外_再生合計"].map(num),
                          "国内_再生合計": kt["国内_再生合計"].map(num),
                          "海外_再生中央値": kt["海外_再生中央値"].map(num)})
        st.dataframe(kt, hide_index=True, width="stretch")

# ---------- 4. 3地域の比較 ----------
with tabs[3]:
    st.subheader("地域の比較")
    overview = pd.DataFrame({names[k]: v["summary"] for k, v in data.items()})
    shown = overview.copy()
    for row in ["海外向けの割合", "最大テーマの再生シェア", "言語不要動画_再生シェア"]:
        shown.loc[row] = shown.loc[row].map(pct)
    for row in ["海外_再生中央値", "国内_再生中央値", "言語不要動画_再生中央値"]:
        shown.loc[row] = shown.loc[row].map(num)
    shown.loc["集中度HHI"] = overview.loc["集中度HHI"].map(lambda x: f"{x:.2f}")
    # 同じ列に数字と「30%」のような文字が混ざると表示用の変換で警告が出るので、すべて文字にそろえる
    shown = shown.astype(str)
    st.dataframe(shown, width="stretch", height=36 * (len(shown) + 1) + 4)

    st.markdown("**投稿年ごとの海外向けの割合**（訪日客が戻った2023年以降の動き）")
    trend = []
    for k, v in data.items():
        g = v["tourism"].dropna(subset=["year"]).groupby("year")
        for y, d in g:
            if y >= 2019 and len(d) >= 5:
                trend.append({"地域": v["name"], "年": int(y), "海外向けの割合": (d["market"] == "海外").mean(),
                              "本数": len(d)})
    if trend:
        tr = pd.DataFrame(trend)
        scale = region_colors(list(names.values()), names[base])
        st.altair_chart(alt.Chart(tr).mark_line(point=True, strokeWidth=3)
                        .encode(x=alt.X("年:O", title=None, axis=alt.Axis(labelAngle=0)),
                                y=alt.Y("海外向けの割合:Q", axis=alt.Axis(format="%"), title=None),
                                color=alt.Color("地域:N", scale=scale, legend=alt.Legend(orient="top", title=None)),
                                tooltip=["地域", "年", alt.Tooltip("海外向けの割合:Q", format=".0%"), "本数"])
                        .properties(height=280), width="stretch")
        st.caption("検索上位のサンプルでの推移で、今年は途中まで。本数が5本未満の年は表示していない。")

    c = st.columns(3)
    kpi = pd.DataFrame({
        "地域": list(names.values()),
        "集中度HHI": [v["summary"]["集中度HHI"] for v in data.values()],
        "海外の再生中央値": [v["summary"]["海外_再生中央値"] for v in data.values()],
        "韓国語の動画": [int((foreign_of(v["tourism"])["lang_final"] == "ko").sum()) for v in data.values()],
    })
    for col, (field, title) in zip(c, [("集中度HHI", "海外の再生の集中度（低いほど入口が分散）"),
                                        ("海外の再生中央値", "海外向け動画の再生中央値（回）"),
                                        ("韓国語の動画", "海外向けの韓国語の動画（本）")]):
        with col:
            st.markdown(f"**{title}**")
            st.altair_chart(alt.Chart(kpi).mark_bar()
                            .encode(x=alt.X("地域:N", title=None, sort=None, axis=alt.Axis(labelAngle=0)),
                                    y=alt.Y(f"{field}:Q", title=None),
                                    color=alt.Color("地域:N", scale=region_colors(list(names.values()), names[base]),
                                                    legend=None),
                                    tooltip=["地域", field])
                            .properties(height=220), width="stretch")

    st.markdown(f"**{cur['name']}に足りないもの**（他の地域では海外の再生の10%以上を占めるのに、{cur['name']}では2%未満のテーマ）")
    fo_theme = pd.DataFrame({names[k]: view_share(foreign_of(v["tourism"]), "primary_theme")
                             for k, v in data.items()}).fillna(0)
    found = False
    for theme, row in fo_theme.iterrows():
        if theme in EXCLUDE_FROM_CONCENTRATION:
            continue
        oth = {n: row[n] for n in names.values() if n != cur["name"] and row[n] >= 0.10}
        if oth and row[cur["name"]] < 0.02:
            found = True
            detail = "、".join(f"{n} {v:.0%}" for n, v in oth.items())
            st.warning(f"**{theme}**　{cur['name']} {row[cur['name']]:.0%} ／ {detail}")
    if not found:
        st.caption("該当なし")

    st.divider()
    st.markdown("#### 競合のプロモーション手法：誰が発信しているか（海外向けの動画）")
    st.caption("チャンネル名で、公式（自治体・観光協会など）、メディア（テレビ・新聞）、企業・施設（空港・鉄道・ホテル・寺社など）、"
               "個人の作り手に分けた。検索の上位に出てこない公式の発信は数に入らない（出てこないこと自体が「届いていない」という結果）。")
    ov = act.region_overview({names[k]: (v["tourism"], v["config"]) for k, v in data.items()}, "海外")
    share_cols = [f"{kind}_再生シェア" for kind in act.TYPES]
    long = ov.melt(id_vars=["地域"], value_vars=share_cols, var_name="発信者", value_name="再生シェア")
    long["発信者"] = long["発信者"].str.replace("_再生シェア", "", regex=False)
    st.altair_chart(alt.Chart(long).mark_bar()
                    .encode(y=alt.Y("地域:N", sort=list(names.values()), title=None),
                            x=alt.X("再生シェア:Q", stack="normalize", axis=alt.Axis(format="%"), title="海外向けの再生のシェア"),
                            color=alt.Color("発信者:N", scale=alt.Scale(domain=act.TYPES, range=[AMBER, SLATE, STONE, SEA]),
                                            legend=alt.Legend(orient="top", title=None)),
                            order=alt.Order("発信者:N"),
                            tooltip=["地域", "発信者", alt.Tooltip("再生シェア:Q", format=".1%")])
                    .properties(height=alt.Step(34)), width="stretch")
    shown_ov = ov.copy()
    for col in share_cols:
        shown_ov[col] = shown_ov[col].map(pct)
    st.dataframe(shown_ov.astype(str), hide_index=True, width="stretch")
    with st.expander("公式と判定したチャンネルを見る（判定の確認用）"):
        for k, v in data.items():
            cl = act.channel_list(v["tourism"], v["config"], "公式")
            st.markdown(f"**{names[k]}**")
            if cl.empty:
                st.caption("なし（検索の上位に出てこない）")
            else:
                st.dataframe(cl.assign(再生合計=cl["再生合計"].map(num)), hide_index=True, width="stretch")
        st.caption("チャンネル名だけで判定しているので、間違いもある。regions/<地域>.json に "
                   "\"channel_types\": {\"チャンネル名\": \"公式\"} と書くと種類を指定できる。")

# ---------- 5. 伸びる要因 ----------
with tabs[4]:
    st.subheader("何が再生数に効いているか（重回帰分析）")
    theme_bases = {"歴史・寺社（成田山のような寺＝1）": "歴史・寺社", "絶景・自然（景色の動画＝1）": aek.BASE_THEME}
    base_label = st.radio("テーマの基準（1倍とするテーマ）", list(theme_bases), horizontal=True,
                          help="基準を変えても、テーマどうしの差（グルメと寺の差など）は同じ。どのテーマを1として見るかだけが変わる")
    models = fit_models(base, tuple(slugs), theme_bases[base_label])
    c = st.columns(2)
    market = c[0].radio("市場", ["海外", "国内"], horizontal=True)
    can_subs = (market, True) in models
    with_subs = c[1].toggle("登録者数をそろえる", value=False, disabled=not can_subs,
                            help="チャンネル登録者数をモデルに加え、人気のチャンネルが作ったことによる効果を取り除く")
    model, md = models[(market, with_subs and can_subs)]
    used_base = md["theme"].cat.categories[0]
    st.caption("地域・テーマ・動画の形式・言語・経過日数を同時にモデルに入れ、他の条件をそろえたときに、"
               "それぞれの要素が再生数を何倍にするかを推定しています。点が効果、線が95%信頼区間で、"
               f"線が1倍の点線をまたいでいれば偶然の範囲です。基準はテーマ「{used_base}」、地域「{names[base]}」"
               + ("、言語「英語」。" if market == "海外" else "。"))
    ef = effects_df(model)
    hide_region = st.checkbox("地域の効果を表示しない（テーマと形式に集中する）", value=True)
    if hide_region:
        ef = ef[~ef["要因"].str.startswith("地域")]
    ef = ef.sort_values("効果", ascending=False)
    yenc = alt.Y("要因:N", sort=list(ef["要因"]), title=None, axis=alt.Axis(labelLimit=260, labelOverlap=False))
    color = alt.Color("判定:N", scale=alt.Scale(domain=["偶然とは考えにくい（p<0.05）", "偶然の範囲"], range=[SEA, STONE]),
                      legend=alt.Legend(orient="top", title=None, labelLimit=300))
    xscale = alt.Scale(type="log", domain=[max(0.05, float(ef["下限"].min()) * 0.9), float(ef["上限"].max()) * 1.1])
    rules = alt.Chart(ef).mark_rule(strokeWidth=2).encode(
        y=yenc, x=alt.X("下限:Q", scale=xscale, title="効果（倍、対数目盛り）"), x2="上限:Q", color=color)
    points = alt.Chart(ef).mark_circle(size=90, opacity=1).encode(
        y=yenc, x=alt.X("効果:Q", scale=xscale), color=color,
        tooltip=["要因", alt.Tooltip("効果:Q", format=".2f"), alt.Tooltip("下限:Q", format=".2f"),
                 alt.Tooltip("上限:Q", format=".2f"), alt.Tooltip("p値:Q", format=".3f")])
    one = alt.Chart(pd.DataFrame({"x": [1.0]})).mark_rule(strokeDash=[4, 4], color=INK_MUTED).encode(x="x:Q")
    st.altair_chart((rules + points + one).properties(height=28 * len(ef) + 40), width="stretch")
    st.caption(f"n={int(model.nobs)}本、決定係数R²={model.rsquared:.2f}。相関の分析であり、因果までは言えない。")
    if can_subs:
        # 登録者数をそろえる前後で、テーマの効果がどう変わるかを、同じ動画（登録者数が分かるもの）で比べる
        e0 = effects_df(models[(market, "same_rows")][0]).set_index("要因")
        e1 = effects_df(models[(market, True)][0]).set_index("要因")
        theme_rows = [r for r in e0.index if r.startswith("テーマ") and r in e1.index]
        shrunk = [(r, e0.loc[r, "効果"], e1.loc[r, "効果"], e0.loc[r, "p値"] < 0.05, e1.loc[r, "p値"] < 0.05)
                  for r in theme_rows]
        lost = [x for x in shrunk if x[3] and not x[4]]
        kept = [x for x in shrunk if x[3] and x[4]]
        subs_eff = e1.loc["登録者数（10倍になると）", "効果"] if "登録者数（10倍になると）" in e1.index else None
        msg = [f"登録者数をそろえる前と後の比較（{market}向け、登録者数が分かる動画で計算）："]
        if subs_eff:
            msg.append(f"登録者数が10倍になると、再生数は約{subs_eff:.1f}倍。")
        if lost:
            msg.append("そろえると偶然の範囲に縮むテーマ：" +
                       "、".join(f"{r.replace('テーマ: ', '')}（×{a:.1f}→×{b2:.1f}）" for r, a, b2, _, _ in lost) + "。")
        if kept:
            msg.append("そろえても残るテーマ：" +
                       "、".join(f"{r.replace('テーマ: ', '')}（×{a:.1f}→×{b2:.1f}）" for r, a, b2, _, _ in kept) + "。")
        st.info(" ".join(msg))

# ---------- 6. コメント ----------
with tabs[5]:
    st.subheader("海外の視聴者は、どの言語で、何に反応しているか")
    cm = cur["comments"]
    if cm is None:
        st.caption("コメントのデータがありません。fetch_comments.py を実行してください。")
    else:
        fc = cm[cm["group"] == "海外向け"]
        langs = fc[fc["comment_lang"] != "unknown"]["comment_lang"].map(lambda x: LANG_LABEL.get(x, x))
        dist = langs.value_counts(normalize=True).head(8).reset_index()
        dist.columns = ["言語", "割合"]
        left, right = st.columns([2, 3])
        with left:
            st.markdown("**海外向け動画のコメントの言語**")
            st.altair_chart(bar_share(dist, "割合", "言語"), width="stretch")
            st.caption("YouTubeでは視聴者の国籍は投稿者しか見られないため、コメントの言語を視聴者層の代理指標にしている")
        with right:
            st.markdown("**いいねが多い、日本語以外のコメント**")
            top = fc[fc["comment_lang"].map(is_foreign)].nlargest(10, "like_count")
            for _, r in top.iterrows():
                st.markdown(f"> {str(r['text'])[:220]}  \n👍 {int(r['like_count']):,}　｜　"
                            f"{LANG_LABEL.get(r['comment_lang'], r['comment_lang'])}　｜　{str(r['video_title'])[:40]}")

# ---------- 7. 判定を試す ----------
with tabs[6]:
    st.subheader("動画のタイトルから、言語・観光かどうか・テーマを判定する")
    st.caption("分析で使っているのと同じルールで判定し、その理由も表示します。")
    ex = st.selectbox("例から選ぶ", list(EXAMPLES.keys()))
    ex_title, ex_desc, ex_region = EXAMPLES[ex]
    test_default = slugs.index(ex_region) if ex_region in slugs else slugs.index(slug)
    region_for_test = st.selectbox("どの地域のルールで判定するか（何がノイズかは地域によって違う）", slugs,
                                   index=test_default, format_func=lambda x: data[x]["full"])
    title = st.text_input("タイトル", value=ex_title)
    desc = st.text_area("説明文（任意）", value=ex_desc, height=80)
    if title:
        clf = Classifier(load_region(region_for_test))
        lang, source = detect_video_language(title, desc)
        mk = "国内" if lang == "ja" else ("海外" if is_foreign(lang) else "不明")
        result = clf.classify(title, desc)
        c = st.columns(3)
        c[0].metric("言語", LANG_LABEL.get(lang, lang))
        c[1].metric("国内／海外", mk)
        c[2].metric("判定", ("観光以外（" if result["is_noise"] else "観光（") + result["primary_theme"] + "）")
        st.markdown(f"**言語の判定の根拠**：{SOURCE_LABEL.get(source, source)}")
        if result["matched"]:
            st.markdown("**テーマの判定で一致したキーワード**：" + "、".join(result["matched"]))
        if len(result["themes"]) > 1:
            st.markdown("**ほかに当てはまったテーマ**：" + "、".join(result["themes"][1:]))
        fmts = clf.formats(title)
        if fmts:
            st.markdown("**動画の形式**：" + "、".join(fmts))

# ---------- 8. 費用対効果 ----------
with tabs[7]:
    st.subheader("クリエイターを招くと、どれくらい見られるか（広告との比較）")
    models = fit_models(base, tuple(slugs))
    if ("海外", True) not in models:
        st.caption("チャンネル登録者数のデータがありません。fetch_channel_stats.py を実行してください。")
    else:
        model, md = models[("海外", True)]
        # 対数のモデルの予測値は「典型的な値（中央値に近い）」。再生数はたまに大きくバズる偏った分布なので、
        # 何人も招く施策の費用対効果は、バズも含めた平均（期待値）で考える。
        # 誤差が対数正規分布に従うとすると、期待値 = 典型値 × 10^(ln(10) × 残差分散 / 2)
        boost = 10 ** (math.log(10) * model.scale / 2)
        st.caption(
            f"分析で作った回帰モデル（海外向け動画 {int(model.nobs)}本、R²={model.rsquared:.2f}）で、" + names[base] + "の動画を、"
            "登録者数の違うクリエイターが作った場合の再生数を予測します。相関にもとづく予測で、動画の出来によって大きくぶれます。"
            f"再生数は「多くは少ししか見られず、たまに大きくバズる」偏った分布なので、典型的な値とバズも含めた平均（期待値）を分け、"
            f"費用対効果は期待値で計算しています（このモデルでは期待値が典型値の約{boost:.1f}倍）。")

        themes = list(md["theme"].cat.categories)
        langs = list(md["lang_group"].cat.categories)
        c = st.columns(4)
        theme = c[0].selectbox("動画のテーマ", themes, index=themes.index("グルメ") if "グルメ" in themes else 0)
        _lang0 = dash_cfg.get("cost_lang", "英語")
        lang_g = c[1].selectbox("動画の言語", langs, index=langs.index(_lang0) if _lang0 in langs else 0)
        horizon = c[2].selectbox("いつ時点の再生数で比べるか", ["投稿1年後", "投稿3年後"])
        budget = c[3].number_input("予算（円）", min_value=0, value=3_000_000, step=500_000)
        age_days = 365 if horizon == "投稿1年後" else 365 * 3

        st.markdown("**前提（公開されている相場から設定。下の出典を参照）**")
        c = st.columns(3)
        rate = c[0].number_input("登録者1人あたりの単価（円）", min_value=0.0, value=4.0, step=0.5,
                                 help="依頼の相場は、1投稿あたり登録者数×2〜5円")
        travel = c[1].number_input("交通・宿泊などの実費（1人あたり・円）", min_value=0,
                                   value=int(dash_cfg.get("travel_default", 30_000)), step=10_000,
                                   help="出演料とは別にかかる。" + dash_cfg.get("travel_help", ""))
        # 広告の単価は配信先の国で大きく違う（アジア圏2〜7円、欧米の主要国15〜40円、日本5〜15円）
        default_cpv = {"英語": 25.0, "韓国語": 5.0, "中国語": 5.0}.get(lang_g, 10.0)
        cpv = c[2].number_input("広告の1再生あたりの単価（円）", min_value=0.1, value=default_cpv, step=0.5,
                                help="配信先の国で大きく違う。アジア圏2〜7円、欧米の主要国15〜40円、日本5〜15円。"
                                     "初期値は、選んだ動画の言語の国に広告を配信する場合の目安")
        tiers = st.data_editor(pd.DataFrame({
            "クリエイターの規模": ["小（登録者1万人）", "中（登録者10万人）", "大（登録者100万人）"],
            "登録者数": [10_000, 100_000, 1_000_000],
        }), hide_index=True, width="stretch", num_rows="fixed")

        def predict(subs, days):
            new = pd.DataFrame([{"theme": theme, "region": data[base]["name"], "lang_group": lang_g,
                                 "log_age": math.log10(days), "log_subs": math.log10(subs + 1),
                                 **{col: 0 for col in aek.FORMAT_COLS.values()}}])
            pred = model.get_prediction(new).summary_frame(alpha=0.05).iloc[0]
            typical = 10 ** pred["mean"]
            return typical, typical * boost, 10 ** pred["mean_ci_lower"], 10 ** pred["mean_ci_upper"]

        rows = []
        for _, r in tiers.iterrows():
            typical, expected, lo, hi = predict(r["登録者数"], age_days)
            fee = r["登録者数"] * rate + travel
            n = int(budget // fee) if fee > 0 else 0
            rows.append({"方法": r["クリエイターの規模"], "登録者数": r["登録者数"], "1人あたりの費用": fee,
                         "典型的な再生数": typical, "典型値の幅（95%）": f"{num(lo)}〜{num(hi)}",
                         "期待値（バズ込み）": expected, "招ける人数": n, "合計の期待再生数": expected * n,
                         "1再生あたりの費用（円）": fee / expected if expected > 0 else np.nan,
                         "損益分岐の費用": cpv * expected})
        rows.append({"方法": "YouTube広告", "登録者数": np.nan, "1人あたりの費用": np.nan, "典型的な再生数": np.nan,
                     "典型値の幅（95%）": "-", "期待値（バズ込み）": np.nan, "招ける人数": np.nan,
                     "合計の期待再生数": budget / cpv, "1再生あたりの費用（円）": cpv, "損益分岐の費用": np.nan})
        res = pd.DataFrame(rows)

        st.markdown(f"**結果（{horizon}）**")
        st.dataframe(pd.DataFrame({
            "方法": res["方法"],
            "1人の費用（円）": res["1人あたりの費用"].map(num),
            "1本の典型的な再生数": res["典型的な再生数"].map(num),
            "1本の期待再生数（バズ込み）": res["期待値（バズ込み）"].map(num),
            "招ける人数": res["招ける人数"].map(num),
            "合計再生数": res["合計の期待再生数"].map(num),
            "1再生の費用（円）": res["1再生あたりの費用（円）"].map(lambda x: "-" if pd.isna(x) else f"{x:,.1f}"),
        }), hide_index=True, width="stretch")
        with st.expander("典型的な再生数（バズを除いた値）と予測の幅を見る"):
            st.dataframe(res[["方法", "典型的な再生数", "典型値の幅（95%）"]].assign(
                典型的な再生数=res["典型的な再生数"].map(num)).iloc[:-1], hide_index=True, width="stretch")

        left, right = st.columns([3, 2])
        with left:
            st.altair_chart(alt.Chart(res).mark_bar()
                            .encode(x=alt.X("1再生あたりの費用（円）:Q", title="1再生あたりの費用（円）"),
                                    y=alt.Y("方法:N", sort=None, title=None, axis=alt.Axis(labelLimit=220)),
                                    color=alt.condition(alt.datum["方法"] == "YouTube広告", alt.value(STONE), alt.value(SEA)),
                                    tooltip=["方法", alt.Tooltip("1再生あたりの費用（円）:Q", format=",.1f")])
                            .properties(height=alt.Step(36)), width="stretch")
        with right:
            st.markdown("**広告と比べると**")
            for _, r in res.iloc[:-1].iterrows():
                verdict = "広告より効率がいい" if r["1人あたりの費用"] <= r["損益分岐の費用"] else "広告より割高"
                st.markdown(f"- {r['方法']}：費用 {num(r['1人あたりの費用'])}円 ／ 広告と同じ効率になる上限 "
                            f"{num(r['損益分岐の費用'])}円 → **{verdict}**")

        elasticity = model.params.get("log_subs", np.nan)
        if not pd.isna(elasticity):
            st.info(
                f"**規模と効率の関係**：登録者数が10倍になっても、再生数は約{10 ** elasticity:.1f}倍にしかならない"
                f"（登録者数が1%増えると再生数は約{elasticity:.2f}%増える）。依頼の費用は登録者数にほぼ比例するので、"
                "大きいクリエイターを1人招くより、小さいクリエイターを何人も招く方が効率がいい。")

        # ===== 組み合わせの試算 =====
        st.markdown("#### 組み合わせの試算：小さなクリエイターに作ってもらい、反応の良かった動画を広告で広げる")
        st.caption("1再生あたりの費用だけなら、広告の方が安いことが多い。一方、クリエイターの紹介動画は残り続けて見られ続け、"
                   "作り手の言葉で語られる物語は広告では作れない。二次利用の許諾を取っておけば、反応の良かった動画をそのまま広告で配信できる。")
        c = st.columns(2)
        tier_names = list(res["方法"].iloc[:-1])
        pick = c[0].selectbox("招くクリエイターの規模", tier_names, index=0)
        share = c[1].slider("予算のうち、クリエイターに使う割合（%）", 0, 100, 50, 10)
        r = res[res["方法"] == pick].iloc[0]
        fee = r["1人あたりの費用"]
        _, exp1, _, _ = predict(r["登録者数"], 365)
        _, exp3, _, _ = predict(r["登録者数"], 365 * 3)
        plans = []
        for name, sh in [("広告だけ", 0), (f"組み合わせ（{share}%）", share), ("クリエイターだけ", 100)]:
            n = int(budget * sh / 100 // fee) if fee > 0 else 0
            ad = budget - n * fee
            ad_views = ad / cpv
            plans.append({"プラン": name, "招ける人数": n, "広告費（円）": ad,
                          "1年後の再生数": n * exp1 + ad_views, "3年後の再生数": n * exp3 + ad_views,
                          "残る動画・作り手": n})
        pl = pd.DataFrame(plans)
        st.dataframe(pl.assign(**{
            "広告費（円）": pl["広告費（円）"].map(num),
            "1年後の再生数": pl["1年後の再生数"].map(num),
            "3年後の再生数": pl["3年後の再生数"].map(num),
            "残る動画・作り手": pl["残る動画・作り手"].map(lambda x: f"{x}本・{x}人"),
        }), hide_index=True, width="stretch")
        ads_only, creators_only = pl.iloc[0], pl.iloc[-1]
        gap3 = creators_only["3年後の再生数"] / ads_only["3年後の再生数"] if ads_only["3年後の再生数"] else np.nan
        if not pd.isna(gap3):
            st.info(f"**同じ予算で比べると**：3年後の再生数は、クリエイターだけのプランが広告だけのプランの約{gap3:.0%}。"
                    f"そのうえで、紹介動画が{int(creators_only['残る動画・作り手'])}本、海外に向けて発信してくれる作り手が"
                    f"{int(creators_only['残る動画・作り手'])}人残る（広告だけなら0）。")
        st.caption("広告の再生は配信をやめると止まるため、1年後も3年後も同じ値にしている。紹介動画は投稿後も再生が積み上がる"
                   "（3年後の値は、古い動画ほど再生がたまっている関係からの推定）。作り手が増えることは、上位チャンネルへの依存を下げるKPIにつながる。")

        st.markdown("**依頼の仕方の工夫**：登録者数ではなく「平均再生回数×単価（相場2〜10円）」で支払う契約にし、"
                    "登録者数の割によく見られている作り手を選ぶと、1再生あたりの費用を広告並みに抑えられる。"
                    "また2023年10月からステルスマーケティングが規制され、依頼した投稿には広告であることの表示が必要で、"
                    "違反の責任は依頼した側（市）が負う。PR表記を契約で徹底する。")
        st.caption("出典：YouTuberへの依頼の相場（インスタラボ「ユーチューバーへのPR依頼の流れや費用相場」）、"
                   "YouTube広告の視聴単価と国による違い（デジマーケジャーナル「YouTube広告の費用相場と種類別料金」、2026年1月）、"
                   "自治体の招請事業の例（群馬県「令和8年度 海外インフルエンサー等招請事業 委託仕様書」）")

    st.divider()
    st.subheader("KPI：施策の効果を毎月この仕組みで測る")
    bt = data[base]["tourism"]
    fo_b = foreign_of(bt)
    sc = data[base]["summary"]
    nv_b, nc_b = name_view_share(fo_b, data[base]["config"])
    def kpi_value(k):
        kind = k.get("kind")
        if kind == "name_share":
            return f"{pct(nc_b)}（再生では{pct(nv_b)}）"
        if kind == "hhi":
            return f"{sc['集中度HHI']:.2f}"
        if kind == "top3":
            return pct(top3_share(fo_b))
        if kind == "lang_count":
            return f"{int((fo_b['lang_final'] == k['lang']).sum())}本"
        if kind == "channels":
            return f"{fo_b['channel_title'].nunique()}個"
        if kind == "theme_view_share":
            return pct(theme_share(bt, "海外", k["theme"]))
        if kind == "spot_count":
            return f"{int(spot_mask(fo_b, data[base]['config'], k['spots']).sum())}本"
        if kind == "spot_median":
            m = spot_mask(fo_b, data[base]["config"], [k["spot"]])
            return f"{num(fo_b.loc[m, 'view_count'].median())}回" if m.sum() else "-"
        if kind == "count_excluding_themes":
            return f"{int((~fo_b['primary_theme'].isin(k['themes'])).sum())}本"
        return k.get("value", "-")

    kpi_defs = dash_cfg.get("kpis") or [
        {"label": "海外の再生の集中度HHI（下げる）", "kind": "hhi", "target": "0.5以下"},
        {"label": "上位3チャンネルへの依存度（下げる）", "kind": "top3", "target": "50%以下"},
        {"label": "海外向けの動画を作ったチャンネル数（増やす）", "kind": "channels", "target": "現状の1.5倍"}]
    kpis = pd.DataFrame({"KPI": [k["label"] for k in kpi_defs],
                         "現状": [kpi_value(k) for k in kpi_defs],
                         "目標（例）": [k.get("target", "-") for k in kpi_defs]})
    st.dataframe(kpis, hide_index=True, width="stretch")
    st.caption("目標の値はたたき台（1年目＝施策を始めてから12か月）。知る → 行く → 使うの順に、同じ分析を毎月回して推移を見る。")
