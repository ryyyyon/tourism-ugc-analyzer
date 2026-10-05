"""
ステップ1〜3をまとめて実行する

使い方:
  python run_pipeline.py --region enoshima_kamakura oarai   # 複数地域を続けて実行
  python run_pipeline.py --region choshi --skip-comments     # コメント取得を飛ばす

クォータの目安: 1地域あたり 約2,100ユニット（検索 約2,000 + 詳細・コメント 約100）
               1日の上限は10,000なので、1日に4地域まで
"""

import argparse

import check_youtube_volume
import classify_themes
import fetch_comments
from region_config import list_regions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", nargs="+", default=["choshi"],
                        help=f"地域名（複数可）。使える地域: {', '.join(list_regions())}")
    parser.add_argument("--skip-comments", action="store_true", help="コメントの取得を飛ばす")
    args = parser.parse_args()

    youtube = None
    for region in args.region:
        print("\n" + "=" * 70)
        print(f" {region}")
        print("=" * 70)
        if youtube is None:
            youtube = check_youtube_volume.get_youtube()
        check_youtube_volume.run(region, youtube)
        if not args.skip_comments:
            fetch_comments.run(region, youtube=youtube)
        classify_themes.run(region)


if __name__ == "__main__":
    main()
