"""仕手パターン検出モジュール

底値横ばい → 試し玉 → 振るい落とし → 本上昇 → 売り抜け
のパターンをフェーズ判定する。
"""

import pandas as pd
import numpy as np


def detect_sideways(df: pd.DataFrame, window: int = 30, threshold: float = 0.05) -> bool:
    """底値圏での横ばいを検出する。

    Args:
        window: 判定期間（日数）
        threshold: 値幅がこの比率以下なら横ばいと判定

    Returns:
        横ばい判定
    """
    if len(df) < window:
        return False

    recent = df.tail(window)
    price_range = (recent["High"].max() - recent["Low"].min()) / recent["Close"].mean()
    return price_range < threshold


def detect_volume_creep(df: pd.DataFrame, window: int = 20) -> float:
    """出来高の漸増を検出する（仕手Phase A: 静かな買い集め）。

    直近N日間の出来高が前N日に比べてどれだけ増加しているか。
    Returns:
        増加率（1.0 = 変化なし、1.5 = 50%増加）
    """
    if len(df) < window * 2:
        return 1.0

    recent_vol = df["Volume"].tail(window).mean()
    prev_vol = df["Volume"].iloc[-window * 2 : -window].mean()

    if prev_vol == 0:
        return 1.0

    return recent_vol / prev_vol


def detect_spike_and_drop(
    df: pd.DataFrame, spike_threshold: float = 0.10, lookback: int = 30
) -> dict:
    """試し玉 → 振るい落としパターンを検出する。

    直近N日間で急騰後に急落したパターンを探す。

    Returns:
        {
            "detected": bool,
            "spike_date": 急騰日,
            "spike_pct": 急騰率,
            "drop_date": 急落日,
            "drop_pct": 急落率,
            "current_vs_pre_spike": 現在値 / 急騰前の値,
        }
    """
    result = {"detected": False}

    if len(df) < lookback:
        return result

    recent = df.tail(lookback).copy()
    daily_return = recent["Close"].pct_change()

    # 急騰日を探す（spike_threshold以上の日次リターン）
    spikes = daily_return[daily_return > spike_threshold]
    if spikes.empty:
        return result

    # 最新の急騰日を取得
    spike_date = spikes.index[-1]
    spike_pct = float(spikes.iloc[-1])

    # 急騰後のデータ
    after_spike = recent.loc[spike_date:]
    if len(after_spike) < 2:
        return result

    # 急騰後の最安値を探す
    post_spike_returns = after_spike["Close"].pct_change().cumsum()
    min_return_date = post_spike_returns.idxmin()
    drop_pct = float(post_spike_returns.min())

    # 現在値と急騰前の比較
    pre_spike_close = recent["Close"].loc[:spike_date].iloc[-2] if len(recent.loc[:spike_date]) >= 2 else recent["Close"].iloc[0]
    current_close = recent["Close"].iloc[-1]
    current_vs_pre = float(current_close / pre_spike_close) if pre_spike_close > 0 else 1.0

    result = {
        "detected": True,
        "spike_date": spike_date,
        "spike_pct": round(spike_pct * 100, 1),
        "drop_date": min_return_date,
        "drop_pct": round(drop_pct * 100, 1),
        "current_vs_pre_spike": round(current_vs_pre, 3),
    }

    return result


def detect_shakeout(df: pd.DataFrame, window: int = 60) -> dict:
    """振るい落としパターンを検出する。

    底値横ばい中に一度下げてから戻るパターン。
    「底値を割ったように見せかけて弱い持ち株を手放させる」動き。

    Returns:
        {
            "detected": bool,
            "dip_depth": 下げ幅（%）,
            "recovery_pct": 回復率（%）,
            "volume_at_dip": 下げ時の出来高倍率,
        }
    """
    result = {"detected": False}

    if len(df) < window:
        return result

    recent = df.tail(window)

    # 前半の底値水準を算出
    first_half = recent.iloc[: window // 2]
    baseline_low = first_half["Low"].min()
    baseline_avg = first_half["Close"].mean()

    # 後半でbaseline_lowを下回った日を探す
    second_half = recent.iloc[window // 2 :]
    dips_below = second_half[second_half["Low"] < baseline_low]

    if dips_below.empty:
        return result

    dip_low = dips_below["Low"].min()
    dip_date = dips_below["Low"].idxmin()
    dip_depth = (baseline_low - dip_low) / baseline_avg * 100

    # ディップ後の回復
    after_dip = second_half.loc[dip_date:]
    if len(after_dip) < 2:
        return result

    recovery_high = after_dip["Close"].max()
    recovery_pct = (recovery_high - dip_low) / dip_low * 100

    # ディップ時の出来高
    avg_vol = first_half["Volume"].mean()
    dip_volume_ratio = float(dips_below["Volume"].mean() / avg_vol) if avg_vol > 0 else 1.0

    result = {
        "detected": recovery_pct > dip_depth,  # 回復が下げ幅を超えていれば振るい落とし成功
        "dip_depth": round(dip_depth, 1),
        "recovery_pct": round(recovery_pct, 1),
        "volume_at_dip": round(dip_volume_ratio, 2),
        "dip_date": dip_date,
    }

    return result


def detect_phase(df: pd.DataFrame) -> dict:
    """仕手パターンの現在フェーズを判定する。

    Returns:
        {
            "phase": "A" | "B" | "C" | "D" | "E" | "NONE",
            "confidence": 0-100,
            "description": フェーズの説明,
            "details": 各検出結果,
        }
    """
    if len(df) < 60:
        return {"phase": "NONE", "confidence": 0, "description": "データ不足"}

    is_sideways = detect_sideways(df, window=30)
    vol_creep = detect_volume_creep(df, window=20)
    spike_drop = detect_spike_and_drop(df, lookback=30)
    shakeout = detect_shakeout(df, window=60)

    details = {
        "sideways": is_sideways,
        "volume_creep": round(vol_creep, 2),
        "spike_and_drop": spike_drop,
        "shakeout": shakeout,
    }

    # Phase判定ロジック
    # Phase E: 急騰後に大幅下落中（売り抜け後）
    if spike_drop["detected"] and spike_drop.get("drop_pct", 0) < -20:
        return {
            "phase": "E",
            "confidence": 70,
            "description": "売り抜け後の下落局面。手出し禁止。",
            "details": details,
        }

    # Phase D: 急騰中（出来高爆発）
    latest_vol_ratio = float(df["Volume"].iloc[-1] / df["Volume"].rolling(20).mean().iloc[-1]) if df["Volume"].rolling(20).mean().iloc[-1] > 0 else 1
    recent_return = float((df["Close"].iloc[-1] / df["Close"].iloc[-5] - 1) * 100) if len(df) >= 5 else 0

    if latest_vol_ratio > 5 and recent_return > 15:
        return {
            "phase": "D",
            "confidence": 75,
            "description": "本上昇中。出来高爆発。利確タイミングに注意。",
            "details": details,
        }

    # Phase C: 振るい落とし検出
    if shakeout["detected"]:
        return {
            "phase": "C",
            "confidence": 65,
            "description": "振るい落とし検出。回復すればエントリーチャンス。",
            "details": details,
        }

    # Phase B: 試し玉（急騰後に戻った）
    if spike_drop["detected"] and spike_drop.get("current_vs_pre_spike", 1) < 1.05:
        return {
            "phase": "B",
            "confidence": 60,
            "description": "試し玉の可能性。急騰後に元の水準に戻った。次の動きに注目。",
            "details": details,
        }

    # Phase A: 静かな買い集め
    if is_sideways and vol_creep > 1.2:
        return {
            "phase": "A",
            "confidence": 50,
            "description": "底値横ばい中に出来高漸増。買い集めの可能性。",
            "details": details,
        }

    # 横ばいだが出来高変化なし
    if is_sideways:
        return {
            "phase": "NONE",
            "confidence": 30,
            "description": "底値横ばいだが特異な動きなし。監視継続。",
            "details": details,
        }

    return {
        "phase": "NONE",
        "confidence": 0,
        "description": "仕手パターン非該当。",
        "details": details,
    }
