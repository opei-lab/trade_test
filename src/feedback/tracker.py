"""推奨記録・結果追跡モジュール

スクリーニングで推奨した銘柄の「その後」を追跡し、
的中/外れを記録してフィードバックに使う。
"""

from datetime import datetime, timedelta
from src.data.database import get_session, Recommendation, ScoreWeights
from src.data.price import fetch_price


def record_recommendation(result: dict):
    """スクリーニング結果をDBに記録する。"""
    session = get_session()
    rec = Recommendation(
        code=result["code"],
        name=result.get("name", ""),
        recommended_at=datetime.now(),
        entry_price=result.get("entry", 0),
        target_price=result.get("target", 0),
        strategy_type=result.get("phase", "NONE"),
    )
    session.add(rec)
    session.commit()
    session.close()


def check_outcomes(days_after: int = 30):
    """過去の推奨のうち、結果が未確定のものを確認する。

    推奨日からdays_after日が経過した推奨について、
    実際の高値・安値と損益を記録する。
    """
    session = get_session()
    cutoff = datetime.now() - timedelta(days=days_after)

    pending = session.query(Recommendation).filter(
        Recommendation.hit.is_(None),
        Recommendation.recommended_at < cutoff,
    ).all()

    for rec in pending:
        try:
            start = rec.recommended_at.strftime("%Y-%m-%d")
            end = (rec.recommended_at + timedelta(days=days_after)).strftime("%Y-%m-%d")
            df = fetch_price(rec.code, start=start, end=end)

            if df.empty:
                continue

            actual_high = float(df["High"].max())
            actual_low = float(df["Low"].min())

            # 目標到達 = 的中
            hit = 1 if actual_high >= rec.target_price else 0
            profit_pct = (actual_high - rec.entry_price) / rec.entry_price * 100 if rec.entry_price > 0 else 0

            rec.actual_high = actual_high
            rec.actual_low = actual_low
            rec.result_date = datetime.now().date()
            rec.profit_pct = round(profit_pct, 1)
            rec.hit = hit

        except Exception:
            continue

    session.commit()
    session.close()


def get_hit_rate() -> dict:
    """的中率を算出する。"""
    session = get_session()
    total = session.query(Recommendation).filter(Recommendation.hit.isnot(None)).count()
    hits = session.query(Recommendation).filter(Recommendation.hit == 1).count()
    session.close()

    rate = hits / total * 100 if total > 0 else 0
    return {"total": total, "hits": hits, "hit_rate": round(rate, 1)}
