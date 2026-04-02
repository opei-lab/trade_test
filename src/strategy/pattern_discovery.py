"""勝ちパターン発見エンジン

複数指標の組み合わせから勝率が高いパターンを過去データで探索する。
単一指標ではなく、「底値圏 + 出来高急増 + 信用改善」のような
複合条件で勝率が跳ね上がるパターンを発見する。
"""

import pandas as pd
import numpy as np
from itertools import combinations
from src.data.price import fetch_price
from src.analysis.supply import calc_supply_score
from src.feedback.historical_optimizer import analyze_price_path


# 指標の定義（閾値付き）
INDICATORS = {
    "deep_bottom": {
        "label": "深底値圏（10%以下）",
        "extract": lambda s: s.get("price_position", 50),
        "condition": lambda v: v < 10,
    },
    "bottom": {
        "label": "底値圏（25%以下）",
        "extract": lambda s: s.get("price_position", 50),
        "condition": lambda v: v < 25,
    },
    "vol_surge": {
        "label": "出来高急増（2.8倍超）",
        "extract": lambda s: s.get("volume_anomaly", 0),
        "condition": lambda v: v >= 2.8,
    },
    "vol_increase": {
        "label": "出来高増加（1.5倍超）",
        "extract": lambda s: s.get("volume_anomaly", 0),
        "condition": lambda v: v >= 1.5,
    },
    "vol_dry": {
        "label": "出来高枯渇（0.5倍以下）",
        "extract": lambda s: s.get("volume_anomaly", 0),
        "condition": lambda v: v <= 0.5,
    },
    "squeeze_tight": {
        "label": "ボラ極限収縮（80%超）",
        "extract": lambda s: s.get("squeeze", 0),
        "condition": lambda v: v > 80,
    },
    "divergence_pos": {
        "label": "売り枯れ（乖離+20超）",
        "extract": lambda s: s.get("divergence", 0),
        "condition": lambda v: v > 20,
    },
    "accumulation_strong": {
        "label": "強い買い集め（+25超）",
        "extract": lambda s: s.get("accumulation", 0),
        "condition": lambda v: v > 25,
    },
    "accumulation_at_bottom": {
        "label": "底値圏で買い集め",
        "extract": lambda s: (s.get("accumulation", 0), s.get("price_position", 50)),
        "condition": lambda v: v[0] > 5 and v[1] < 30,
    },
    "high_supply_score": {
        "label": "需給スコア高（50超）",
        "extract": lambda s: s.get("total", 0),
        "condition": lambda v: v > 50,
    },
}


def evaluate_indicators(supply: dict) -> dict:
    """supply結果に対して全指標を評価する。"""
    results = {}
    for key, ind in INDICATORS.items():
        try:
            value = ind["extract"](supply)
            results[key] = ind["condition"](value)
        except Exception:
            results[key] = False
    return results


def discover_patterns(
    codes: list[str],
    period_days: int = 730,
    sample_interval: int = 15,
    hold_days: int = 60,
    min_combo_size: int = 2,
    max_combo_size: int = 4,
    min_samples: int = 20,
    progress_callback=None,
) -> dict:
    """全銘柄の過去データから勝ちパターンを探索する。

    Returns:
        {
            "patterns": [{
                "conditions": [条件名リスト],
                "clean_win_rate": 安全勝ち率,
                "quick_win_rate": 即効勝ち率,
                "avg_gain": 平均利益,
                "avg_peak_day": 平均ピーク日,
                "avg_dd": 平均DD,
                "samples": サンプル数,
                "score": 総合スコア,
            }],
            "total_samples": 全サンプル数,
            "baseline": ベースライン統計,
        }
    """
    # Step 1: 全サンプルの指標+結果を収集
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

                try:
                    supply = calc_supply_score(window)
                except Exception:
                    continue

                path = analyze_price_path(df, idx, hold_days)
                if path is None:
                    continue

                indicators = evaluate_indicators(supply)
                record = {**indicators, **path}
                all_records.append(record)

        except Exception:
            continue

    if not all_records:
        return {"patterns": [], "total_samples": 0, "baseline": {}}

    df_all = pd.DataFrame(all_records)

    # ベースライン
    baseline = {
        "total": len(df_all),
        "clean_win_rate": round(df_all["is_clean_win"].mean() * 100, 1),
        "quick_win_rate": round(df_all["is_quick_win"].mean() * 100, 1),
        "avg_gain": round(df_all["peak_gain_pct"].mean(), 1),
        "avg_peak_day": round(df_all["peak_day"].mean(), 0),
        "avg_dd": round(df_all["max_dd_before_peak"].mean(), 1),
    }

    # Step 2: 指標の組み合わせを全探索
    indicator_keys = list(INDICATORS.keys())
    patterns = []

    for size in range(min_combo_size, max_combo_size + 1):
        for combo in combinations(indicator_keys, size):
            # この組み合わせが全てTrueのサンプルを抽出
            mask = pd.Series(True, index=df_all.index)
            for key in combo:
                if key in df_all.columns:
                    mask &= df_all[key] == True

            subset = df_all[mask]
            if len(subset) < min_samples:
                continue

            clean_rate = subset["is_clean_win"].mean() * 100
            quick_rate = subset["is_quick_win"].mean() * 100
            avg_gain = subset["peak_gain_pct"].mean()
            avg_day = subset["peak_day"].mean()
            avg_dd = subset["max_dd_before_peak"].mean()

            # ベースラインからのリフト
            clean_lift = clean_rate - baseline["clean_win_rate"]
            quick_lift = quick_rate - baseline["quick_win_rate"]

            # リフトがない組み合わせはスキップ
            if clean_lift <= 0 and quick_lift <= 0:
                continue

            # 総合スコア = リフト × サンプル数の対数（統計的信頼度）
            import math
            score = (clean_lift * 2 + quick_lift) * math.log(len(subset) + 1)

            patterns.append({
                "conditions": [INDICATORS[k]["label"] for k in combo],
                "condition_keys": list(combo),
                "clean_win_rate": round(clean_rate, 1),
                "quick_win_rate": round(quick_rate, 1),
                "clean_lift": round(clean_lift, 1),
                "quick_lift": round(quick_lift, 1),
                "avg_gain": round(avg_gain, 1),
                "avg_peak_day": round(avg_day, 0),
                "avg_dd": round(avg_dd, 1),
                "samples": len(subset),
                "score": round(score, 1),
            })

    # スコア順にソート
    patterns.sort(key=lambda x: x["score"], reverse=True)

    return {
        "patterns": patterns[:30],  # 上位30パターン
        "total_samples": len(df_all),
        "baseline": baseline,
    }


def format_pattern_report(result: dict) -> str:
    """パターン発見結果をレポートにする。"""
    lines = ["## 勝ちパターン発見結果\n"]
    baseline = result.get("baseline", {})
    lines.append(f"全{result['total_samples']}サンプル分析")
    lines.append(f"ベースライン: 安全勝ち{baseline.get('clean_win_rate', 0)}%, 即効勝ち{baseline.get('quick_win_rate', 0)}%, 平均+{baseline.get('avg_gain', 0)}%\n")

    for i, p in enumerate(result.get("patterns", [])[:10]):
        conditions = " + ".join(p["conditions"])
        lines.append(f"### パターン{i+1}: {conditions}")
        lines.append(f"- 安全勝ち率: **{p['clean_win_rate']}%** (ベース比+{p['clean_lift']}%)")
        lines.append(f"- 即効勝ち率: {p['quick_win_rate']}% (ベース比+{p['quick_lift']}%)")
        lines.append(f"- 平均利益: +{p['avg_gain']}%, 平均{p['avg_peak_day']:.0f}日, DD: -{p['avg_dd']}%")
        lines.append(f"- サンプル: {p['samples']}件, スコア: {p['score']}")
        lines.append("")

    return "\n".join(lines)
