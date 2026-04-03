"""バックテスト検証モジュール

Stage 1シグナルの組み合わせを過去データで検証し、
勝率70-80%を担保するゴールデンルールを発見する。

勝ちの定義: 60日以内にMFE（期中最高値）が+15%以上
対象: グロース市場・低価格・高ボラ銘柄
"""

import json
import logging
import pickle
from datetime import date
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis.supply import calc_supply_score
from src.analysis.manipulation.detector import detect_phase
from src.analysis.timing import calc_timing_score
from src.analysis.resistance import calc_ceiling_score, detect_volume_vacuum
from src.analysis.safety import calc_downside_floor, calc_asymmetry_score
from src.data.price import fetch_price

_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_RULES_FILE = _DATA_DIR / "validated_rules.json"
_CACHE_FILE = _DATA_DIR / "validation_backtest.pkl"

WIN_THRESHOLD = 15.0  # MFE +15%以上が勝ち
HOLD_DAYS = 60
SAMPLE_INTERVAL = 10  # 10営業日ごとにサンプリング
MIN_SAMPLES = 30  # ルール成立の最低サンプル数
MIN_WIN_RATE = 0.70  # ゴールデンルールの最低勝率
MIN_CONFIDENCE_LOWER = 0.60  # 95%信頼区間の下限


def capture_signals(df: pd.DataFrame, idx: int) -> dict:
    """指定時点での全Stage 1シグナルを計算する。dfのみ使用。"""
    window = df.iloc[:idx + 1]
    if len(window) < 60:
        return None

    try:
        supply = calc_supply_score(window)
        phase = detect_phase(window)
        timing = calc_timing_score(window)
        ceiling = calc_ceiling_score(window)
        vacuum = detect_volume_vacuum(window)
        floor = calc_downside_floor(window, {})

        current = float(window["Close"].iloc[-1])

        # 非対称性（上値はfind_price_targetsから推定）
        hist_high = float(window["Close"].max())
        upside = (hist_high - current) / current * 100 if current > 0 else 0
        downside = floor.get("max_downside_pct", 30)
        asymmetry = calc_asymmetry_score(max(upside, 10), max(downside, 5))

        # 出来高トレンド
        volume_trend = 1.0
        if len(window) >= 40:
            vol_recent = float(window["Volume"].tail(20).mean())
            vol_prev = float(window["Volume"].iloc[-40:-20].mean())
            volume_trend = vol_recent / vol_prev if vol_prev > 0 else 1.0

        # 安値切り上げ
        higher_lows = False
        if len(window) >= 60:
            lows = window["Low"].tail(60)
            q1 = float(lows.iloc[:20].min())
            q2 = float(lows.iloc[20:40].min())
            q3 = float(lows.iloc[40:].min())
            higher_lows = q1 <= q2 <= q3

        # ヒストリカルレンジ
        recent = window.tail(min(120, len(window)))
        hist_range = float(recent["Close"].max()) / max(float(recent["Close"].min()), 1)

        return {
            "price_position": supply.get("price_position", 50),
            "divergence": supply.get("divergence", 0),
            "squeeze": supply.get("squeeze", 0),
            "accumulation": supply.get("accumulation", 0),
            "volume_anomaly": supply.get("volume_anomaly", 1),
            "is_bottom": supply.get("is_bottom", False),
            "supply_score": supply.get("total", 0),
            "phase": phase.get("phase", "NONE"),
            "phase_confidence": phase.get("confidence", 0),
            "timing_score": timing.get("timing_score", 0),
            "urgency": timing.get("urgency", "watching"),
            "vol_ignition": timing.get("details", {}).get("volume_ignition", {}).get("detected", False),
            "squeeze_extreme": timing.get("details", {}).get("squeeze_extreme", {}).get("detected", False),
            "support_bounce": timing.get("details", {}).get("support_bounce", {}).get("detected", False),
            "capitulation": timing.get("details", {}).get("capitulation", {}).get("detected", False),
            "ceiling_score": ceiling.get("ceiling_score", 50),
            "overhead_pct": ceiling.get("overhead_supply", {}).get("total_overhead_pct", 50),
            "has_vacuum": vacuum.get("has_vacuum", False),
            "vacuum_width_pct": vacuum.get("vacuum_width_pct", 0),
            "max_downside_pct": floor.get("max_downside_pct", 30),
            "floor_confidence": floor.get("floor_confidence", 0),
            "asymmetry": asymmetry,
            "volume_trend": round(volume_trend, 2),
            "higher_lows": higher_lows,
            "historical_range": round(hist_range, 2),
            "current_price": current,
        }
    except Exception:
        return None


def measure_outcome(df: pd.DataFrame, entry_idx: int) -> dict:
    """エントリー後60日間のアウトカムを計測する。"""
    entry_price = float(df["Close"].iloc[entry_idx])
    forward = df.iloc[entry_idx + 1: entry_idx + 1 + HOLD_DAYS]

    if len(forward) < 5:
        return None

    highs = forward["High"].values
    lows = forward["Low"].values

    mfe_prices = (highs - entry_price) / entry_price * 100
    mae_prices = (entry_price - lows) / entry_price * 100

    mfe = float(np.max(mfe_prices))
    mae = float(np.max(mae_prices))
    peak_day = int(np.argmax(mfe_prices)) + 1
    trough_day = int(np.argmax(mae_prices)) + 1

    # 段階MFE
    mfe_10d = float(np.max(mfe_prices[:min(10, len(mfe_prices))])) if len(mfe_prices) >= 5 else 0
    mfe_20d = float(np.max(mfe_prices[:min(20, len(mfe_prices))])) if len(mfe_prices) >= 10 else 0
    mfe_30d = float(np.max(mfe_prices[:min(30, len(mfe_prices))])) if len(mfe_prices) >= 15 else 0

    return {
        "mfe": round(mfe, 1),
        "mae": round(mae, 1),
        "peak_day": peak_day,
        "trough_day": trough_day,
        "is_win": mfe >= WIN_THRESHOLD,
        "mfe_10d": round(mfe_10d, 1),
        "mfe_20d": round(mfe_20d, 1),
        "mfe_30d": round(mfe_30d, 1),
    }


def backtest_single_stock(code: str, period_days: int = 1095) -> list[dict]:
    """1銘柄のバックテスト。全サンプルポイントでシグナル+アウトカムを記録。"""
    try:
        df = fetch_price(code, period_days=period_days)
        if df is None or df.empty or len(df) < 120:
            return []
    except Exception:
        return []

    results = []
    # 60日前からサンプリング開始（60日分の先読みデータが必要）
    start = max(60, SAMPLE_INTERVAL)
    end = len(df) - HOLD_DAYS - 1

    for idx in range(start, end, SAMPLE_INTERVAL):
        signals = capture_signals(df, idx)
        if signals is None:
            continue

        outcome = measure_outcome(df, idx)
        if outcome is None:
            continue

        entry_date = df.index[idx]
        year = entry_date.year if hasattr(entry_date, "year") else 2020
        era_weight = 4.0 if year >= 2018 else 1.0

        results.append({
            "code": code,
            "date": str(entry_date.date()) if hasattr(entry_date, "date") else str(entry_date),
            "year": year,
            "era_weight": era_weight,
            **signals,
            **outcome,
        })

    return results


def run_validation_backtest(codes: list[str], period_days: int = 1095,
                            progress_callback=None) -> pd.DataFrame:
    """全銘柄のバックテストを実行し、DataFrameで返す。"""
    all_results = []
    total = len(codes)

    for i, code in enumerate(codes):
        if progress_callback and i % 10 == 0:
            progress_callback(i, total, code)

        stock_results = backtest_single_stock(code, period_days)
        all_results.extend(stock_results)

    if not all_results:
        return pd.DataFrame()

    bt_df = pd.DataFrame(all_results)

    # キャッシュ保存
    _DATA_DIR.mkdir(exist_ok=True)
    bt_df.to_pickle(str(_CACHE_FILE))
    logging.info(f"Backtest: {len(bt_df)} samples from {total} stocks saved")

    return bt_df


def load_cached_backtest() -> pd.DataFrame:
    """キャッシュ済みバックテスト結果を読み込む。"""
    if _CACHE_FILE.exists():
        try:
            return pd.read_pickle(str(_CACHE_FILE))
        except Exception:
            pass
    return pd.DataFrame()


# === シグナル検証 ===

# 連続値シグナル（閾値で2分割してliftを計算）
CONTINUOUS_SIGNALS = [
    {"name": "price_position", "direction": "below", "desc": "底値位置"},
    {"name": "divergence", "direction": "above", "desc": "売り枯れ"},
    {"name": "squeeze", "direction": "above", "desc": "ボラ収縮"},
    {"name": "accumulation", "direction": "above", "desc": "集積"},
    {"name": "volume_anomaly", "direction": "above", "desc": "出来高異常"},
    {"name": "supply_score", "direction": "above", "desc": "需給スコア"},
    {"name": "timing_score", "direction": "above", "desc": "タイミング"},
    {"name": "ceiling_score", "direction": "below", "desc": "上値軽さ"},
    {"name": "vacuum_width_pct", "direction": "above", "desc": "真空幅"},
    {"name": "max_downside_pct", "direction": "below", "desc": "下値限定"},
    {"name": "asymmetry", "direction": "above", "desc": "非対称性"},
    {"name": "volume_trend", "direction": "above", "desc": "出来高トレンド"},
    {"name": "historical_range", "direction": "above", "desc": "ヒストリカルレンジ"},
    {"name": "floor_confidence", "direction": "above", "desc": "下値信頼度"},
]

# ブール値シグナル
BOOLEAN_SIGNALS = [
    {"name": "is_bottom", "desc": "底値圏"},
    {"name": "higher_lows", "desc": "安値切り上げ"},
    {"name": "has_vacuum", "desc": "真空地帯あり"},
    {"name": "vol_ignition", "desc": "出来高点火"},
    {"name": "squeeze_extreme", "desc": "収縮極限"},
    {"name": "support_bounce", "desc": "サポート反発"},
    {"name": "capitulation", "desc": "投げ売り検出"},
]

# フェーズシグナル
PHASE_VALUES = ["A", "B", "C", "D", "NONE"]


def _weighted_win_rate(group: pd.DataFrame) -> float:
    """era_weight付き勝率を計算。"""
    if group.empty:
        return 0
    weights = group["era_weight"].values
    wins = group["is_win"].astype(float).values
    return float(np.average(wins, weights=weights))


def _wilson_lower(wins: int, total: int, z: float = 1.96) -> float:
    """Wilson score intervalの下限（95%信頼区間）。"""
    if total == 0:
        return 0
    p = wins / total
    denom = 1 + z ** 2 / total
    center = p + z ** 2 / (2 * total)
    spread = z * np.sqrt((p * (1 - p) + z ** 2 / (4 * total)) / total)
    return (center - spread) / denom


def find_optimal_threshold(bt_df: pd.DataFrame, signal: str, direction: str) -> dict:
    """1シグナルの最適閾値を探索する。"""
    values = bt_df[signal].dropna()
    if len(values) < MIN_SAMPLES * 2:
        return {"signal": signal, "valid": False}

    baseline = _weighted_win_rate(bt_df)
    best = {"threshold": 0, "lift": -999, "win_rate": 0, "n": 0}

    # 10パーセンタイルごとにスキャン
    for pct in range(10, 91, 5):
        threshold = float(np.percentile(values, pct))

        if direction == "below":
            mask = bt_df[signal] <= threshold
        else:
            mask = bt_df[signal] >= threshold

        group = bt_df[mask]
        if len(group) < MIN_SAMPLES:
            continue

        wr = _weighted_win_rate(group)
        lift = wr - baseline

        if lift > best["lift"]:
            best = {
                "threshold": round(threshold, 1),
                "lift": round(lift * 100, 1),
                "win_rate": round(wr * 100, 1),
                "n": len(group),
            }

    return {
        "signal": signal,
        "direction": direction,
        "valid": best["lift"] > 0,
        **best,
    }


def calc_signal_rankings(bt_df: pd.DataFrame) -> list[dict]:
    """全シグナルのliftを計算してランキングする。"""
    rankings = []

    for sig in CONTINUOUS_SIGNALS:
        if sig["name"] not in bt_df.columns:
            continue
        result = find_optimal_threshold(bt_df, sig["name"], sig["direction"])
        result["desc"] = sig["desc"]
        rankings.append(result)

    # ブール値シグナル
    baseline = _weighted_win_rate(bt_df)
    for sig in BOOLEAN_SIGNALS:
        if sig["name"] not in bt_df.columns:
            continue
        true_group = bt_df[bt_df[sig["name"]] == True]
        if len(true_group) < MIN_SAMPLES:
            continue
        wr = _weighted_win_rate(true_group)
        rankings.append({
            "signal": sig["name"],
            "direction": "equals_true",
            "desc": sig["desc"],
            "valid": wr > baseline,
            "threshold": True,
            "lift": round((wr - baseline) * 100, 1),
            "win_rate": round(wr * 100, 1),
            "n": len(true_group),
        })

    # フェーズ
    for phase_val in PHASE_VALUES:
        group = bt_df[bt_df["phase"] == phase_val]
        if len(group) < MIN_SAMPLES:
            continue
        wr = _weighted_win_rate(group)
        rankings.append({
            "signal": f"phase_{phase_val}",
            "direction": "equals",
            "desc": f"フェーズ{phase_val}",
            "valid": wr > baseline,
            "threshold": phase_val,
            "lift": round((wr - baseline) * 100, 1),
            "win_rate": round(wr * 100, 1),
            "n": len(group),
        })

    rankings.sort(key=lambda x: x.get("lift", -999), reverse=True)
    return rankings


# === ゴールデンルール探索 ===

def _make_condition(sig_info: dict) -> dict:
    """シグナル情報から条件dictを作る。"""
    if sig_info["direction"] == "below":
        return {"signal": sig_info["signal"], "op": "<=", "value": sig_info["threshold"]}
    elif sig_info["direction"] == "above":
        return {"signal": sig_info["signal"], "op": ">=", "value": sig_info["threshold"]}
    elif sig_info["direction"] == "equals_true":
        return {"signal": sig_info["signal"], "op": "==", "value": True}
    elif sig_info["direction"] == "equals":
        return {"signal": sig_info["signal"], "op": "==", "value": sig_info["threshold"]}
    return None


def _apply_condition(bt_df: pd.DataFrame, cond: dict) -> pd.Series:
    """条件をDataFrameに適用してマスクを返す。"""
    sig = cond["signal"]
    op = cond["op"]
    val = cond["value"]

    if sig not in bt_df.columns:
        # フェーズ系（phase_A等）
        if sig.startswith("phase_"):
            phase_val = sig.split("_", 1)[1]
            return bt_df["phase"] == phase_val
        return pd.Series(True, index=bt_df.index)

    if op == "<=":
        return bt_df[sig] <= val
    elif op == ">=":
        return bt_df[sig] >= val
    elif op == "==":
        return bt_df[sig] == val
    elif op == "!=":
        return bt_df[sig] != val
    return pd.Series(True, index=bt_df.index)


def find_golden_rules(bt_df: pd.DataFrame) -> dict:
    """勝率70%以上のシグナル組み合わせを探索する。"""
    if bt_df.empty:
        return {"golden_rules": [], "signal_rankings": [], "baseline_win_rate": 0}

    baseline = _weighted_win_rate(bt_df)
    rankings = calc_signal_rankings(bt_df)
    valid_sigs = [r for r in rankings if r.get("valid") and r.get("lift", 0) > 0]

    # lift上位8シグナルで組み合わせ探索
    top_signals = valid_sigs[:8]
    conditions_list = []
    for sig in top_signals:
        cond = _make_condition(sig)
        if cond:
            conditions_list.append((sig, cond))

    # Walk-forward: 時系列で70%/30%に分割
    dates = pd.to_datetime(bt_df["date"])
    split_date = dates.quantile(0.7)
    train = bt_df[dates <= split_date]
    test = bt_df[dates > split_date]

    golden_rules = []

    # 2-4シグナルの全組み合わせをテスト
    for n_combo in range(2, min(5, len(conditions_list) + 1)):
        for combo in combinations(range(len(conditions_list)), n_combo):
            conds = [conditions_list[i][1] for i in combo]
            sigs = [conditions_list[i][0] for i in combo]

            # 訓練データで評価
            train_mask = pd.Series(True, index=train.index)
            for c in conds:
                train_mask &= _apply_condition(train, c)
            train_group = train[train_mask]

            if len(train_group) < MIN_SAMPLES:
                continue

            train_wr = _weighted_win_rate(train_group)
            if train_wr < MIN_WIN_RATE:
                continue

            # テストデータで検証
            test_mask = pd.Series(True, index=test.index)
            for c in conds:
                test_mask &= _apply_condition(test, c)
            test_group = test[test_mask]

            if len(test_group) < 10:
                continue

            test_wr = _weighted_win_rate(test_group)
            if test_wr < 0.65:  # テストでも65%以上
                continue

            # 信頼区間
            total_n = len(train_group) + len(test_group)
            total_wins = int(train_group["is_win"].sum() + test_group["is_win"].sum())
            conf_lower = _wilson_lower(total_wins, total_n)

            if conf_lower < MIN_CONFIDENCE_LOWER:
                continue

            # MFE/MAE統計
            all_group = pd.concat([train_group, test_group])
            avg_mfe = float(all_group["mfe"].mean())
            avg_mae = float(all_group["mae"].mean())
            avg_peak_day = float(all_group["peak_day"].mean())

            golden_rules.append({
                "conditions": conds,
                "signal_names": [s["desc"] for s in sigs],
                "train_win_rate": round(train_wr * 100, 1),
                "test_win_rate": round(test_wr * 100, 1),
                "confidence_lower": round(conf_lower * 100, 1),
                "avg_mfe": round(avg_mfe, 1),
                "avg_mae": round(avg_mae, 1),
                "avg_peak_day": round(avg_peak_day, 1),
                "n_train": len(train_group),
                "n_test": len(test_group),
                "n_total": total_n,
            })

    # スコア順にソート: 勝率 × sqrt(サンプル数)
    golden_rules.sort(
        key=lambda r: r["test_win_rate"] * np.sqrt(r["n_total"]),
        reverse=True,
    )

    result = {
        "version": 1,
        "generated_at": date.today().isoformat(),
        "win_definition": {"mfe_pct": WIN_THRESHOLD, "hold_days": HOLD_DAYS},
        "total_samples": len(bt_df),
        "baseline_win_rate": round(baseline * 100, 1),
        "signal_rankings": rankings[:15],
        "golden_rules": golden_rules[:10],  # 上位10ルール
        "hard_filters": [
            {"signal": "phase", "op": "!=", "value": "E"},
            {"signal": "ceiling_score", "op": "<", "value": 70},
            {"signal": "volume_trend", "op": ">=", "value": 0.5},
            {"signal": "max_downside_pct", "op": "<=", "value": 50},
        ],
    }

    # 保存
    _DATA_DIR.mkdir(exist_ok=True)
    with open(_RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    logging.info(
        f"Golden rules: {len(golden_rules)} found, "
        f"baseline={baseline*100:.1f}%, "
        f"best={golden_rules[0]['test_win_rate']:.1f}% (n={golden_rules[0]['n_total']})"
        if golden_rules else "no golden rules found"
    )

    return result


def load_validated_rules() -> dict:
    """検証済みルールを読み込む。なければ空dictを返す。"""
    if _RULES_FILE.exists():
        try:
            with open(_RULES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def check_stock_against_rules(signals: dict, rules: dict) -> dict:
    """銘柄のシグナルをゴールデンルールと照合する。

    Returns:
        {
            "passes_hard_filter": bool,
            "matched_rules": [マッチしたルール],
            "best_win_rate": 最高勝率,
            "best_avg_mfe": 最高平均MFE,
        }
    """
    if not rules or not rules.get("golden_rules"):
        return {"passes_hard_filter": True, "matched_rules": [], "best_win_rate": 0, "best_avg_mfe": 0}

    # ハードフィルタ
    for hf in rules.get("hard_filters", []):
        sig = hf["signal"]
        op = hf["op"]
        val = hf["value"]
        actual = signals.get(sig)
        if actual is None:
            continue
        if op == "!=" and actual == val:
            return {"passes_hard_filter": False, "matched_rules": [], "best_win_rate": 0, "best_avg_mfe": 0}
        if op == "<" and actual >= val:
            return {"passes_hard_filter": False, "matched_rules": [], "best_win_rate": 0, "best_avg_mfe": 0}
        if op == ">=" and actual < val:
            return {"passes_hard_filter": False, "matched_rules": [], "best_win_rate": 0, "best_avg_mfe": 0}
        if op == "<=" and actual > val:
            return {"passes_hard_filter": False, "matched_rules": [], "best_win_rate": 0, "best_avg_mfe": 0}

    # ゴールデンルール照合
    matched = []
    for rule in rules.get("golden_rules", []):
        all_pass = True
        for cond in rule.get("conditions", []):
            sig = cond["signal"]
            op = cond["op"]
            val = cond["value"]
            actual = signals.get(sig)

            # phase_X系
            if sig.startswith("phase_"):
                phase_val = sig.split("_", 1)[1]
                actual = signals.get("phase")
                if actual != phase_val:
                    all_pass = False
                    break
                continue

            if actual is None:
                all_pass = False
                break
            if op == "<=" and actual > val:
                all_pass = False
                break
            if op == ">=" and actual < val:
                all_pass = False
                break
            if op == "==" and actual != val:
                all_pass = False
                break

        if all_pass:
            matched.append(rule)

    best_wr = max((r["test_win_rate"] for r in matched), default=0)
    best_mfe = max((r["avg_mfe"] for r in matched), default=0)

    return {
        "passes_hard_filter": True,
        "matched_rules": matched,
        "best_win_rate": best_wr,
        "best_avg_mfe": best_mfe,
    }
