"""日次バッチジョブ

毎日自動で実行し、データ収集 → 分析 → 答え合わせを行う。
"""

from datetime import datetime
from src.data.stocklist import fetch_stocklist
from src.strategy.screener import screen_stocks
from src.feedback.tracker import record_recommendation, check_outcomes
from src.feedback.optimizer import update_weights


def daily_scan():
    """日次スクリーニングを実行する。"""
    print(f"[{datetime.now()}] Daily scan started")

    # 全市場（プライム+スタンダード+グロース）をスキャン
    from src.data.stocklist import get_low_price_stocks
    stocks = get_low_price_stocks()
    codes = stocks["code"].tolist()

    results = screen_stocks(codes, min_score=0)

    # 上位の推奨をDBに記録
    for r in results[:10]:
        record_recommendation(r)

    print(f"[{datetime.now()}] Scanned {len(codes)} stocks, {len(results)} passed filters")
    return results


def daily_review():
    """過去の推奨の答え合わせを行う。"""
    print(f"[{datetime.now()}] Daily review started")
    check_outcomes(days_after=30)
    update_weights()
    print(f"[{datetime.now()}] Review complete")


def run_daily():
    """日次バッチの全工程を実行する。"""
    daily_scan()
    daily_review()
