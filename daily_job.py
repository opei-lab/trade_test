"""日次バッチジョブ（自走改善のエンジン）

Windowsタスクスケジューラから毎日1回実行される。
1. 全銘柄スキャン → 推奨をDBに記録
2. 過去の推奨の答え合わせ
3. 十分なデータが溜まったらパラメータ最適化
"""

import sys
import logging
from datetime import datetime
from pathlib import Path

# ログ設定
log_dir = Path(__file__).parent / "data"
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(log_dir / "daily_job.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

def main():
    logging.info("=== Daily job started ===")

    try:
        from src.data.database import init_db
        init_db()
        logging.info("DB initialized")
    except Exception as e:
        logging.error(f"DB init failed: {e}")
        return

    # Step 1: スキャン
    try:
        from src.data.stocklist import get_growth_stocks
        from src.strategy.screener import screen_stocks
        from src.feedback.tracker import record_recommendation

        stocks = get_growth_stocks()
        codes = stocks["code"].tolist()
        logging.info(f"Scanning {len(codes)} stocks")

        results = screen_stocks(codes, min_score=0)
        logging.info(f"Found {len(results)} candidates")

        # 上位10件をDBに記録
        recorded = 0
        for r in results[:10]:
            try:
                record_recommendation(r)
                recorded += 1
            except Exception as e:
                logging.warning(f"Failed to record {r.get('code')}: {e}")

        logging.info(f"Recorded {recorded} recommendations")

    except Exception as e:
        logging.error(f"Scan failed: {e}")

    # Step 2: 答え合わせ
    try:
        from src.feedback.tracker import check_outcomes, get_hit_rate

        check_outcomes(days_after=30)
        stats = get_hit_rate()
        logging.info(f"Hit rate: {stats}")

    except Exception as e:
        logging.error(f"Review failed: {e}")

    # Step 3: パラメータ最適化
    try:
        from src.feedback.optimizer import update_weights

        result = update_weights()
        logging.info(f"Optimizer: {result}")

    except Exception as e:
        logging.error(f"Optimization failed: {e}")

    logging.info("=== Daily job completed ===")


if __name__ == "__main__":
    main()
