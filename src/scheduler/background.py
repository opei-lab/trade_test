"""バックグラウンドスキャン

アプリ起動時に裏でスキャンを実行し、結果をキャッシュに保存する。
画面は常にキャッシュから結果を表示するだけ。
処理中でも画面操作が可能。
"""

import threading
import time
import logging
from datetime import datetime, date
from pathlib import Path

log_dir = Path(__file__).parent.parent.parent / "data"
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(log_dir / "daily_job.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

_thread_started = False
_scan_status = {"running": False, "last_run": None, "progress": "", "error": None}


def get_scan_status() -> dict:
    """スキャンの進行状態を返す。"""
    return dict(_scan_status)


def _run_scan(scan_mode: str = "おまかせ", sector: str = "", custom_codes: list = None):
    """フルスキャン（Stage 1 + Stage 2）をバックグラウンドで実行。"""
    global _scan_status

    _scan_status["running"] = True
    _scan_status["progress"] = "開始中..."
    _scan_status["error"] = None

    try:
        from src.data.database import init_db
        init_db()

        from src.data.stocklist import get_growth_stocks, fetch_stocklist, get_stocks_by_sector
        from src.strategy.screener import screen_stocks
        from src.strategy.deep_analysis import run_deep_analysis
        from src.strategy.cache import save_screen_results
        from src.data.watchlist import update_from_screening
        from src.feedback.tracker import record_recommendation, check_outcomes
        from src.feedback.optimizer import update_weights

        # Stage 1
        _scan_status["progress"] = "銘柄リスト取得中..."
        if custom_codes:
            import pandas as pd
            stocks = pd.DataFrame({"code": custom_codes, "name": "", "market": "", "sector": ""})
        elif scan_mode == "全市場":
            stocks = fetch_stocklist()
        elif scan_mode == "業種指定" and sector:
            stocks = get_stocks_by_sector(sector)
        else:
            stocks = get_growth_stocks()

        all_codes = stocks["code"].tolist()
        name_map = dict(zip(stocks["code"].astype(str), stocks["name"]))
        # 上限200銘柄（速度と網羅性のバランス）
        codes = all_codes[:200]
        total = len(codes)
        logging.info(f"Scanning {total} stocks")

        def on_progress_1(c, t, code):
            _scan_status["progress"] = f"Stage 1: {code} ({c+1}/{t})"

        candidates = screen_stocks(codes, min_score=0, progress_callback=on_progress_1)

        for r in candidates:
            jpx = name_map.get(r["code"], "")
            if jpx and jpx.strip():
                r["name"] = jpx

        logging.info(f"Stage 1: {len(candidates)} candidates")

        if candidates:
            # Stage 2
            def on_progress_2(c, t, code):
                _scan_status["progress"] = f"Stage 2: {code} ({c+1}/{t})"

            results = run_deep_analysis(candidates, progress_callback=on_progress_2)
            logging.info(f"Stage 2: {len(results)} results")

            # キャッシュ保存
            save_screen_results(scan_mode, results)

            # ウォッチリスト更新
            update_from_screening(results)

            # 推奨記録
            for r in results[:10]:
                try:
                    record_recommendation(r)
                except Exception:
                    pass

        # 答え合わせ + 最適化
        _scan_status["progress"] = "答え合わせ中..."
        check_outcomes(days_after=30)
        update_weights()

        _scan_status["last_run"] = today.isoformat()
        _scan_status["progress"] = f"完了（{len(results) if candidates else 0}件）"
        logging.info("=== Scan completed ===")

    except Exception as e:
        _scan_status["error"] = str(e)
        _scan_status["progress"] = f"エラー: {e}"
        logging.error(f"Scan failed: {e}")
    finally:
        _scan_status["running"] = False


def _background_loop():
    """バックグラウンドループ。起動時に即実行、以降1時間ごと。"""
    # 起動直後に実行
    _run_scan()

    while True:
        time.sleep(3600)
        try:
            _run_scan()
        except Exception as e:
            logging.error(f"Background loop error: {e}")


def start_background_job():
    """バックグラウンドジョブを開始する。"""
    global _thread_started
    if _thread_started:
        return

    thread = threading.Thread(target=_background_loop, daemon=True)
    thread.start()
    _thread_started = True
