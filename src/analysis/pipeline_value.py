"""パイプライン価値算出モジュール

バイオ・創薬銘柄の段階的な価値確定ステップを定量化する。
各ステップの成功確率（業界統計）× 市場規模から期待値を算出。

同様のロジックを他業種にも適用:
- テック: 製品ローンチ → ユーザー獲得 → 黒字化
- 製造: 受注 → 量産 → フル稼働
"""

# バイオ/創薬の各フェーズの成功確率（業界統計ベース）
# Source: BIO/QLS Advisors Clinical Development Success Rates
BIO_PHASE_SUCCESS_RATES = {
    "preclinical": 0.05,   # 前臨床 → 承認: 5%
    "phase1": 0.10,        # Phase 1 → 承認: 10%
    "phase2": 0.25,        # Phase 2 → 承認: 25%
    "phase3": 0.55,        # Phase 3 → 承認: 55%
    "filed": 0.85,         # 申請済 → 承認: 85%
    "approved": 1.0,       # 承認済
}

# 各フェーズ間の成功確率
BIO_STEP_PROBABILITIES = {
    "phase1_to_phase2": 0.52,
    "phase2_to_phase3": 0.29,
    "phase3_to_filed": 0.58,
    "filed_to_approved": 0.85,
}


def calc_staged_targets_bio(
    current_price: float,
    market_cap: float,
    target_market_size: float,
    current_phase: str = "phase2",
    market_share_pct: float = 5,
    revenue_multiple: float = 10,
) -> list[dict]:
    """バイオ銘柄の段階的な目標価格を算出する。

    Args:
        current_price: 現在の株価
        market_cap: 現在の時価総額
        target_market_size: 対象市場規模（円）
        current_phase: 現在のフェーズ
        market_share_pct: 想定市場シェア（%）
        revenue_multiple: 売上に対する時価総額倍率（PSR）

    Returns:
        [{
            "step": ステップ名,
            "target_price": 目標株価,
            "multiplier": 現在値からの倍率,
            "probability": 到達確率,
            "expected_value": 期待値（目標 × 確率）,
            "floor_after": 到達後の下値の床,
            "description": 説明,
        }]
    """
    if market_cap <= 0 or current_price <= 0:
        return []

    potential_revenue = target_market_size * (market_share_pct / 100)
    potential_market_cap = potential_revenue * revenue_multiple

    # 現在の時価総額に対する理論倍率
    full_multiplier = potential_market_cap / market_cap
    if full_multiplier < 1:
        full_multiplier = 2  # 最低でも2倍

    stages = []

    # フェーズに応じた段階を生成
    phase_order = ["phase1", "phase2", "phase3", "filed", "approved", "revenue"]
    try:
        current_idx = phase_order.index(current_phase)
    except ValueError:
        current_idx = 1  # デフォルトPhase 2

    remaining_phases = phase_order[current_idx + 1:]

    # 各段階の価値配分（後のフェーズほど大きい）
    value_weights = {
        "phase2": 0.05,    # データ良好
        "phase3": 0.15,    # Phase 3入り
        "filed": 0.30,     # 申請
        "approved": 0.60,  # 承認
        "revenue": 1.0,    # 売上実績
    }

    cumulative_prob = 1.0
    prev_price = current_price

    for phase in remaining_phases:
        # この段階到達時の時価総額
        weight = value_weights.get(phase, 0.5)
        phase_market_cap = market_cap + (potential_market_cap - market_cap) * weight
        phase_price = current_price * (phase_market_cap / market_cap)

        # 段階間の成功確率
        step_key = f"{phase_order[phase_order.index(phase) - 1]}_to_{phase}"
        step_prob = BIO_STEP_PROBABILITIES.get(step_key, 0.5)
        cumulative_prob *= step_prob

        # 到達後の下値の床（前段階の価格の80%程度）
        floor_after = prev_price * 0.8

        phase_labels = {
            "phase2": "Phase 2データ良好",
            "phase3": "Phase 3入り",
            "filed": "承認申請",
            "approved": "承認取得",
            "revenue": "売上実績（ブロックバスター）",
        }

        stages.append({
            "step": phase_labels.get(phase, phase),
            "target_price": round(phase_price),
            "multiplier": round(phase_price / current_price, 1),
            "probability": round(cumulative_prob * 100, 1),
            "expected_value": round(phase_price * cumulative_prob),
            "floor_after": round(floor_after),
            "description": f"到達時 ¥{round(phase_price):,}（{round(phase_price/current_price, 1)}倍）確率{round(cumulative_prob*100, 1)}%",
        })

        prev_price = phase_price

    return stages


def calc_staged_targets_generic(
    current_price: float,
    historical_high: float,
    prev_highs: list[float],
    stage_score: float = 0,
) -> list[dict]:
    """一般銘柄の段階的な目標価格を算出する。

    過去の具体的な高値を段階目標として使う。
    """
    stages = []
    targets = sorted(set([historical_high] + [h for h in prev_highs if h > current_price * 1.1]))

    for i, target in enumerate(targets[:4]):
        mult = target / current_price if current_price > 0 else 1

        # 過去に到達した価格なので、確率は比較的高い
        # ただし距離が遠いほど確率は下がる
        if mult < 1.3:
            prob = 70
        elif mult < 1.8:
            prob = 50
        elif mult < 3:
            prob = 30
        else:
            prob = 15

        stages.append({
            "step": f"目標{i+1}",
            "target_price": round(target),
            "multiplier": round(mult, 1),
            "probability": prob,
            "expected_value": round(target * prob / 100),
            "description": f"過去高値 ¥{round(target):,}（{round(mult, 1)}倍）",
        })

    # ステージ変化がある場合は追加目標
    if stage_score >= 20 and targets:
        new_high = max(targets) * 1.5
        stages.append({
            "step": "ステージ変化反映",
            "target_price": round(new_high),
            "multiplier": round(new_high / current_price, 1),
            "probability": 20,
            "expected_value": round(new_high * 0.2),
            "description": f"ファンダ変化による新高値 ¥{round(new_high):,}",
        })

    return stages
