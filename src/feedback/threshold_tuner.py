"""閾値チューニングモジュール

確度条件の閾値（「底値圏=20%以下」等）をバックテスト結果から
最適化する。固定値ではなく、データが教える最適閾値を見つける。
"""

import numpy as np
import pandas as pd


def find_optimal_threshold(
    bt_df: pd.DataFrame,
    feature: str,
    target: str = "is_clean_win",
    direction: str = "below",
    candidates: list = None,
) -> dict:
    """ある指標の最適な閾値を探索する。

    Args:
        bt_df: バックテスト結果DataFrame
        feature: 閾値を探す指標のカラム名
        target: 最適化対象（is_clean_win, is_quick_win, path_quality）
        direction: "below"=閾値以下で買い, "above"=閾値以上で買い
        candidates: 試す閾値のリスト（Noneならパーセンタイルから自動生成）

    Returns:
        {
            "optimal_threshold": 最適閾値,
            "hit_rate_at_optimal": その閾値での的中率,
            "baseline_rate": 全体の的中率,
            "lift": 向上分,
            "sample_size": サンプル数,
            "all_results": 全閾値の結果,
        }
    """
    if feature not in bt_df.columns or target not in bt_df.columns:
        return {"optimal_threshold": None, "error": "column not found"}

    data = bt_df[[feature, target]].dropna()
    if len(data) < 30:
        return {"optimal_threshold": None, "error": "insufficient data"}

    is_bool_target = data[target].dtype == bool or set(data[target].unique()).issubset({0, 1, True, False})
    baseline = float(data[target].mean())

    if candidates is None:
        candidates = [float(np.percentile(data[feature], p)) for p in range(5, 96, 5)]
        candidates = sorted(set(candidates))

    best_threshold = None
    best_lift = -999
    all_results = []

    for threshold in candidates:
        if direction == "below":
            mask = data[feature] <= threshold
        else:
            mask = data[feature] >= threshold

        subset = data[mask]
        if len(subset) < 10:
            continue

        if is_bool_target:
            rate = float(subset[target].mean())
        else:
            rate = float(subset[target].mean())

        lift = rate - baseline

        all_results.append({
            "threshold": round(threshold, 2),
            "hit_rate": round(rate * 100 if is_bool_target else rate, 2),
            "lift": round(lift * 100 if is_bool_target else lift, 2),
            "samples": len(subset),
        })

        if lift > best_lift and len(subset) >= 20:
            best_lift = lift
            best_threshold = threshold

    return {
        "optimal_threshold": round(best_threshold, 2) if best_threshold is not None else None,
        "hit_rate_at_optimal": round((baseline + best_lift) * 100 if is_bool_target else baseline + best_lift, 2),
        "baseline_rate": round(baseline * 100 if is_bool_target else baseline, 2),
        "lift": round(best_lift * 100 if is_bool_target else best_lift, 2),
        "sample_size": len(data[data[feature] <= best_threshold]) if best_threshold and direction == "below" else len(data[data[feature] >= best_threshold]) if best_threshold else 0,
        "all_results": all_results,
    }


def tune_all_thresholds(bt_df: pd.DataFrame, target: str = "is_clean_win") -> dict:
    """全条件の閾値を一括チューニングする。

    Returns:
        {feature_name: {optimal_threshold, lift, ...}}
    """
    tuning_config = [
        {"feature": "price_position", "direction": "below", "current": 20, "label": "底値圏の閾値（%）"},
        {"feature": "squeeze", "direction": "above", "current": 60, "label": "ボラ収縮の閾値"},
        {"feature": "divergence", "direction": "above", "current": 10, "label": "売り枯れの閾値"},
        {"feature": "accumulation", "direction": "above", "current": 5, "label": "買い集めの閾値"},
        {"feature": "volume_anomaly", "direction": "above", "current": 2.0, "label": "出来高異常の閾値"},
    ]

    results = {}
    for cfg in tuning_config:
        feature = cfg["feature"]
        if feature not in bt_df.columns:
            continue

        result = find_optimal_threshold(
            bt_df,
            feature=feature,
            target=target,
            direction=cfg["direction"],
        )
        result["current_threshold"] = cfg["current"]
        result["label"] = cfg["label"]
        results[feature] = result

    return results


def format_threshold_report(tuning_results: dict) -> str:
    """閾値チューニング結果をレポートにする。"""
    lines = ["## 閾値チューニング結果\n"]

    for feature, result in tuning_results.items():
        label = result.get("label", feature)
        current = result.get("current_threshold", "?")
        optimal = result.get("optimal_threshold")
        lift = result.get("lift", 0)

        if optimal is None:
            lines.append(f"- **{label}**: データ不足で最適化不可")
            continue

        changed = "→ 変更推奨" if abs(optimal - current) / (current + 0.01) > 0.2 else "→ 現行値で妥当"
        lines.append(f"- **{label}**: 現行={current}, 最適={optimal}, リフト={lift:+.1f}% {changed}")

    return "\n".join(lines)
