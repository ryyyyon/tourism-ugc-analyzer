"""
ステップ3: 動画をテーマに分類し、国内向け／海外向けでどのテーマが見られているかを比較する

使い方:
  python classify_themes.py --region choshi
  python classify_themes.py --region choshi --show 温泉・宿   # 特定テーマの動画一覧も表示

前提:
  - ステップ1（check_youtube_volume.py）を実行済み（必須）
  - ステップ2（fetch_comments.py）を実行済み（任意。あればコメントの言語で動画の言語判定を補正する）
  - APIは使わないので、何度実行してもクォータは消費しない

やること:
  1. 最新のルールで、関連判定と言語判定をやり直す
  2. コメントがある動画は、コメントの言語分布で言語判定を補正する
     （例: コメントの95%が日本語なのに zh-cn と判定されていた動画 → 国内向け）
  3. 共通辞書＋地域の追加辞書でテーマに分類し、ノイズ（ニュース・音楽等）を除く
  4. テーマ × 国内/海外 で本数・構成比・再生数・いいね率を比較
  5. 未分類の動画を表示（辞書を改善するため）
"""

import argparse
import sys
from datetime import datetime

import pandas as pd

from lang_utils import detect_video_language, is_foreign
from region_config import data_dir, latest, load_region, migrate_legacy
from theme_rules import Classifier

pd.set_option("display.unicode.east_asian_width", True)  # 日本語の表示ずれを防ぐ
pd.set_option("display.width", 200)

MIN_COMMENTS = 10          # コメント言語で補正するのに必要な、言語が判定できたコメントの最低件数
JA_SHARE_DOMESTIC = 0.8    # コメントの日本語率がこれ以上なら国内向けとみなす
JA_SHARE_FOREIGN = 0.3     # 日本語判定の動画でも、コメントの日本語率がこれ未満なら海外の視聴者向け


def market_of(lang):
    if lang == "ja":
        return "国内"
    if is_foreign(lang):
        return "海外"
    return "不明"


# タイトルの文字ではっきり判定できたものは、コメントで上書きしない
# （人の目での検証で、簡体字の中国語の動画がコメントによって日本語に上書きされていたため）
CONFIDENT_SOURCES = {"title_script", "han_rule", "title_bilingual"}


def correct_lang_by_comments(videos, comments):
    """コメントの言語分布を使って、動画の言語判定を補正する"""
    videos["lang_final"] = videos["lang"]
    videos["lang_fix"] = ""
    if comments is None:
        return videos
    known = comments[~comments["comment_lang"].isin(["unknown", "zh_or_ja"])]
    for vid, d in known.groupby("video_id"):
        if len(d) < MIN_COMMENTS:
            continue
        share = d["comment_lang"].value_counts(normalize=True)
        top_lang, top_share = share.index[0], share.iloc[0]
        ja_share = share.get("ja", 0)
        idx = videos.index[videos["video_id"] == vid]
        if len(idx) == 0:
            continue
        cur = videos.loc[idx[0], "lang"]
        if videos.loc[idx[0], "lang_source"] in CONFIDENT_SOURCES:
            continue
        if ja_share >= JA_SHARE_DOMESTIC and cur != "ja":
            new = "ja"
        elif cur in ("ja", "zh_or_ja") and ja_share < JA_SHARE_FOREIGN:
            new = top_lang
        else:
            continue
        videos.loc[idx, "lang_final"] = new
        videos.loc[idx, "lang_fix"] = f"{cur}→{new}（コメント{len(d)}件中 {top_lang} {top_share:.0%}）"
    return videos


def theme_market_table(df):
    """テーマ × 国内/海外 の比較表を作る"""
    dom_total = (df["market"] == "国内").sum()
    for_total = (df["market"] == "海外").sum()
    dom_views = df.loc[df["market"] == "国内", "view_count"].sum()
    for_views = df.loc[df["market"] == "海外", "view_count"].sum()
    rows = []
    for theme, d in df.groupby("primary_theme"):
        dom, fo = d[d["market"] == "国内"], d[d["market"] == "海外"]
        rows.append({
            "テーマ": theme,
            "国内_本数": len(dom),
            "海外_本数": len(fo),
            "国内_構成比": len(dom) / dom_total if dom_total else 0,
            "海外_構成比": len(fo) / for_total if for_total else 0,
            "国内_再生構成比": dom["view_count"].sum() / dom_views if dom_views else 0,
            "海外_再生構成比": fo["view_count"].sum() / for_views if for_views else 0,
            "国内_再生中央値": dom["view_count"].median(),
            "海外_再生中央値": fo["view_count"].median(),
            "国内_いいね率": dom["like_rate"].median(),
            "海外_いいね率": fo["like_rate"].median(),
        })
    t = pd.DataFrame(rows)
    t["合計"] = t["国内_本数"] + t["海外_本数"]
    return t.sort_values("合計", ascending=False).drop(columns="合計").reset_index(drop=True)


def fmt_table(t):
    out = t.copy()
    for c in ["国内_構成比", "海外_構成比", "国内_再生構成比", "海外_再生構成比"]:
        out[c] = out[c].map(lambda x: f"{x:.0%}")
    for c in ["国内_いいね率", "海外_いいね率"]:
        out[c] = out[c].map(lambda x: "-" if pd.isna(x) else f"{x:.1%}")
    for c in ["国内_再生中央値", "海外_再生中央値"]:
        out[c] = out[c].map(lambda x: "-" if pd.isna(x) else f"{int(x):,}")
    return out


def build_themed_videos(region_slug):
    """分類済みの動画の表を作って返す（表示なし。ダッシュボードからも使う）"""
    migrate_legacy()
    region = load_region(region_slug)
    clf = Classifier(region)
    video_csv = latest(region_slug, "videos_check_*.csv")
    if video_csv is None:
        sys.exit(f"動画CSVがありません。先に check_youtube_volume.py --region {region_slug} を実行してください。")
    comment_csv = latest(region_slug, "comments_*.csv")

    videos = pd.read_csv(video_csv)
    videos["relevant"] = videos.apply(lambda r: clf.is_relevant(r["title"], r["description"]), axis=1)
    videos = videos[videos["relevant"]].copy()
    comments = pd.read_csv(comment_csv) if comment_csv else None

    videos[["lang", "lang_source"]] = videos.apply(
        lambda r: detect_video_language(r["title"], r["description"],
                                        r["default_audio_language"], r["default_language"]),
        axis=1, result_type="expand")
    videos = correct_lang_by_comments(videos, comments)
    videos["market"] = videos["lang_final"].map(market_of)

    result = videos.apply(lambda r: clf.classify(r["title"], r["description"], r["channel_title"]), axis=1)
    videos["is_noise"] = result.map(lambda x: x["is_noise"])
    videos["primary_theme"] = result.map(lambda x: x["primary_theme"])
    videos["themes"] = result.map(lambda x: "|".join(x["themes"]))
    videos["matched_keywords"] = result.map(lambda x: "|".join(x["matched"]))
    videos["formats"] = videos["title"].map(lambda t: "|".join(clf.formats(t)))
    views = videos["view_count"]
    videos["like_rate"] = (videos["like_count"] / views).where(views > 0)
    return region, videos, video_csv, comment_csv


def run(region_slug, show=None):
    region, videos, video_csv, comment_csv = build_themed_videos(region_slug)
    print(f"\n【{region['name']}】ステップ3: テーマ分類と国内・海外の比較")
    print(f"動画データ: {video_csv}")
    print(f"コメントデータ: {comment_csv or 'なし（言語の補正は行いません）'}")
    print(f"地域に関連する動画: {len(videos)}本\n")

    fixed = videos[videos["lang_fix"] != ""]
    print(f"=== コメントの言語で判定を補正した動画: {len(fixed)}本 ===")
    for _, r in fixed.iterrows():
        print(f"  {r['lang_fix']} | {r['title'][:45]}")

    noise = videos[videos["is_noise"]]
    print(f"\n=== ノイズとして除外: {len(noise)}本 / {len(videos)}本 ===")
    for theme, d in noise.groupby("primary_theme"):
        print(f"  [{theme}] {len(d)}本  例: " +
              " / ".join(d.nlargest(2, "view_count")["title"].str[:28]))

    tourism = videos[~videos["is_noise"]]
    known = tourism[tourism["market"] != "不明"]
    print(f"\n観光関連: {len(tourism)}本（国内 {int((known['market'] == '国内').sum())}本 / "
          f"海外 {int((known['market'] == '海外').sum())}本 / 言語不明 {int((tourism['market'] == '不明').sum())}本）")

    table = theme_market_table(known)
    shown = fmt_table(table)
    print("\n=== テーマ別の比較①：量（言語不明を除く） ===")
    print("（構成比 = 国内/海外それぞれの中での割合。本数は検索キーワードの選び方に影響される点に注意）")
    print(shown[["テーマ", "国内_本数", "海外_本数", "国内_構成比", "海外_構成比",
                 "国内_再生構成比", "海外_再生構成比"]].to_string(index=False))
    print("\n=== テーマ別の比較②：1本あたりの反応 ===")
    print("（本数が少ないテーマは参考程度。いいね率は再生数の少ない動画ほど高く出やすい）")
    print(shown[["テーマ", "国内_本数", "海外_本数", "国内_再生中央値", "海外_再生中央値",
                 "国内_いいね率", "海外_いいね率"]].to_string(index=False))

    foreign = known[known["market"] == "海外"]
    main_langs = foreign["lang_final"].value_counts()
    main_langs = main_langs[main_langs >= 3].index.tolist()
    if main_langs:
        print("\n=== 海外向け動画の言語 × テーマ（3本以上ある言語のみ） ===")
        print(pd.crosstab(foreign["primary_theme"], foreign["lang_final"])[main_langs].to_string())

    t = table[~table["テーマ"].isin(["未分類", "旅行全般"])]
    gap = t[(t["国内_構成比"] >= 0.05) & (t["海外_構成比"] < t["国内_構成比"] * 0.5)]
    print("\n=== 空白地帯の候補（国内では構成比5%以上あるのに、海外での構成比が国内の半分未満） ===")
    if gap.empty:
        print("  なし")
    for _, r in gap.iterrows():
        med = "-" if pd.isna(r["国内_再生中央値"]) else f"{int(r['国内_再生中央値']):,}回"
        print(f"  {r['テーマ']}: 国内 {r['国内_本数']}本（{r['国内_構成比']:.0%}、再生中央値 {med}）"
              f" / 海外 {r['海外_本数']}本（{r['海外_構成比']:.0%}）")

    uncls = tourism[tourism["primary_theme"] == "未分類"]
    generic = tourism[tourism["primary_theme"] == "旅行全般"]
    print(f"\n=== 未分類 {len(uncls)}本 / 旅行全般 {len(generic)}本（辞書改善の参考に、未分類の再生数上位） ===")
    for _, r in uncls.nlargest(15, "view_count").iterrows():
        v = int(r["view_count"]) if pd.notna(r["view_count"]) else 0
        print(f"  [{r['lang_final']}] {v:>9,}回 | {r['title'][:60]}")

    if show:
        d = videos[videos["primary_theme"] == show]
        print(f"\n=== 「{show}」の動画一覧（{len(d)}本、市場ごとに再生数上位20本） ===")
        for market, dm in d.groupby("market"):
            print(f"\n[{market}] {len(dm)}本")
            for _, r in dm.nlargest(20, "view_count").iterrows():
                v = int(r["view_count"]) if pd.notna(r["view_count"]) else 0
                print(f"  [{r['lang_final']}] {v:>9,}回 | {r['title'][:45]} | {r['channel_title'][:20]}"
                      f" | 一致: {r['matched_keywords'][:30]}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = data_dir(region_slug) / f"videos_themed_{stamp}.csv"
    videos.drop(columns=["keyword_list"], errors="ignore").to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n保存しました: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="choshi", help="regions/ にある地域名（例: choshi, oarai）")
    parser.add_argument("--show", help="このテーマの動画一覧を表示する（例: 温泉・宿）")
    args = parser.parse_args()
    run(args.region, args.show)


if __name__ == "__main__":
    main()
