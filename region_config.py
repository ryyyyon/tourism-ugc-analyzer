"""
地域設定の読み込みと、データの保存先の管理

  地域の設定: regions/<地域名>.json
  データの保存先: data/<地域名>/

新しい地域を分析したいときは、regions/ に設定ファイルを1つ追加するだけでよい。
"""

import json
import shutil
from pathlib import Path

REGIONS_DIR = Path("regions")
DATA_ROOT = Path("data")


def list_regions():
    return sorted(p.stem for p in REGIONS_DIR.glob("*.json"))


def load_region(slug):
    path = REGIONS_DIR / f"{slug}.json"
    if not path.exists():
        raise SystemExit(f"地域の設定ファイル {path} がありません。使える地域: {', '.join(list_regions())}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["slug"] = slug
    cfg.setdefault("name", slug)
    cfg.setdefault("extra_tourism", {})
    cfg.setdefault("extra_noise", {})
    return cfg


def data_dir(slug):
    d = DATA_ROOT / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest(slug, pattern):
    files = sorted(data_dir(slug).glob(pattern))
    return files[-1] if files else None


def migrate_legacy():
    """地域対応の前に data/ 直下に保存していた銚子のデータを data/choshi/ に移す"""
    if not DATA_ROOT.exists():
        return
    legacy = [p for p in DATA_ROOT.iterdir() if p.is_file()]
    if not legacy:
        return
    dest = data_dir("choshi")
    for p in legacy:
        name = p.name.replace("choshi_videos_check_", "videos_check_")
        shutil.move(str(p), str(dest / name))
    print(f"（以前のデータ {len(legacy)}件を {dest} に移動しました）\n")
