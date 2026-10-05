"""
動画を投稿したチャンネルの登録者数などを取得する

目的: 回帰分析で「人気のチャンネルだから伸びた」効果を取り除くため、
     チャンネルの登録者数をモデルに加える（analyze_engagement_keywords.py が自動で使う）

使い方:
  python fetch_channel_stats.py                 # regions/ にある全地域
  python fetch_channel_stats.py --region choshi

クォータ: videos.list（チャンネルIDの取得）と channels.list はどちらも50件で1ユニット。
         1地域あたり30〜40ユニット程度
結果: data/<地域>/channel_stats.csv（動画ID・チャンネルID・登録者数など）
     2回目以降は取得済みの動画を飛ばす
"""

import argparse
import sys

import pandas as pd
from googleapiclient.errors import HttpError

from check_youtube_volume import get_youtube
from region_config import data_dir, latest, list_regions, load_region


def chunks(items, size=50):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def fetch_channel_ids(youtube, video_ids):
    mapping = {}
    for part in chunks(video_ids):
        res = youtube.videos().list(part="snippet", id=",".join(part)).execute()
        for v in res.get("items", []):
            mapping[v["id"]] = v["snippet"].get("channelId")
    return mapping


def fetch_channels(youtube, channel_ids):
    rows = []
    for part in chunks(channel_ids):
        res = youtube.channels().list(part="statistics,snippet", id=",".join(part)).execute()
        for c in res.get("items", []):
            st = c.get("statistics", {})
            hidden = st.get("hiddenSubscriberCount", False)
            rows.append({
                "channel_id": c["id"],
                "channel_country": c["snippet"].get("country"),  # 設定していないチャンネルも多い
                "subscribers": None if hidden else int(st.get("subscriberCount", 0)),
                "channel_videos": int(st.get("videoCount", 0)),
                "channel_total_views": int(st.get("viewCount", 0)),
            })
    return pd.DataFrame(rows)


def run(slug, youtube):
    region = load_region(slug)
    out_path = data_dir(slug) / "channel_stats.csv"
    video_csv = latest(slug, "videos_check_*.csv")
    if video_csv is None:
        print(f"【{region['name']}】動画CSVがないので飛ばします")
        return
    videos = pd.read_csv(video_csv)
    done = pd.read_csv(out_path) if out_path.exists() else pd.DataFrame(columns=["video_id"])
    todo = [v for v in videos["video_id"] if v not in set(done["video_id"])]
    print(f"【{region['name']}】未取得の動画 {len(todo)}本 / 推定クォータ 約{len(todo) // 50 + 1}ユニット＋チャンネル分")
    if not todo:
        return

    mapping = fetch_channel_ids(youtube, todo)
    new = pd.DataFrame({"video_id": list(mapping.keys()), "channel_id": list(mapping.values())})
    channel_ids = sorted(set(new["channel_id"].dropna()))
    stats = fetch_channels(youtube, channel_ids)
    new = new.merge(stats, on="channel_id", how="left")
    merged = pd.concat([done, new], ignore_index=True).drop_duplicates("video_id", keep="last")
    merged.to_csv(out_path, index=False, encoding="utf-8-sig")
    hidden = int(new["subscribers"].isna().sum())
    print(f"  チャンネル {len(channel_ids)}個の情報を取得（登録者数が非公開: 動画{hidden}本分）→ {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", nargs="+", default=list_regions())
    args = parser.parse_args()
    youtube = get_youtube()
    try:
        for slug in args.region:
            run(slug, youtube)
    except HttpError as e:
        sys.exit(f"\nAPIエラー: {e}\n（取得済みの地域は保存されています）")


if __name__ == "__main__":
    main()
