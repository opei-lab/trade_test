"""タイミング（発火タイミング）検出モジュール

「動きやすい状態」ではなく「直近で動く兆候がある」を検出する。
全て直近数日のデータの変化から判定。
"""

import pandas as pd
import numpy as np


def detect_volume_ignition(df: pd.DataFrame, lookback: int = 5, threshold: float = 2.0) -> dict:
    """出来高の急変（着火シグナル）を検出する。

    直近N日の出来高が20日平均の何倍かを見る。
    誰かが動き始めた = 株価も動く前兆。

    Returns:
        {
            "detected": bool,
            "recent_vol_ratio": 直近出来高 / 平均出来高,
            "ignition_day": 急増した日,
            "description": 説明,
        }
    """
    if len(df) < 25:
        return {"detected": False, "recent_vol_ratio": 0, "description": "データ不足"}

    volume = df["Volume"]
    avg_20 = volume.rolling(20).mean()

    recent = volume.tail(lookback)
    recent_avg = float(recent.mean())
    baseline = float(avg_20.iloc[-lookback - 1]) if len(avg_20) > lookback else float(avg_20.dropna().iloc[-1])

    ratio = recent_avg / baseline if baseline > 0 else 0

    # 直近で最も出来高が大きかった日
    max_vol_idx = recent.idxmax()
    max_vol_ratio = float(volume.loc[max_vol_idx] / baseline) if baseline > 0 else 0

    detected = ratio >= threshold or max_vol_ratio >= threshold * 1.5

    desc = ""
    if detected:
        if max_vol_ratio >= 5:
            desc = f"出来高爆発（平常の{max_vol_ratio:.1f}倍）、大きな動きの前兆"
        elif max_vol_ratio >= 3:
            desc = f"出来高急増（平常の{max_vol_ratio:.1f}倍）"
        else:
            desc = f"出来高増加傾向（直近平均{ratio:.1f}倍）"

    return {
        "detected": detected,
        "recent_vol_ratio": round(ratio, 2),
        "max_vol_ratio": round(max_vol_ratio, 2),
        "ignition_day": str(max_vol_idx.date()) if detected and hasattr(max_vol_idx, "date") else None,
        "description": desc,
    }


def detect_squeeze_extreme(df: pd.DataFrame, window: int = 20) -> dict:
    """ボリンジャーバンドの極限収縮を検出する。

    バンド幅が過去1年で最も狭い水準 = 数日〜数週間以内に大きく動く。
    （方向は不明だが、他の指標と組み合わせて判断）

    Returns:
        {
            "detected": bool,
            "band_width_percentile": バンド幅の過去パーセンタイル（低いほど収縮）,
            "description": 説明,
        }
    """
    if len(df) < window + 50:
        return {"detected": False, "band_width_percentile": 50, "description": "データ不足"}

    close = df["Close"]
    sma = close.rolling(window).mean()
    std = close.rolling(window).std()
    band_width = (4 * std) / sma

    current_bw = float(band_width.iloc[-1])
    percentile = float(band_width.rank(pct=True).iloc[-1]) * 100

    detected = percentile < 10  # 過去の下位10%以内

    desc = ""
    if detected:
        if percentile < 3:
            desc = "ボラ収縮が歴史的極限（数日以内に大きな動きの可能性大）"
        else:
            desc = f"ボラ収縮が過去{percentile:.0f}パーセンタイル（動き出し間近）"

    return {
        "detected": detected,
        "band_width_percentile": round(percentile, 1),
        "description": desc,
    }


def detect_support_bounce(df: pd.DataFrame, lookback: int = 10) -> dict:
    """長期サポートラインからの反発を検出する。

    直近でサポートに接触 → 反発し始めた = 動き出しのトリガー。

    Returns:
        {
            "detected": bool,
            "bounce_pct": サポートからの反発率,
            "support_price": サポート価格,
            "description": 説明,
        }
    """
    if len(df) < 60:
        return {"detected": False, "description": "データ不足"}

    close = df["Close"]
    low = df["Low"]

    # 60日サポートライン
    support_60 = float(low.rolling(60).min().iloc[-lookback])
    current = float(close.iloc[-1])
    recent_low = float(low.tail(lookback).min())

    # サポートに接触（5%以内に接近）してから反発
    touched = (recent_low - support_60) / support_60 < 0.05 if support_60 > 0 else False
    bounced = current > recent_low * 1.03  # 底値から3%以上反発

    detected = touched and bounced
    bounce_pct = (current - recent_low) / recent_low * 100 if recent_low > 0 else 0

    desc = ""
    if detected:
        desc = f"サポート¥{support_60:,.0f}から反発中（+{bounce_pct:.1f}%）"

    return {
        "detected": detected,
        "bounce_pct": round(bounce_pct, 1),
        "support_price": round(support_60),
        "description": desc,
    }


def detect_capitulation(df: pd.DataFrame, lookback: int = 10) -> dict:
    """投げ売り（セリングクライマックス）直後を検出する。

    急落 + 出来高爆発 → その後に出来高減少 = 売りが枯れた = 底打ち。

    Returns:
        {
            "detected": bool,
            "capitulation_date": 投げ売り日,
            "drop_pct": 急落率,
            "description": 説明,
        }
    """
    if len(df) < 30:
        return {"detected": False, "description": "データ不足"}

    close = df["Close"]
    volume = df["Volume"]
    daily_return = close.pct_change()
    avg_vol = volume.rolling(20).mean()

    recent = df.tail(lookback)

    # 直近N日で急落（-8%以上）+ 出来高3倍以上の日を探す
    for i in range(len(recent)):
        idx = recent.index[i]
        ret = float(daily_return.loc[idx]) if idx in daily_return.index else 0
        vol_ratio = float(volume.loc[idx] / avg_vol.loc[idx]) if idx in avg_vol.index and float(avg_vol.loc[idx]) > 0 else 0

        if ret < -0.08 and vol_ratio > 3:
            # 投げ売り日を検出
            # その後の出来高が減少 + 株価安定 = 底打ち
            after = close.loc[idx:]
            if len(after) >= 3:
                after_vol_trend = volume.loc[idx:].tail(5).mean() / float(volume.loc[idx])
                after_price_stable = float(after.iloc[-1]) >= float(after.iloc[1]) * 0.97

                if after_vol_trend < 0.5 and after_price_stable:
                    return {
                        "detected": True,
                        "capitulation_date": str(idx.date()) if hasattr(idx, "date") else str(idx),
                        "drop_pct": round(ret * 100, 1),
                        "description": f"投げ売り({ret*100:.0f}%, 出来高{vol_ratio:.0f}倍)後に安定 → 底打ちの可能性",
                    }

    return {"detected": False, "description": ""}


def calc_timing_score(df: pd.DataFrame) -> dict:
    """直近で動く可能性を総合判定する。

    Returns:
        {
            "timing_score": 0-100（高いほど直近で動く可能性大）,
            "signals": 検出されたシグナルのリスト,
            "urgency": "immediate" | "soon" | "watching",
            "description": 総合判定の説明,
        }
    """
    ignition = detect_volume_ignition(df)
    squeeze = detect_squeeze_extreme(df)
    bounce = detect_support_bounce(df)
    capitulation = detect_capitulation(df)

    score = 0
    signals = []

    if ignition["detected"]:
        score += 30
        signals.append(ignition["description"])

    if squeeze["detected"]:
        score += 25
        signals.append(squeeze["description"])

    if bounce["detected"]:
        score += 25
        signals.append(bounce["description"])

    if capitulation["detected"]:
        score += 30
        signals.append(capitulation["description"])

    score = min(100, score)

    if score >= 60:
        urgency = "immediate"
    elif score >= 30:
        urgency = "soon"
    else:
        urgency = "watching"

    urgency_labels = {
        "immediate": "今すぐ注目（複数の発火シグナル）",
        "soon": "近日中に動く可能性",
        "watching": "監視継続",
    }

    return {
        "timing_score": score,
        "signals": signals,
        "urgency": urgency,
        "description": urgency_labels[urgency],
        "details": {
            "volume_ignition": ignition,
            "squeeze_extreme": squeeze,
            "support_bounce": bounce,
            "capitulation": capitulation,
        },
    }
