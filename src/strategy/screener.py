"""自動スクリーニングモジュール

全銘柄を自動分析し、有望銘柄をスコア順にランキングする。
目標価格は全て定量データ（需給、時価総額、浮動株、出来高、過去値動き）から算出。
専門家の意見やアナリスト予想は一切使わない。
"""

import pandas as pd
import numpy as np
from src.data.price import fetch_price, get_stock_info
from src.analysis.supply import calc_supply_score, calc_price_position
from src.analysis.manipulation.detector import detect_phase
from src.analysis.safety import calc_downside_floor, calc_asymmetry_score, is_pure_manipulation
from src.analysis.resistance import calc_ceiling_score, detect_volume_vacuum
from src.data.margin import fetch_margin_data, calc_margin_pressure
from src.strategy.conviction import calc_conviction
from src.analysis.timing import calc_timing_score
from src.analysis.stage_change import detect_financial_stage_change, format_stage_summary
from src.analysis.event_proximity import find_upcoming_events, calc_event_proximity_score
from src.analysis.backtest import backtest_stock, find_winning_patterns, estimate_realistic_target
from src.analysis.pipeline_value import calc_staged_targets_bio, calc_staged_targets_generic
from src.strategy.multi_trade import generate_multi_trade_plan
from src.ml.predictor import predict_win_probability


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
    """過去の具体的な価格水準から目標候補を導出する。

    全て「過去に実際にあった価格」または「出来高が集中した価格帯」
    から算出する。ファクター掛け算はしない。

    Returns:
        {
            "targets": [価格候補のリスト（低い順）],
            "supports": [サポート価格],
            "resistances": [レジスタンス価格],
            "historical_high": 期間内最高値,
            "historical_low": 期間内最安値,
            "prev_highs": 過去の主要な高値,
        }
    """
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]
    current = float(close.iloc[-1])

    # 1. 出来高プロファイルからサポート/レジスタンス
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

    # 2. 過去の主要な高値（ピーク検出）
    prev_highs = []
    window = 20
    if len(high) > window * 2:
        rolling_max = high.rolling(window, center=True).max()
        peaks = high[(high == rolling_max) & (high > current * 1.1)]
        # 重複除去（近い価格をまとめる）
        peak_values = sorted(peaks.unique(), reverse=True)
        for pv in peak_values[:5]:
            pv = float(pv)
            if not any(abs(pv - ph) / pv < 0.05 for ph in prev_highs):
                prev_highs.append(pv)

    historical_high = float(close.max())
    historical_low = float(close.min())

    # 目標候補を統合（現在値より上のもの）
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
    """過去の同じ価格帯の値動きから、目標到達までの期間を推定する。

    「過去にこの水準からあの水準まで何日かかったか」を実績ベースで出す。

    Returns:
        {
            "estimated_days": 推定日数,
            "confidence": 推定の信頼度,
            "method": 推定方法,
            "description": 説明,
        }
    """
    close = df["Close"]
    if len(close) < 60:
        return {"estimated_days": None, "confidence": "low", "method": "insufficient_data", "description": "データ不足"}

    move_pct = (target - entry) / entry

    # 方法1: 過去に同程度の上昇が起きた時の所要日数
    daily_returns = close.pct_change()
    durations = []

    for i in range(len(close) - 20):
        start_price = float(close.iloc[i])
        # この地点から目標倍率に到達した日を探す
        target_price = start_price * (1 + move_pct)
        for j in range(i + 1, min(i + 252, len(close))):  # 最大1年
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

    # 方法2: 過去の平均的な上昇速度から推定
    positive_days = daily_returns[daily_returns > 0]
    if not positive_days.empty:
        avg_daily_gain = float(positive_days.mean())
        if avg_daily_gain > 0:
            # move_pctを達成するのに必要な「上昇日」の数
            up_days_needed = move_pct / avg_daily_gain
            # 上昇日の割合（全営業日のうち何割が上昇か）
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

    # エントリー: 直近のサポート
    supports = price_levels["supports"]
    if supports:
        entry = round(max(supports))
    else:
        entry = round(current * 0.95)

    # 目標: 過去高値から選定し、しこりの手前で補正
    targets = price_levels["targets"]
    historical_high = price_levels["historical_high"]
    resistances = price_levels.get("resistances", [])

    if targets:
        double_targets = [t for t in targets if t >= entry * 2]
        if double_targets:
            target = double_targets[0]
        else:
            target = targets[-1]
    else:
        target = round(historical_high)

    # しこり vs 大口の力関係で目標を判定
    # しこりがあっても大口が消化できるなら目標はそのまま

    # ステージ変化がある場合のみ過去最高値を超える目標を許可
    stage_score = 0
    if isinstance(supply, dict):
        stage_score = supply.get("stage_score", 0)

    if stage_score >= 20:
        # ステージ変化あり: 過去最高値を目標上限として許容（実績価格のみ）
        target = max(target, round(historical_high))
    elif info:
        # ステージ変化はないが、市場規模から理論上限を算出できる場合
        market_cap = info.get("market_cap", 0)
        # Forward PEやPEGから成長織り込み度を判定
        forward_pe = info.get("forwardPE", 0)
        trailing_pe = info.get("trailingPE", 0)
        if forward_pe and trailing_pe and forward_pe > 0 and trailing_pe > forward_pe * 1.3:
            # 将来利益が大幅に増える見込み（Forward PE << Trailing PE）
            # 理論上限 = 現在値 × (Trailing PE / Forward PE)
            growth_ceiling = current * (trailing_pe / forward_pe)
            target = max(target, round(min(growth_ceiling, historical_high)))
        else:
            # 成長見込みなし: 過去高値が上限
            target = min(target, round(historical_high))

    # 目標は過去の実績価格を超えない（過去高値が上限）
    # reward_pctが基準未満ならフィルタで落ちる
    target = min(target, round(historical_high))

    # 損切り: エントリーの-10%
    stop_loss = round(entry * 0.90)

    # 仕手フェーズによる微調整
    p = phase.get("phase", "NONE")
    if p == "D":
        entry = round(current)
        stop_loss = round(current * 0.85)

    reward_pct = (target - entry) / entry * 100 if entry > 0 else 0
    risk_pct = (entry - stop_loss) / entry * 100 if entry > 0 else 10
    risk_reward = reward_pct / risk_pct if risk_pct > 0 else 0

    # タイミング判定
    if current <= entry * 1.03:
        timing = "NOW"
    elif current <= entry * 1.10:
        timing = "NEAR"
    else:
        timing = "WAIT"

    # 期間推定
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

    # 需給根拠
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

    # 浮動株・時価総額根拠
    if info:
        market_cap = info.get("market_cap", 0)
        if 0 < market_cap < 10e9:  # 100億未満
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

    # フェーズ根拠
    p = phase.get("phase", "NONE")
    phase_reasons = {
        "A": "出来高漸増、買い集め兆候",
        "B": "試し上げ後の調整、次の動き注目",
        "C": "振るい落とし検出、回復兆候",
        "D": "本上昇中、利確タイミング注意",
    }
    if p in phase_reasons:
        reasons.append(phase_reasons[p])

    # ステージ変化根拠（最重要）
    stage_summary = trade.get("stage_summary", "")
    if stage_summary and stage_summary != "特筆すべきステージ変化なし":
        reasons.insert(0, stage_summary)  # 先頭に配置

    # 上値の重さ根拠
    ceiling = trade.get("ceiling", {})
    if ceiling.get("ceiling_score", 0) < 20:
        reasons.append("上値軽い（しこり少）")
    for cr in ceiling.get("reasons", [])[:2]:
        if cr:
            reasons.append(cr)

    margin = trade.get("margin", {})
    if margin.get("margin_ratio", 0) > 0 and margin["margin_ratio"] < 1:
        reasons.append(f"売り長{margin['margin_ratio']:.1f}倍（踏み上げ期待）")

    # 安全性根拠
    safety = trade.get("safety", {})
    floor = trade.get("floor", {})

    if floor.get("floor_price"):
        reasons.append(f"下値の床¥{floor['floor_price']:,}（{floor.get('floor_type', '')}）")
    if floor.get("max_downside_pct", 100) < 20:
        reasons.append(f"最大下落-{floor['max_downside_pct']:.0f}%（限定的）")
    if trade.get("asymmetry", 0) >= 70:
        reasons.append(f"非対称リターン（上方{trade['reward_pct']:.0f}% vs 下方-{floor.get('max_downside_pct', 0):.0f}%）")

    # 売買根拠
    if trade["timing"] == "NOW":
        reasons.append("現在値がエントリー圏内")
    if trade["risk_reward"] >= 3:
        reasons.append(f"RR比{trade['risk_reward']:.1f}（良好）")
    if trade.get("multiplier", 0) >= 5:
        reasons.append(f"インパクト倍率{trade['multiplier']:.1f}x")

    return " / ".join(reasons) if reasons else "需給スコアが基準以上"


def screen_stocks(
    codes: list[str],
    period_days: int = 365,
    min_score: float = 40,
    progress_callback=None,
) -> list[dict]:
    """6段階フィルタ。確定情報から順に、精度重視で間引く。

    Stage 1: 環境（499→~150）— 価格・流動性。即判定
    Stage 2: 信用（~150→~140）— 致命的な信用倍率だけ除外
    Stage 3: 構造（~140→~50）— しこり・真空・Phase・上値余地
    Stage 4: 需給（~50→~30）— 売り枯れ・出来高トレンド
    Stage 5: 動意（~30→10）— 出来高点火+残り上値余地でスコアリング
    → 上位10件をStage 6（情報分析: ファンダ+IR同時評価）に送る
    """
    import logging
    processed = set()
    total = len(codes)

    # ============================================================
    # Stage 1: 環境フィルタ（全銘柄。超高速）
    # 価格・流動性・データ量。触れない銘柄を除外
    # ============================================================
    stage1 = []
    for i, code in enumerate(codes):
        if progress_callback:
            progress_callback(i, total, code)

        if code in processed:
            continue
        processed.add(code)

        try:
            df = fetch_price(code, period_days=period_days)
            if df.empty or len(df) < 60:
                continue

            current = float(df["Close"].iloc[-1])

            if current > 5000:
                continue

            avg_volume_20d = float(df["Volume"].tail(20).mean())
            avg_turnover = avg_volume_20d * current
            if avg_volume_20d < 1000:
                continue
            if avg_turnover < 1_000_000:
                continue

            stage1.append({"code": code, "df": df, "current": current,
                            "avg_volume": avg_volume_20d, "avg_turnover": avg_turnover})
        except Exception:
            continue

    logging.info(f"Stage 1 環境: {total}→{len(stage1)}")

    # ============================================================
    # Stage 2: 構造フィルタ（「上がれる構造か」）
    # しこり・真空・Phase・上値余地。dfのみの計算。高速
    # ============================================================
    stage2 = []
    for item in stage1:
        code = item["code"]
        df = item["df"]
        current = item["current"]

        try:
            info = {}
            supply = calc_supply_score(df)
            phase = detect_phase(df)
            trade = calc_entry_exit(df, supply, phase)
            price_levels = find_price_targets(df)

            price_position = supply.get("price_position", 50)

            # フェーズE（売り崩し後）は即除外
            if phase.get("phase") == "E":
                continue

            # 損切り-15%固定
            trade["stop_loss"] = round(trade["entry"] * 0.85)
            trade["risk_pct"] = 15
            trade["risk_reward"] = trade["reward_pct"] / 15 if trade["reward_pct"] > 0 else 0

            # 上値の重さ・真空地帯
            _ceil = calc_ceiling_score(df)
            _ceiling_score = _ceil.get("ceiling_score", 50)
            _vac = detect_volume_vacuum(df)

            # しこりが最悪（直近高値圏に出来高密集）→ 除外
            if _ceiling_score >= 70:
                continue

            # 安値切り上げ（底固め進行中か）
            _higher_lows = False
            if len(df) >= 60:
                _lows = df["Low"].tail(60)
                _q1_low = float(_lows.iloc[:20].min())
                _q2_low = float(_lows.iloc[20:40].min())
                _q3_low = float(_lows.iloc[40:].min())
                _higher_lows = _q1_low <= _q2_low <= _q3_low

            # 構造的に上がれる余地があるか
            has_upside = (
                trade["reward_pct"] >= 30  # 最低30%の上値余地
                or _vac.get("has_vacuum", False)  # 真空地帯がある
                or (price_position <= 15 and current < 500)  # 超低位の深底値
            )
            if not has_upside:
                continue

            # ヒストリカルレンジ
            recent_6m = df.tail(min(120, len(df)))
            historical_range = float(recent_6m["Close"].max()) / max(float(recent_6m["Close"].min()), 1)
            historical_high = price_levels.get("historical_high", current)

            # 勝ちパターンフラグ
            is_best_pattern = (price_position < 15 and historical_range >= 3)
            is_good_pattern = (price_position < 25 and historical_range >= 2.5)

            floor = calc_downside_floor(df, {})
            asymmetry = calc_asymmetry_score(trade["reward_pct"], floor.get("max_downside_pct", 20))
            _overhead_pct = _ceil.get("overhead_supply", {}).get("total_overhead_pct", 0)
            _has_vacuum = _vac.get("has_vacuum", False)
            _vacuum_width = _vac.get("vacuum_width_pct", 0)

            # 出来高トレンド
            _volume_trend = 1.0
            if len(df) >= 40:
                _vol_recent = float(df["Volume"].tail(20).mean())
                _vol_prev = float(df["Volume"].iloc[-40:-20].mean())
                _volume_trend = _vol_recent / _vol_prev if _vol_prev > 0 else 1.0

            stage2.append({
                "code": code,
                "df": df,
                "current_price": current,
                "name": "",
                "is_best_pattern": is_best_pattern,
                "is_good_pattern": is_good_pattern,
                "historical_range": round(historical_range, 1),
                "ret_3d": round((current - float(df["Close"].iloc[-4])) / float(df["Close"].iloc[-4]) * 100, 1) if len(df) >= 4 else 0,
                "supply_score": supply.get("total", 0),
                "float_scarcity": 0,
                "market_cap": 0,
                "phase": phase.get("phase", "NONE"),
                "phase_confidence": phase.get("confidence", 0),
                "phase_desc": phase.get("description", ""),
                "entry": trade["entry"],
                "target": trade["target"],
                "stop_loss": trade["stop_loss"],
                "reward_pct": trade["reward_pct"],
                "risk_pct": trade["risk_pct"],
                "risk_reward": trade["risk_reward"],
                "multiplier": trade.get("multiplier", 0),
                "timing": trade["timing"],
                "is_bottom": supply.get("is_bottom", False),
                "volume_anomaly": supply.get("volume_anomaly", 0),
                "squeeze": supply.get("squeeze", 0),
                "safety_score": 50,
                "floor_price": floor.get("floor_price", 0),
                "max_downside_pct": floor.get("max_downside_pct", 0),
                "asymmetry": asymmetry,
                "risk_factors": [],
                "ceiling_score": _ceiling_score,
                "margin_ratio": 0,
                "margin_score": 50,
                "margin_reason": "",
                "overhead_pct": _overhead_pct,
                "margin_buy_change": 0,
                "price_position": supply.get("price_position", 50),
                "divergence": supply.get("divergence", 0),
                "accumulation": supply.get("accumulation", 0),
                "ml_win_prob": None,
                "has_vacuum": _has_vacuum,
                "vacuum_desc": _vac.get("description", ""),
                "vacuum_width_pct": _vacuum_width,
                "volume_trend": round(_volume_trend, 2),
                "higher_lows": _higher_lows,
                "stage_score": 0,
                "stage_changes": [],
                "stage_risks": [],
                "market_gap": "none",
                "dilution_risk_count": 0,
                "stage_summary": "",
            })
        except Exception:
            continue

    logging.info(f"Stage 2 構造: {len(stage1)}→{len(stage2)}")

    # ============================================================
    # Stage 3: 需給フィルタ（「需給が味方しているか」）
    # 確定的にダメなものだけ切る。上位50件に絞る
    # ============================================================
    stage3 = []
    for r in stage2:
        if r.get("volume_trend", 1.0) < 0.5:
            continue
        if r.get("max_downside_pct", 30) > 50:
            continue
        if r.get("supply_score", 0) < 20:
            continue
        if r.get("reward_pct", 0) < 15:
            continue
        stage3.append(r)

    stage3.sort(key=lambda x: x.get("supply_score", 0), reverse=True)
    stage3 = stage3[:50]

    logging.info(f"Stage 3 需給: {len(stage2)}→{len(stage3)}")

    # ============================================================
    # Stage 4: 信用致命判定（50件だけに適用。5-10倍=勝率10%を除外）
    # ============================================================
    from src.analysis.funda_score import calc_margin_score

    stage4 = []
    for r in stage3:
        code = r["code"]
        try:
            margin = fetch_margin_data(code)
            ms = calc_margin_score(margin.get("margin_ratio", 0))
            r["margin_ratio"] = ms["margin_ratio"]
            r["margin_score"] = ms["margin_score"]
            r["margin_reason"] = ms["margin_reason"]
            r["margin_buy_change"] = margin.get("margin_buy_change", 0)

            if ms["is_fatal"]:
                continue
        except Exception:
            pass
        stage4.append(r)

    logging.info(f"Stage 4 信用: {len(stage3)}→{len(stage4)}")

    # ============================================================
    # Stage 5: 動意スコアリング（「動き始めてるか」+「残り上値余地」）
    # ============================================================
    for r in stage4:
        df = r.get("df")
        try:
            # タイミング
            timing_result = calc_timing_score(df)
            r["timing_score"] = timing_result["timing_score"]
            r["urgency"] = timing_result["urgency"]
            r["timing_signals"] = timing_result["signals"]
            r["timing_desc"] = timing_result["description"]

            # イベント接近
            events = find_upcoming_events("", "")
            event_prox = calc_event_proximity_score(events)
            r["event_proximity_score"] = event_prox["score"]
            r["event_description"] = event_prox.get("description", "")
            r["upcoming_events"] = events[:3]

            # 理由テキスト
            r["reason"] = build_reason(
                {"is_bottom": r["is_bottom"], "volume_anomaly": r["volume_anomaly"],
                 "squeeze": r["squeeze"], "divergence": r["divergence"]},
                {"phase": r["phase"]},
                r, {},
            )

            # 確度（Stage 1時点。Stage 2で再計算）
            r["conviction"] = calc_conviction(r)
            r["conviction_grade"] = r["conviction"]["grade"]
            r["conviction_count"] = r["conviction"]["conviction_count"]
        except Exception:
            r["timing_score"] = 0
            r["urgency"] = "watching"
            r["timing_signals"] = []
            r["timing_desc"] = ""

    # === 動意スコアでソート ===
    # バックテスト結果: divergence（売り枯れ）+ vol_ignition（出来高点火）+ vacuum（真空）が有効
    def calc_motion_score(r):
        score = 0

        # 売り枯れ度（バックテストで最も差が出た指標）
        div = r.get("divergence", 0)
        if div > 20:
            score += 30
        elif div > 0:
            score += 15

        # 出来高点火（残り上値余地とセットで評価）
        reward_remaining = r.get("reward_pct", 0)
        vol_anom = r.get("volume_anomaly", 1)
        if r.get("timing_score", 0) >= 25 or (1.3 <= vol_anom <= 3):
            if reward_remaining >= 50:
                score += 30  # 動き始め + まだ上値たっぷり
            elif reward_remaining >= 30:
                score += 15  # 動き始め + そこそこ余地
            else:
                score -= 10  # 動いたけどもう遅い

        # 真空地帯（一気抜けの余地）
        if r.get("has_vacuum"):
            score += 20

        # 安値切り上げ（底固め）
        if r.get("higher_lows"):
            score += 15

        # フェーズ（大口仕込み兆候。Dは上値余地次第）
        phase = r.get("phase", "NONE")
        if phase == "D":
            # 急上昇中。上値余地があればまだ乗れる、なければ高値掴み
            score += 20 if reward_remaining >= 50 else -15
        else:
            score += {"A": 25, "B": 15, "C": 20, "NONE": 0}.get(phase, 0)

        # 上値の軽さ
        ceiling = r.get("ceiling_score", 50)
        if ceiling < 30:
            score += 15
        elif ceiling >= 55:
            score -= 10

        # 非対称性（上値>下値）
        if r.get("asymmetry", 0) > 70:
            score += 10

        return score

    for r in stage4:
        r["motion_score"] = calc_motion_score(r)

    stage4.sort(key=lambda x: x.get("motion_score", 0), reverse=True)

    # 上位10件をStage 6（情報分析: ファンダ+IR同時評価）に送る
    stage5 = stage4[:10]

    # dfを除外（JSONシリアライズ不可）
    for r in stage5:
        r.pop("df", None)

    logging.info(f"Stage 5 動意: {len(stage4)}→{len(stage5)}")

    # ファンダはStage 6（deep_analyze）でIRと同時に独立評価
    # ここでは切らない。スコアカードとして表示するだけ
    return stage5
