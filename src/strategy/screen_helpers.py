"""スクリーニング補助関数

screener.pyから分離したヘルパー関数群。
価格目標算出、エントリー/イグジット計算、推奨理由生成等。
"""

import numpy as np
import pandas as pd


def calc_float_scarcity(info: dict, avg_volume: float) -> float:
    """浮動株枯渇度を算出する。"""
    float_shares = info.get("float_shares", 0)
    if float_shares <= 0 or avg_volume <= 0:
        return 0
    turnover_days = float_shares / avg_volume
    if turnover_days <= 0:
        return 0
    return round(max(0, min(100, (1 / turnover_days) * 500)), 1)


def find_price_targets(df: pd.DataFrame) -> dict:
    """過去の具体的な価格水準から目標候補を導出する。"""
    close = df["Close"]
    high = df["High"]
    volume = df["Volume"]
    current = float(close.iloc[-1])

    price_bins = pd.cut(close, bins=30)
    vol_by_price = df.groupby(price_bins, observed=True)["Volume"].sum()
    top_levels = vol_by_price.nlargest(8)

    supports = []
    resistances = []
    for interval in top_levels.index:
        mid = float((interval.left + interval.right) / 2)
        if mid < current * 0.97:
            supports.append(mid)
        elif mid > current * 1.03:
            resistances.append(mid)

    prev_highs = []
    window = 20
    if len(high) > window * 2:
        rolling_max = high.rolling(window, center=True).max()
        peaks = high[(high == rolling_max) & (high > current * 1.1)]
        peak_values = sorted(peaks.unique(), reverse=True)
        for pv in peak_values[:5]:
            pv = float(pv)
            if not any(abs(pv - ph) / pv < 0.05 for ph in prev_highs):
                prev_highs.append(pv)

    historical_high = float(close.max())
    historical_low = float(close.min())

    all_targets = set()
    for r in resistances:
        all_targets.add(round(r))
    for ph in prev_highs:
        all_targets.add(round(ph))
    all_targets.add(round(historical_high))

    targets = sorted([t for t in all_targets if t > current * 1.05])

    return {
        "targets": targets,
        "supports": sorted(supports, reverse=True),
        "resistances": sorted(resistances),
        "historical_high": historical_high,
        "historical_low": historical_low,
        "prev_highs": prev_highs,
    }


def estimate_timeframe(df: pd.DataFrame, entry: float, target: float) -> dict:
    """過去の同じ価格帯の値動きから、目標到達までの期間を推定する。"""
    close = df["Close"]
    if len(close) < 60:
        return {"estimated_days": None, "confidence": "low", "method": "insufficient_data", "description": "データ不足"}

    move_pct = (target - entry) / entry
    daily_returns = close.pct_change()
    durations = []

    for i in range(len(close) - 20):
        start_price = float(close.iloc[i])
        target_price = start_price * (1 + move_pct)
        for j in range(i + 1, min(i + 252, len(close))):
            if float(close.iloc[j]) >= target_price:
                durations.append(j - i)
                break

    if durations:
        median_days = int(np.median(durations))
        q25_days = int(np.percentile(durations, 25))
        q75_days = int(np.percentile(durations, 75))
        confidence = "high" if len(durations) >= 5 else "medium" if len(durations) >= 2 else "low"
        return {
            "estimated_days": median_days,
            "fast_case_days": q25_days,
            "slow_case_days": q75_days,
            "sample_count": len(durations),
            "confidence": confidence,
            "method": "historical_pattern",
            "description": f"過去{len(durations)}回の実績: 中央値{median_days}日（早くて{q25_days}日、遅くて{q75_days}日）",
        }

    positive_days = daily_returns[daily_returns > 0]
    if not positive_days.empty:
        avg_daily_gain = float(positive_days.mean())
        if avg_daily_gain > 0:
            up_days_needed = move_pct / avg_daily_gain
            up_ratio = len(positive_days) / len(daily_returns.dropna())
            if up_ratio > 0:
                total_days = int(up_days_needed / up_ratio)
                return {
                    "estimated_days": total_days,
                    "confidence": "low",
                    "method": "velocity_estimate",
                    "description": f"過去の上昇速度から推定: 約{total_days}日（参考値）",
                }

    return {"estimated_days": None, "confidence": "low", "method": "unknown", "description": "推定不能"}


def calc_entry_exit(df: pd.DataFrame, supply: dict, phase: dict, info: dict = None) -> dict:
    """エントリー/イグジット価格を具体的な価格水準から算出する。"""
    close = df["Close"]
    current = float(close.iloc[-1])
    price_levels = find_price_targets(df)

    supports = price_levels["supports"]
    if supports:
        entry = round(max(supports))
    else:
        entry = round(current * 0.95)

    targets = price_levels["targets"]
    historical_high = price_levels["historical_high"]

    if targets:
        double_targets = [t for t in targets if t >= entry * 2]
        if double_targets:
            target = double_targets[0]
        else:
            target = targets[-1]
    else:
        target = round(historical_high)

    stage_score = 0
    if isinstance(supply, dict):
        stage_score = supply.get("stage_score", 0)

    if stage_score >= 20:
        target = max(target, round(historical_high))
    elif info:
        forward_pe = info.get("forwardPE", 0)
        trailing_pe = info.get("trailingPE", 0)
        if forward_pe and trailing_pe and forward_pe > 0 and trailing_pe > forward_pe * 1.3:
            growth_ceiling = current * (trailing_pe / forward_pe)
            target = max(target, round(min(growth_ceiling, historical_high)))
        else:
            target = min(target, round(historical_high))

    target = min(target, round(historical_high))
    stop_loss = round(entry * 0.90)

    p = phase.get("phase", "NONE")
    if p == "D":
        entry = round(current)
        stop_loss = round(current * 0.85)

    reward_pct = (target - entry) / entry * 100 if entry > 0 else 0
    risk_pct = (entry - stop_loss) / entry * 100 if entry > 0 else 10
    risk_reward = reward_pct / risk_pct if risk_pct > 0 else 0

    if current <= entry * 1.03:
        timing = "NOW"
    elif current <= entry * 1.10:
        timing = "NEAR"
    else:
        timing = "WAIT"

    timeframe = estimate_timeframe(df, entry, target)

    return {
        "entry": entry,
        "target": target,
        "stop_loss": stop_loss,
        "reward_pct": round(reward_pct, 1),
        "risk_pct": round(risk_pct, 1),
        "risk_reward": round(risk_reward, 2),
        "timing": timing,
        "multiplier": round(target / entry, 1) if entry > 0 else 0,
        "target_basis": "過去の高値" if targets else "過去最高値",
        "prev_highs": price_levels.get("prev_highs", []),
        "timeframe": timeframe,
    }


def build_reason(supply: dict, phase: dict, trade: dict, info: dict = None) -> str:
    """推奨理由を定量データのみで生成する。"""
    reasons = []

    if supply.get("is_bottom"):
        reasons.append("底値圏（売り枯れ+ボラ収縮）")

    vol_anom = supply.get("volume_anomaly", 0)
    if vol_anom > 2:
        reasons.append(f"出来高が平常の{vol_anom:.1f}倍")

    squeeze = supply.get("squeeze", 0)
    if squeeze > 70:
        reasons.append(f"ボラ収縮{squeeze:.0f}%（爆発前）")

    divergence = supply.get("divergence", 0)
    if divergence > 20:
        reasons.append("売り枯れ（株価下落+出来高減少）")

    if info:
        market_cap = info.get("market_cap", 0)
        if 0 < market_cap < 10e9:
            reasons.append(f"時価総額{market_cap/1e8:.0f}億（小型、動きやすい）")

        float_shares = info.get("float_shares", 0)
        outstanding = info.get("shares_outstanding", 0)
        if outstanding > 0 and float_shares > 0:
            float_ratio = float_shares / outstanding * 100
            if float_ratio < 30:
                reasons.append(f"浮動株比率{float_ratio:.0f}%（希少）")

        avg_vol = info.get("average_volume", 0)
        if float_shares > 0 and avg_vol > 0:
            turnover = float_shares / avg_vol
            if turnover < 30:
                reasons.append(f"浮動株回転{turnover:.0f}日（枯渇気味）")

    p = phase.get("phase", "NONE")
    phase_reasons = {
        "A": "出来高漸増、買い集め兆候",
        "B": "試し上げ後の調整、次の動き注目",
        "C": "振るい落とし検出、回復兆候",
        "D": "本上昇中、利確タイミング注意",
    }
    if p in phase_reasons:
        reasons.append(phase_reasons[p])

    stage_summary = trade.get("stage_summary", "")
    if stage_summary and stage_summary != "特筆すべきステージ変化なし":
        reasons.insert(0, stage_summary)

    ceiling = trade.get("ceiling", {})
    if ceiling.get("ceiling_score", 0) < 20:
        reasons.append("上値軽い（しこり少）")
    for cr in ceiling.get("reasons", [])[:2]:
        if cr:
            reasons.append(cr)

    margin = trade.get("margin", {})
    if margin.get("margin_ratio", 0) > 0 and margin["margin_ratio"] < 1:
        reasons.append(f"売り長{margin['margin_ratio']:.1f}倍（踏み上げ期待）")

    safety = trade.get("safety", {})
    floor = trade.get("floor", {})

    if floor.get("floor_price"):
        reasons.append(f"下値の床¥{floor['floor_price']:,}（{floor.get('floor_type', '')}）")
    if floor.get("max_downside_pct", 100) < 20:
        reasons.append(f"最大下落-{floor['max_downside_pct']:.0f}%（限定的）")
    if trade.get("asymmetry", 0) >= 70:
        reasons.append(f"非対称リターン（上方{trade['reward_pct']:.0f}% vs 下方-{floor.get('max_downside_pct', 0):.0f}%）")

    if trade["timing"] == "NOW":
        reasons.append("現在値がエントリー圏内")
    if trade["risk_reward"] >= 3:
        reasons.append(f"RR比{trade['risk_reward']:.1f}（良好）")
    if trade.get("multiplier", 0) >= 5:
        reasons.append(f"インパクト倍率{trade['multiplier']:.1f}x")

    return " / ".join(reasons) if reasons else "需給スコアが基準以上"
