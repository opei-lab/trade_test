"""パラメータ自動最適化モジュール

的中/外れのデータから、確度条件の重みを自動調整する。
「このConditionが合致していた推奨は的中率が高い」→ 重みを上げる。
"""

from datetime import datetime
from src.data.database import get_session, Recommendation, ScoreWeights


def calc_condition_effectiveness() -> dict:
    """各確度条件の有効性を算出する。

    （Phase 5の自走改善で使用。十分なデータが溜まってから機能する）

    Returns:
        {condition_id: {"hit_rate": 的中率, "sample_size": サンプル数}}
    """
    # 現時点ではデータが不足しているため、スタブ実装
    # 推奨記録が100件以上溜まったら実データで計算する
    session = get_session()
    total = session.query(Recommendation).filter(Recommendation.hit.isnot(None)).count()
    session.close()

    if total < 30:
        return {"status": "insufficient_data", "total": total, "required": 30}

    return {"status": "ready", "total": total}


def update_weights():
    """的中率データから重みを更新する。

    十分なデータが溜まるまでは何もしない。
    """
    effectiveness = calc_condition_effectiveness()
    if effectiveness.get("status") != "ready":
        return effectiveness

    # TODO: 十分なデータが溜まった時点で実装
    # 各推奨のconviction条件合致情報を保存する仕組みが必要
    return {"status": "updated"}
