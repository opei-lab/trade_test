"""スクリーニング結果キャッシュ

スキャン結果をJSONで保存し、同日の再スキャンではキャッシュを返す。
株価は日次データなので、1日1回のスキャンで十分。
"""

import json
from pathlib import Path
from datetime import datetime, date

CACHE_DIR = Path(__file__).parent.parent.parent / "data"
CACHE_FILE = CACHE_DIR / "screen_cache.json"


def save_screen_results(scan_mode: str, results: list[dict]):
    """スクリーニング結果をキャッシュに保存する。"""
    CACHE_DIR.mkdir(exist_ok=True)

    # 結果からシリアライズ不可のオブジェクトを除外
    clean_results = []
    for r in results:
        clean = {}
        for k, v in r.items():
            if isinstance(v, (str, int, float, bool, type(None))):
                clean[k] = v
            elif isinstance(v, list):
                clean[k] = _clean_list(v)
            elif isinstance(v, dict):
                clean[k] = _clean_dict(v)
        clean_results.append(clean)

    cache = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "scan_mode": scan_mode,
        "count": len(results),
        "results": clean_results,
    }

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, default=str)


def load_screen_cache(scan_mode: str) -> list[dict] | None:
    """今日のキャッシュがあれば返す。なければNone。"""
    if not CACHE_FILE.exists():
        return None

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

        if cache.get("date") != date.today().isoformat():
            return None

        if cache.get("scan_mode") != scan_mode:
            return None

        return cache.get("results", [])
    except Exception:
        return None


def get_cache_info() -> dict | None:
    """キャッシュの情報を返す。"""
    if not CACHE_FILE.exists():
        return None

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return {
            "date": cache.get("date"),
            "timestamp": cache.get("timestamp"),
            "scan_mode": cache.get("scan_mode"),
            "count": cache.get("count", 0),
        }
    except Exception:
        return None


def clear_cache():
    """キャッシュを削除する。"""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()


def _clean_dict(d):
    clean = {}
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool, type(None))):
            clean[k] = v
        elif isinstance(v, list):
            clean[k] = _clean_list(v)
        elif isinstance(v, dict):
            clean[k] = _clean_dict(v)
    return clean


def _clean_list(lst):
    clean = []
    for item in lst:
        if isinstance(item, (str, int, float, bool, type(None))):
            clean.append(item)
        elif isinstance(item, dict):
            clean.append(_clean_dict(item))
        elif isinstance(item, list):
            clean.append(_clean_list(item))
    return clean
