"""バックグラウンド日次バッチ

Streamlitアプリ起動中にバックグラウンドスレッドで動く。
タスクスケジューラ不要。アプリが動いていれば自走する。
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
_last_run_date = None


def _run_daily_job():
    """日次バッチの実体。"""
    global _last_run_date

    # 今日既に実行済みならスキップ
    today = date.today()
    if _last_run_date == today:
        return

    logging.info("=== Daily job started ===")

    try:
        from src.data.database import init_db
        init_db()

        from src.data.stocklist import get_growth_stocks
        from src.strategy.screener import screen_stocks
        from src.feedback.tracker import record_recommendation, check_outcomes, get_hit_rate
        from src.feedback.optimizer import update_weights

        # スキャン
        stocks = get_growth_stocks()
        codes = stocks["code"].tolist()
        logging.info(f"Scanning {len(codes)} stocks")

        results = screen_stocks(codes, min_score=0)
        logging.info(f"Found {len(results)} candidates")

        for r in results[:10]:
            try:
                record_recommendation(r)
            except Exception:
                pass

        # 答え合わせ
        check_outcomes(days_after=30)
        stats = get_hit_rate()
        logging.info(f"Hit rate: {stats}")

        # 最適化
        update_weights()

        _last_run_date = today
        logging.info("=== Daily job completed ===")

    except Exception as e:
        logging.error(f"Daily job failed: {e}")


def _background_loop():
    """バックグラウンドループ。1時間ごとにチェック。"""
    while True:
        try:
            _run_daily_job()
        except Exception as e:
            logging.error(f"Background loop error: {e}")
        time.sleep(3600)  # 1時間ごと


def start_background_job():
    """バックグラウンドジョブを開始する（1回だけ）。"""
    global _thread_started
    if _thread_started:
        return

    thread = threading.Thread(target=_background_loop, daemon=True)
    thread.start()
    _thread_started = True
