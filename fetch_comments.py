"""
ステップ2: 上位動画のコメントを取得し「どの言語圏の人が見て、何に反応しているか」を調べる

YouTubeでは視聴者の国籍・年齢は投稿者本人しか見られないため、
「コメントの言語分布」を視聴者層の代理指標として使う。

対象:
  - 海外向け動画（日本語以外）の再生数上位 N本
  - 比較用に、日本語動画の再生数上位 M本

使い方:
  python fetch_comments.py --region choshi
  python fetch_comments.py --region oarai --top-foreign 30 --pages 3

前提: 先にステップ1（check_youtube_volume.py）を同じ地域で実行していること
クォータ: commentThreads.list = 1回1ユニット（1回最大100件）
"""

import argparse
import json
import sys
from datetime import datetime

import pandas as pd
from googleapiclient.errors import HttpError

from check_youtube_volume import get_youtube
from lang_utils import detect_text_language, is_foreign
from region_config import data_dir, latest, load_region, migrate_legacy


def fetch_comments(youtube, video_id, max_pages):
    """1本の動画のコメント（返信を除くトップレベル）を取得する"""
    comments, token = [], None
    for _ in range(max_pages):
        try:
            res = youtube.commentThreads().list(
                part="snippet", videoId=video_id, maxResults=100,
                order="relevance", textFormat="plainText", pageToken=token,
            ).execute()
        except HttpError as e:
            # コメントが無効な動画や削除済みの動画は飛ばす（クォータ切れなどは止める）
            if "commentsDisabled" in str(e) or e.resp.status == 404:
                return comments, "skipped"
            raise
        for item in res.get("items", []):
            top = item["snippet"]["topLevelComment"]["snippet"]
            comments.append({
                # 個人を特定する情報（投稿者名など）は分析に不要なので保存しない
                "comment_id": item["id"],
                "text": top.get("textDisplay", ""),
                "like_count": top.get("likeCount", 0),
                "published_at": top.get("publishedAt"),
                "reply_count": item["snippet"].get("totalReplyCount", 0),
            })
        token = res.get("nextPageToken")
        if not token:
            break
    return comments, "ok"


def lang_share(series, n=3):
    """言語の割合を「en 62%, zh-tw 20%」のような文字列にする（判定不能は除く）"""
    s = series[series != "unknown"]
    if s.empty:
        return "-"
    vc = s.value_counts(normalize=True).head(n)
    return ", ".join(f"{k} {v:.0%}" for k, v in vc.items())


def run(region_slug, top_foreign=20, top_ja=10, pages=2, youtube=None):
    """コメントを取得・集計し、CSVのパスを返す"""
    migrate_legacy()
    region = load_region(region_slug)
    out_dir = data_dir(region_slug)
    print(f"\n【{region['name']}】ステップ2: コメントの取得")

    csv_path = latest(region_slug, "videos_check_*.csv")
    if csv_path is None:
        sys.exit(f"動画CSVがありません。先に check_youtube_volume.py --region {region_slug} を実行してください。")
    print(f"動画データ: {csv_path}")
    videos = pd.read_csv(csv_path)
    if "is_foreign" not in videos.columns:
        sys.exit("動画CSVが古い形式です。新しい check_youtube_volume.py で作り直してください。")
    videos = videos[videos["relevant"]].copy()
    videos["view_count"] = videos["view_count"].fillna(0)

    foreign = videos[videos["is_foreign"]].nlargest(top_foreign, "view_count")
    domestic = videos[videos["lang"] == "ja"].nlargest(top_ja, "view_count")
    targets = pd.concat([foreign.assign(group="海外向け"), domestic.assign(group="国内向け")])

    cache_path = out_dir / "comments_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    need = [v for v in targets["video_id"] if v not in cache]
    print(f"対象動画: {len(targets)}本（うち未取得 {len(need)}本）"
          f" / 推定クォータ消費: 最大 約{len(need) * pages} ユニット\n")

    if need:
        youtube = youtube or get_youtube()
    try:
        for vid in need:
            comments, status = fetch_comments(youtube, vid, pages)
            cache[vid] = {"status": status, "comments": comments}
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  取得: {vid} -> {len(comments)}件 ({status})")
    except HttpError as e:
        sys.exit(f"\nAPIエラー: {e}\n（取得済みの分は保存されています。リセット後に再実行すれば続きから取得します）")

    # ===== コメントを1つの表にまとめる =====
    rows = []
    for _, v in targets.iterrows():
        for c in cache[v["video_id"]]["comments"]:
            rows.append({**c, "video_id": v["video_id"], "group": v["group"],
                         "video_lang": v["lang"], "video_title": v["title"],
                         "channel_title": v["channel_title"], "video_views": int(v["view_count"])})
    if not rows:
        print("コメントが1件も取得できませんでした。")
        return None
    cm = pd.DataFrame(rows)
    cm["comment_lang"] = cm["text"].map(detect_text_language)

    # ===== 集計1: 動画ごとのコメント言語 =====
    print("\n=== 動画ごとのコメント言語（上位3つ、判定不能を除く） ===")
    for group in ["海外向け", "国内向け"]:
        print(f"\n[{group}]")
        g = cm[cm["group"] == group]
        for vid, d in g.groupby("video_id", sort=False):
            head = d.iloc[0]
            print(f"  [{head['video_lang']}] {head['video_views']:>10,}回 | {len(d):>3}件 | "
                  f"{lang_share(d['comment_lang'])} | {head['video_title'][:35]}")

    # ===== 集計2: グループ全体のコメント言語 =====
    print("\n=== グループ全体のコメント言語の割合 ===")
    for group, d in cm.groupby("group"):
        print(f"  {group}: {len(d)}件 | {lang_share(d['comment_lang'], n=6)}")

    # ===== 集計3: 海外向け動画の、日本語以外のコメントでいいねが多いもの =====
    print("\n=== 海外向け動画で、いいねが多い日本語以外のコメント トップ15 ===")
    fc = cm[(cm["group"] == "海外向け") & cm["comment_lang"].map(is_foreign)]
    for _, r in fc.nlargest(15, "like_count").iterrows():
        text = r["text"].replace("\n", " ")[:90]
        print(f"  [{r['comment_lang']}] 👍{r['like_count']:>5} | {text}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = out_dir / f"comments_{stamp}.csv"
    cm.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n保存しました: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="choshi", help="regions/ にある地域名（例: choshi, oarai）")
    parser.add_argument("--top-foreign", type=int, default=20, help="海外向け動画の上位何本を対象にするか")
    parser.add_argument("--top-ja", type=int, default=10, help="比較用の日本語動画の上位何本を対象にするか")
    parser.add_argument("--pages", type=int, default=2, help="1本あたり何ページ（×100件）取得するか")
    args = parser.parse_args()
    run(args.region, args.top_foreign, args.top_ja, args.pages)


if __name__ == "__main__":
    main()
