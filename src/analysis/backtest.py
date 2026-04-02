"""バックテストモジュール

過去データから「どういう条件の銘柄が実際に上がったか」を検証する。
勝ちパターンの抽出と、時代によるバイアス補正を行う。

重要: アルゴリズム取引普及前（〜2015年頃）と後では市場の動きが異なる。
直近3-5年のデータを重視し、10年以上前のデータは参考値として扱う。
"""

import pandas as pd
import numpy as np
from datetime import datetime
from src.data.price import fetch_price
from src.analysis.supply import calc_supply_score
from src.analysis.manipulation.detector import detect_phase

# アルゴ普及の境界年（これ以降を「現代」として重み付け）
ALGO_ERA_START = 2018


def backtest_stock(code: str, period_days: int = 1095) -> list[dict]:
    """1銘柄の過去データを走査し、エントリーポイントと結果を記録する。

    過去の各時点で「今のスクリーニングロジック」を適用した場合、
    その後どうなったかを記録する。

    Returns:
        [{"date": エントリー日, "entry": 価格, "max_gain_30d": 30日後の最大上昇率, ...}]
    """
    df = fetch_price(code, period_days=period_days)
    if df.empty or len(df) < 120:
        return []

    results = []
    close = df["Close"]

    # 60日ごとにサンプリング（全日だと重すぎる）
    sample_indices = range(60, len(df) - 60, 20)

    for idx in sample_indices:
        window = df.iloc[:idx]
        future = df.iloc[idx:idx + 60]  # 60日後までの結果

        if len(window) < 60 or len(future) < 10:
            continue

        try:
            supply = calc_supply_score(window)
            phase = detect_phase(window)

            entry_price = float(close.iloc[idx])
            entry_date = close.index[idx]

            # 将来の結果
            future_close = future["Close"]
            future_high = future["High"]
            max_price_30d = float(future_high.head(30).max()) if len(future_high) >= 30 else float(future_high.max())
            max_price_60d = float(future_high.max())
            min_price = float(future["Low"].min())

            max_gain_30d = (max_price_30d - entry_price) / entry_price * 100
            max_gain_60d = (max_price_60d - entry_price) / entry_price * 100
            max_drawdown = (entry_price - min_price) / entry_price * 100

            # 時代重み（直近ほど重い）
            year = entry_date.year if hasattr(entry_date, "year") else datetime.now().year
            era_weight = 2.0 if year >= ALGO_ERA_START else 0.5

            results.append({
                "date": str(entry_date.date()) if hasattr(entry_date, "date") else str(entry_date),
                "year": year,
                "era_weight": era_weight,
                "entry_price": round(entry_price),
                "supply_score": supply.get("total", 0),
                "phase": phase.get("phase", "NONE"),
                "price_position": supply.get("price_position", 50),
                "is_bottom": supply.get("is_bottom", False),
                "squeeze": supply.get("squeeze", 0),
                "max_gain_30d": round(max_gain_30d, 1),
                "max_gain_60d": round(max_gain_60d, 1),
                "max_drawdown": round(max_drawdown, 1),
                "hit_2x": max_gain_60d >= 100,
                "hit_50pct": max_gain_60d >= 50,
            })

        except Exception:
            continue

    return results


def find_winning_patterns(backtest_results: list[dict]) -> dict:
    """バックテスト結果から勝ちパターンを抽出する。

    「どの条件が揃っている時に実際に上がったか」を統計的に検出。
    時代重み付きで集計する。

    Returns:
        {
            "patterns": 有効なパターンのリスト,
            "overall_stats": 全体統計,
            "era_comparison": 時代別の比較,
        }
    """
    if not backtest_results:
        return {"patterns": [], "overall_stats": {}, "era_comparison": {}}

    df = pd.DataFrame(backtest_results)

    # 全体統計
    overall = {
        "total_samples": len(df),
        "avg_gain_30d": round(float(df["max_gain_30d"].mean()), 1),
        "avg_gain_60d": round(float(df["max_gain_60d"].mean()), 1),
        "avg_drawdown": round(float(df["max_drawdown"].mean()), 1),
        "hit_2x_rate": round(float(df["hit_2x"].mean()) * 100, 1),
        "hit_50pct_rate": round(float(df["hit_50pct"].mean()) * 100, 1),
    }

    # 時代別比較
    modern = df[df["year"] >= ALGO_ERA_START]
    legacy = df[df["year"] < ALGO_ERA_START]
    era_comparison = {}

    if not modern.empty:
        era_comparison["modern"] = {
            "period": f"{ALGO_ERA_START}-present",
            "samples": len(modern),
            "avg_gain_60d": round(float(modern["max_gain_60d"].mean()), 1),
            "hit_2x_rate": round(float(modern["hit_2x"].mean()) * 100, 1),
            "avg_drawdown": round(float(modern["max_drawdown"].mean()), 1),
        }

    if not legacy.empty:
        era_comparison["legacy"] = {
            "period": f"before {ALGO_ERA_START}",
            "samples": len(legacy),
            "avg_gain_60d": round(float(legacy["max_gain_60d"].mean()), 1),
            "hit_2x_rate": round(float(legacy["hit_2x"].mean()) * 100, 1),
            "avg_drawdown": round(float(legacy["max_drawdown"].mean()), 1),
        }

    # 条件別の勝率（時代重み付き）
    patterns = []

    # 底値圏の効果
    bottom = df[df["is_bottom"] == True]
    not_bottom = df[df["is_bottom"] == False]
    if not bottom.empty and not not_bottom.empty:
        # 重み付き平均
        bottom_modern = bottom[bottom["year"] >= ALGO_ERA_START]
        bottom_gain = float(bottom_modern["max_gain_60d"].mean()) if not bottom_modern.empty else float(bottom["max_gain_60d"].mean())
        not_bottom_gain = float(not_bottom["max_gain_60d"].mean())
        if bottom_gain > not_bottom_gain * 1.2:
            patterns.append({
                "condition": "bottom_zone",
                "description": "底値圏でエントリー",
                "avg_gain": round(bottom_gain, 1),
                "baseline_gain": round(not_bottom_gain, 1),
                "advantage": round(bottom_gain - not_bottom_gain, 1),
                "sample_size": len(bottom_modern) if not bottom_modern.empty else len(bottom),
            })

    # ボラ収縮の効果
    high_squeeze = df[df["squeeze"] > 60]
    low_squeeze = df[df["squeeze"] <= 60]
    if not high_squeeze.empty and not low_squeeze.empty:
        hs_modern = high_squeeze[high_squeeze["year"] >= ALGO_ERA_START]
        hs_gain = float(hs_modern["max_gain_60d"].mean()) if not hs_modern.empty else float(high_squeeze["max_gain_60d"].mean())
        ls_gain = float(low_squeeze["max_gain_60d"].mean())
        if hs_gain > ls_gain * 1.2:
            patterns.append({
                "condition": "squeeze",
                "description": "ボラ収縮時にエントリー",
                "avg_gain": round(hs_gain, 1),
                "baseline_gain": round(ls_gain, 1),
                "advantage": round(hs_gain - ls_gain, 1),
                "sample_size": len(hs_modern) if not hs_modern.empty else len(high_squeeze),
            })

    # 仕手Phase別の効果
    for p in ["A", "B", "C"]:
        phase_df = df[df["phase"] == p]
        others = df[df["phase"] != p]
        if not phase_df.empty and len(phase_df) >= 3 and not others.empty:
            p_modern = phase_df[phase_df["year"] >= ALGO_ERA_START]
            p_gain = float(p_modern["max_gain_60d"].mean()) if not p_modern.empty else float(phase_df["max_gain_60d"].mean())
            o_gain = float(others["max_gain_60d"].mean())
            if p_gain > o_gain * 1.2:
                patterns.append({
                    "condition": f"phase_{p}",
                    "description": f"Phase {p}でエントリー",
                    "avg_gain": round(p_gain, 1),
                    "baseline_gain": round(o_gain, 1),
                    "advantage": round(p_gain - o_gain, 1),
                    "sample_size": len(p_modern) if not p_modern.empty else len(phase_df),
                })

    # 高需給スコアの効果
    high_supply = df[df["supply_score"] > 50]
    low_supply = df[df["supply_score"] <= 50]
    if not high_supply.empty and not low_supply.empty:
        hs_modern = high_supply[high_supply["year"] >= ALGO_ERA_START]
        hs_gain = float(hs_modern["max_gain_60d"].mean()) if not hs_modern.empty else float(high_supply["max_gain_60d"].mean())
        ls_gain = float(low_supply["max_gain_60d"].mean())
        if hs_gain > ls_gain * 1.2:
            patterns.append({
                "condition": "high_supply_score",
                "description": "需給スコア50超でエントリー",
                "avg_gain": round(hs_gain, 1),
                "baseline_gain": round(ls_gain, 1),
                "advantage": round(hs_gain - ls_gain, 1),
                "sample_size": len(hs_modern) if not hs_modern.empty else len(high_supply),
            })

    patterns.sort(key=lambda x: x["advantage"], reverse=True)

    return {
        "patterns": patterns,
        "overall_stats": overall,
        "era_comparison": era_comparison,
    }


def estimate_realistic_target(backtest_results: list[dict], percentile: int = 75) -> dict:
    """バックテスト結果から現実的な目標値を推定する。

    「過去に同じ条件で入った場合、○パーセンタイルでどこまで上がったか」

    Args:
        percentile: 何パーセンタイルを目標とするか（75 = 4回中3回は到達）

    Returns:
        {
            "realistic_gain_30d": 30日後の現実的な上昇率,
            "realistic_gain_60d": 60日後の現実的な上昇率,
            "worst_case_drawdown": ワーストケースの下落率,
        }
    """
    if not backtest_results:
        return {}

    # 直近データを重視
    modern = [r for r in backtest_results if r["year"] >= ALGO_ERA_START]
    data = modern if len(modern) >= 10 else backtest_results

    gains_30d = [r["max_gain_30d"] for r in data]
    gains_60d = [r["max_gain_60d"] for r in data]
    drawdowns = [r["max_drawdown"] for r in data]

    return {
        "realistic_gain_30d": round(float(np.percentile(gains_30d, percentile)), 1),
        "realistic_gain_60d": round(float(np.percentile(gains_60d, percentile)), 1),
        "median_gain_60d": round(float(np.median(gains_60d)), 1),
        "worst_case_drawdown": round(float(np.percentile(drawdowns, 90)), 1),
        "sample_size": len(data),
        "era": "modern" if len(modern) >= 10 else "mixed",
    }
