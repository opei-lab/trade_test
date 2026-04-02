"""過去データによる重み最適化モジュール

過去2-3年の株価データを使い、「どの確度条件の組み合わせが
最も安全かつ短期に利益が出たか」を検証して重みを最適化する。

重要: 単に「60日後の最大値」ではなく、
- ピークに到達するまでの日数（即効性）
- ピーク到達前の最大ドローダウン（安全性）
- 安全に入って抜けられるかどうか（パスの質）
を評価する。
"""

import pandas as pd
import numpy as np
from src.data.price import fetch_price
from src.analysis.supply import calc_supply_score
from src.analysis.manipulation.detector import detect_phase
from src.strategy.conviction import CONVICTION_CHECKS


def analyze_price_path(df: pd.DataFrame, entry_idx: int, max_hold: int = 60) -> dict:
    """エントリー後の値動きの「質」を詳細に分析する。

    単なる最大値ではなく、実際にトレードできるかどうかを評価する。
    """
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    if entry_idx >= len(close) - 10:
        return None

    end_idx = min(entry_idx + max_hold, len(close))
    entry_price = float(close.iloc[entry_idx])

    # エントリー後の全日の値動きを追跡
    running_max = entry_price
    running_min = entry_price
    max_drawdown_before_peak = 0
    peak_price = entry_price
    peak_day = 0
    first_profit_day = None  # 初めて利益が出た日

    daily_data = []
    for d in range(entry_idx + 1, end_idx):
        day_num = d - entry_idx
        h = float(high.iloc[d])
        l = float(low.iloc[d])
        c = float(close.iloc[d])

        gain_pct = (c - entry_price) / entry_price * 100
        max_gain_pct = (h - entry_price) / entry_price * 100
        intraday_dd = (entry_price - l) / entry_price * 100

        if h > peak_price:
            # 新高値更新前のドローダウンを記録
            max_drawdown_before_peak = max(max_drawdown_before_peak,
                                           (entry_price - running_min) / entry_price * 100)
            peak_price = h
            peak_day = day_num

        running_max = max(running_max, h)
        running_min = min(running_min, l)

        if first_profit_day is None and c > entry_price * 1.03:
            first_profit_day = day_num

        daily_data.append({
            "day": day_num,
            "close": c,
            "gain_pct": round(gain_pct, 1),
        })

    total_peak_gain = (peak_price - entry_price) / entry_price * 100
    total_max_dd = (entry_price - running_min) / entry_price * 100

    # パスの質スコア（高いほど安全に利益が取れるパターン）
    # = ピーク利益 / (ピーク到達前のドローダウン + 1) / (ピーク到達日数 + 1)
    if max_drawdown_before_peak <= 3:
        dd_penalty = 1.0  # ほぼ下げなし = 最高
    elif max_drawdown_before_peak <= 10:
        dd_penalty = 0.7
    elif max_drawdown_before_peak <= 20:
        dd_penalty = 0.3
    else:
        dd_penalty = 0.1  # 途中で大きく下げた = 最悪

    if peak_day <= 5:
        speed_bonus = 2.0  # 5日以内にピーク = 最速
    elif peak_day <= 10:
        speed_bonus = 1.5
    elif peak_day <= 20:
        speed_bonus = 1.0
    elif peak_day <= 40:
        speed_bonus = 0.7
    else:
        speed_bonus = 0.3

    path_quality = total_peak_gain * dd_penalty * speed_bonus

    # 各期間のリターン
    returns = {}
    for period in [5, 10, 20, 30, 60]:
        end = min(entry_idx + period, len(close) - 1)
        if end > entry_idx:
            period_gain = (float(close.iloc[end]) - entry_price) / entry_price * 100
            returns[f"return_{period}d"] = round(period_gain, 1)
        else:
            returns[f"return_{period}d"] = 0

    return {
        "entry_price": round(entry_price),
        "peak_price": round(peak_price),
        "peak_gain_pct": round(total_peak_gain, 1),
        "peak_day": peak_day,
        "max_dd_before_peak": round(max_drawdown_before_peak, 1),
        "total_max_dd": round(total_max_dd, 1),
        "first_profit_day": first_profit_day,
        "path_quality": round(path_quality, 1),
        "dd_penalty": dd_penalty,
        "speed_bonus": speed_bonus,
        **returns,
        # 分類
        "is_clean_win": total_peak_gain >= 30 and max_drawdown_before_peak <= 10 and peak_day <= 20,
        "is_quick_win": total_peak_gain >= 15 and peak_day <= 10 and max_drawdown_before_peak <= 5,
        "is_painful_win": total_peak_gain >= 30 and max_drawdown_before_peak > 15,
        "is_loss": total_peak_gain < 5,
    }


def evaluate_conditions_at_point(df_window: pd.DataFrame) -> dict:
    """ある時点のデータで各確度条件を評価する。"""
    try:
        supply = calc_supply_score(df_window)
    except Exception:
        return {}

    ctx = {
        "supply": supply,
        "ceiling_score": 50,
        "margin_ratio": 0,
        "margin_buy_change": 0,
        "max_downside_pct": 50,
        "asymmetry": 0,
        "market_cap": 0,
        "float_scarcity": 0,
        "safety_score": 50,
        "timing_score": 0,
        "stage_score": 0,
        "dilution_risk_count": 0,
        "event_proximity_score": 0,
    }

    results = {}
    for check in CONVICTION_CHECKS:
        try:
            results[check["id"]] = check["check"](ctx)
        except Exception:
            results[check["id"]] = False

    # supply内部値もフラットに追加（閾値チューニング用）
    results["price_position"] = supply.get("price_position", 50)
    results["squeeze"] = supply.get("squeeze", 0)
    results["divergence"] = supply.get("divergence", 0)
    results["accumulation"] = supply.get("accumulation", 0)
    results["volume_anomaly"] = supply.get("volume_anomaly", 0)
    results["supply_score"] = supply.get("total", 0)

    return results


def run_historical_backtest(
    codes: list[str],
    period_days: int = 730,
    sample_interval: int = 20,
    hold_days: int = 60,
    progress_callback=None,
) -> pd.DataFrame:
    """複数銘柄で大規模バックテスト実行。値動きの質まで評価。"""
    all_records = []
    total = len(codes)

    for i, code in enumerate(codes):
        if progress_callback:
            progress_callback(i, total, code)

        try:
            df = fetch_price(code, period_days=period_days)
            if df.empty or len(df) < 120:
                continue

            for idx in range(60, len(df) - hold_days, sample_interval):
                window = df.iloc[:idx]
                path = analyze_price_path(df, idx, hold_days)
                if path is None:
                    continue

                conditions = evaluate_conditions_at_point(window)
                if not conditions:
                    continue

                record = {
                    "code": code,
                    "date": str(df.index[idx].date()) if hasattr(df.index[idx], "date") else "",
                    "year": df.index[idx].year if hasattr(df.index[idx], "year") else 2024,
                    **path,
                    **conditions,
                }
                all_records.append(record)

        except Exception:
            continue

    return pd.DataFrame(all_records) if all_records else pd.DataFrame()


def optimize_weights(df: pd.DataFrame, target: str = "is_clean_win") -> dict:
    """バックテスト結果から各条件の最適な重みを算出する。

    target:
        "is_clean_win": +30%以上、途中DD10%以下、20日以内（安全に勝つ）
        "is_quick_win": +15%以上、10日以内、DD5%以下（即効性重視）
        "path_quality": パスの質スコア（総合評価）
    """
    if df.empty:
        return {}

    modern = df[df["year"] >= 2018]
    data = modern if len(modern) >= 50 else df

    if target == "path_quality":
        # 連続値の場合は平均で比較
        baseline = data[target].mean()

        results = {}
        condition_ids = [c["id"] for c in CONVICTION_CHECKS]
        for cid in condition_ids:
            if cid not in data.columns:
                continue
            with_cond = data[data[cid] == True]
            without_cond = data[data[cid] == False]
            if len(with_cond) < 10:
                results[cid] = {"weight": 3, "lift": 0, "samples": len(with_cond), "avg_quality": 0}
                continue

            avg_with = with_cond[target].mean()
            avg_without = without_cond[target].mean() if not without_cond.empty else baseline
            lift = avg_with - avg_without

            weight = 5 if lift > 50 else 4 if lift > 20 else 3 if lift > 5 else 2 if lift > 0 else 1
            results[cid] = {
                "weight": weight,
                "lift": round(lift, 1),
                "samples": len(with_cond),
                "avg_quality": round(avg_with, 1),
                "baseline_quality": round(baseline, 1),
            }
        return results

    # bool target
    baseline_rate = data[target].mean() if target in data.columns else 0
    results = {}
    condition_ids = [c["id"] for c in CONVICTION_CHECKS]

    for cid in condition_ids:
        if cid not in data.columns:
            continue

        with_cond = data[data[cid] == True]
        without_cond = data[data[cid] == False]

        if len(with_cond) < 10:
            results[cid] = {"weight": 3, "lift": 0, "samples": len(with_cond), "hit_rate": 0}
            continue

        hit_rate_with = with_cond[target].mean()
        hit_rate_without = without_cond[target].mean() if not without_cond.empty else baseline_rate
        lift = hit_rate_with - hit_rate_without

        weight = 5 if lift > 0.20 else 4 if lift > 0.10 else 3 if lift > 0.05 else 2 if lift > 0 else 1

        results[cid] = {
            "weight": weight,
            "lift": round(lift * 100, 1),
            "samples": len(with_cond),
            "hit_rate": round(hit_rate_with * 100, 1),
            "baseline_rate": round(baseline_rate * 100, 1),
        }

    return results


def find_quick_patterns(df: pd.DataFrame) -> list[dict]:
    """即効性のある勝ちパターンを抽出する。

    「入ってすぐ上がり、途中で下げず、短期で利確できる」パターンを特定。
    """
    if df.empty:
        return []

    modern = df[df["year"] >= 2018]
    data = modern if len(modern) >= 30 else df

    clean_wins = data[data["is_clean_win"] == True]
    quick_wins = data[data["is_quick_win"] == True]

    patterns = []

    # clean winで最も多い条件の組み合わせ
    if not clean_wins.empty:
        condition_ids = [c["id"] for c in CONVICTION_CHECKS if c["id"] in data.columns]
        condition_freq = {}
        for cid in condition_ids:
            freq = clean_wins[cid].mean() if cid in clean_wins.columns else 0
            all_freq = data[cid].mean() if cid in data.columns else 0
            if freq > all_freq * 1.3 and freq > 0.3:  # 全体より30%多く出現
                check_name = next((c["name"] for c in CONVICTION_CHECKS if c["id"] == cid), cid)
                condition_freq[check_name] = round(freq * 100, 1)

        if condition_freq:
            patterns.append({
                "name": "安全な勝ちパターン（+30%以上、DD10%以下、20日以内）",
                "occurrence_rate": round(len(clean_wins) / len(data) * 100, 1),
                "avg_gain": round(clean_wins["peak_gain_pct"].mean(), 1),
                "avg_days": round(clean_wins["peak_day"].mean(), 0),
                "avg_dd": round(clean_wins["max_dd_before_peak"].mean(), 1),
                "key_conditions": condition_freq,
            })

    if not quick_wins.empty:
        condition_ids = [c["id"] for c in CONVICTION_CHECKS if c["id"] in data.columns]
        condition_freq = {}
        for cid in condition_ids:
            freq = quick_wins[cid].mean() if cid in quick_wins.columns else 0
            all_freq = data[cid].mean() if cid in data.columns else 0
            if freq > all_freq * 1.3 and freq > 0.3:
                check_name = next((c["name"] for c in CONVICTION_CHECKS if c["id"] == cid), cid)
                condition_freq[check_name] = round(freq * 100, 1)

        if condition_freq:
            patterns.append({
                "name": "即効パターン（+15%以上、DD5%以下、10日以内）",
                "occurrence_rate": round(len(quick_wins) / len(data) * 100, 1),
                "avg_gain": round(quick_wins["peak_gain_pct"].mean(), 1),
                "avg_days": round(quick_wins["peak_day"].mean(), 0),
                "avg_dd": round(quick_wins["max_dd_before_peak"].mean(), 1),
                "key_conditions": condition_freq,
            })

    return patterns


def apply_optimized_weights(optimized: dict):
    """最適化された重みをCONVICTION_CHECKSに反映する。"""
    for check in CONVICTION_CHECKS:
        if check["id"] in optimized:
            check["weight"] = optimized[check["id"]]["weight"]


def format_optimization_report(optimized: dict, patterns: list = None) -> str:
    """最適化結果をレポート文字列にする。"""
    lines = ["## 重み最適化結果（過去データ検証）\n"]

    sorted_items = sorted(optimized.items(), key=lambda x: x[1].get("lift", 0), reverse=True)

    for cid, data in sorted_items:
        check_name = next((c["name"] for c in CONVICTION_CHECKS if c["id"] == cid), cid)
        lift = data.get("lift", 0)
        weight = data.get("weight", 3)
        hit_rate = data.get("hit_rate", data.get("avg_quality", 0))
        samples = data.get("samples", 0)

        arrow = "+" if lift > 0 else ""
        lines.append(f"- **{check_name}** (重み{weight}): {arrow}{lift}%リフト（{samples}件）")

    if patterns:
        lines.append("\n## 発見されたパターン\n")
        for p in patterns:
            lines.append(f"### {p['name']}")
            lines.append(f"- 発生率: {p['occurrence_rate']}%")
            lines.append(f"- 平均利益: +{p['avg_gain']}%")
            lines.append(f"- 平均日数: {p['avg_days']:.0f}日")
            lines.append(f"- 平均DD: -{p['avg_dd']}%")
            lines.append(f"- 条件: {', '.join(f'{k}({v}%)' for k, v in p['key_conditions'].items())}")

    return "\n".join(lines)
