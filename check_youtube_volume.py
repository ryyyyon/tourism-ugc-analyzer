"""
ステップ1: 指定した観光地のYouTube動画を集め、言語と関連性を判定する

使い方:
  python check_youtube_volume.py --region choshi
  python check_youtube_volume.py --region oarai

地域の設定（検索キーワードなど）は regions/<地域名>.json
結果は data/<地域名>/ に保存される

クォータ目安（無料枠は1日10,000ユニット。日本時間16時〔冬時間は17時〕にリセット）:
  search.list = 1回100ユニット / videos.list = 1回1ユニット
  → 1地域あたり キーワード10個 × 2ページ × 100 = 約2,000ユニット
  検索結果は search_cache.json に保存し、2回目以降は再利用する
"""

import argparse
import json
import os
import sys
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lang_utils import detect_video_language, is_foreign
from region_config import data_dir, load_region, migrate_legacy
from theme_rules import Classifier

DEFAULT_PAGES = 2  # 1ページ最大50件。1ページごとに100ユニット消費


def get_youtube():
    load_dotenv()
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        sys.exit("YOUTUBE_API_KEY が設定されていません。.env を確認してください。")
    return build("youtube", "v3", developerKey=api_key)


def search_video_ids(youtube, query, max_pages):
    """キーワードで検索して動画IDのリストと totalResults（参考値）を返す"""
    ids, total, token = [], None, None
    for _ in range(max_pages):
        res = youtube.search().list(
            q=query, part="id", type="video", maxResults=50, pageToken=token
        ).execute()
        if total is None:
            total = res.get("pageInfo", {}).get("totalResults")
        # まれに videoId を持たない結果が混ざるので、それは飛ばす
        ids += [item["id"]["videoId"] for item in res.get("items", [])
                if "videoId" in item.get("id", {})]
        token = res.get("nextPageToken")
        if not token:
            break
    return ids, total


def to_int(value):
    return int(value) if value is not None else None


def fetch_video_details(youtube, video_ids):
    """動画IDから詳細情報を取得（50件ずつ）"""
    rows = []
    for i in range(0, len(video_ids), 50):
        res = youtube.videos().list(
            part="snippet,statistics", id=",".join(video_ids[i:i + 50])
        ).execute()
        for v in res.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            rows.append({
                "video_id": v["id"],
                "title": sn.get("title", ""),
                "description": (sn.get("description") or "")[:500],
                "channel_title": sn.get("channelTitle", ""),
                "channel_id": sn.get("channelId"),
                "published_at": sn.get("publishedAt"),
                "default_audio_language": sn.get("defaultAudioLanguage"),
                "default_language": sn.get("defaultLanguage"),
                "view_count": to_int(st.get("viewCount")),
                "like_count": to_int(st.get("likeCount")),       # 非公開の場合は None
                "comment_count": to_int(st.get("commentCount")),
            })
    return rows


def run(region_slug, youtube=None):
    """動画を集めて判定し、CSVのパスを返す"""
    migrate_legacy()
    region = load_region(region_slug)
    out_dir = data_dir(region_slug)
    clf = Classifier(region)
    keywords = region["search_keywords"]
    pages = region.get("max_pages_per_keyword", DEFAULT_PAGES)

    cache_path = out_dir / "search_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    uncached = [k for k in keywords if k not in cache]
    print(f"【{region['name']}】ステップ1: 動画の収集")
    print(f"推定クォータ消費: 最大 約{len(uncached) * pages * 100:,} ユニット"
          f"（未取得のキーワード {len(uncached)}個分。1日の上限 10,000）\n")

    youtube = youtube or get_youtube()
    keyword_hits = {}   # video_id -> そのIDがヒットしたキーワードの集合
    keyword_stats = []
    try:
        for kw in keywords:
            if kw in cache:
                ids, total = cache[kw]["ids"], cache[kw]["total"]
                label = "キャッシュ"
            else:
                ids, total = search_video_ids(youtube, kw, pages)
                cache[kw] = {"ids": ids, "total": total}
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
                label = "検索完了"
            keyword_stats.append({"keyword": kw, "total_results_estimate": total, "fetched": len(ids)})
            for vid in ids:
                keyword_hits.setdefault(vid, set()).add(kw)
            print(f"  {label}: {kw!r:28} -> {len(ids)}件")
        rows = fetch_video_details(youtube, list(keyword_hits))
    except HttpError as e:
        sys.exit(f"\nAPIエラー: {e}\n（quotaExceeded の場合は、日本時間16時のリセット後に再実行。"
                 f"取得済みのキーワードは保存されています）")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    raw_path = out_dir / f"raw_check_{stamp}.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump({"keyword_hits": {k: sorted(v) for k, v in keyword_hits.items()},
                   "videos": rows}, f, ensure_ascii=False, indent=2)

    df = pd.DataFrame(rows)
    df["keyword_list"] = df["video_id"].map(lambda v: sorted(keyword_hits[v]))
    df[["lang", "lang_source"]] = df.apply(
        lambda r: detect_video_language(r["title"], r["description"],
                                        r["default_audio_language"], r["default_language"]),
        axis=1, result_type="expand")
    df["is_foreign"] = df["lang"].map(is_foreign)
    df["relevant"] = df.apply(lambda r: clf.is_relevant(r["title"], r["description"]), axis=1)

    # ===== 集計1: キーワード別 =====
    kw_df = pd.DataFrame(keyword_stats)
    rel_ratio = df.explode("keyword_list").groupby("keyword_list")["relevant"].mean()
    kw_df["relevant_ratio"] = kw_df["keyword"].map(rel_ratio).round(2)
    print("\n=== キーワード別 ===")
    print("（total_results_estimate はYouTubeの概算値で当てにならないので参考程度）")
    print(kw_df.to_string(index=False))

    # ===== 集計2: 全体 =====
    rel_df = df[df["relevant"]]
    print(f"\nユニーク動画数: {len(df)} / うち地域に関連: {len(rel_df)}"
          f" / うち海外向け（日本語以外）: {int(rel_df['is_foreign'].sum())}")

    # ===== 集計3: 言語別 =====
    lang_summary = (
        rel_df.groupby("lang")
        .agg(videos=("video_id", "count"),
             total_views=("view_count", "sum"),
             median_views=("view_count", "median"))
        .sort_values("videos", ascending=False)
    )
    print("\n=== 言語別（地域に関連する動画のみ） ===")
    print(lang_summary.to_string())

    # ===== 集計4: 海外向けの再生数上位 =====
    foreign = rel_df[rel_df["is_foreign"]].nlargest(10, "view_count")
    print("\n=== 海外向け動画 再生数トップ10 ===")
    for _, r in foreign.iterrows():
        print(f"  [{r['lang']}/{r['lang_source']}] {int(r['view_count']):>10,} 回 | "
              f"{r['title'][:50]} | {r['channel_title']}")

    csv_path = out_dir / f"videos_check_{stamp}.csv"
    out = df.assign(keywords=df["keyword_list"].map("|".join)).drop(columns=["keyword_list"])
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n保存しました: {csv_path}")
    return csv_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="choshi", help="regions/ にある地域名（例: choshi, oarai）")
    args = parser.parse_args()
    run(args.region)


if __name__ == "__main__":
    main()
