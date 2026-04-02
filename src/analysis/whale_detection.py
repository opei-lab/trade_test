"""大口・機関投資家の動き検出モジュール

日次データから「誰が動かしているか」を推定する。
分足データなしでも、日足の特徴から大口の行動パターンを推定できる。

検出手法:
1. 出来高急増日のローソク足の形 → 大口か個人かを推定
2. 引け際に寄せる動き → 大口の特徴
3. 出来高と株価の関係パターン → 買い集めか売り抜けか
4. 価格帯別の不自然な出来高集中 → アルゴの閾値
"""

import pandas as pd
import numpy as np


def detect_institutional_buying(df: pd.DataFrame, window: int = 20) -> dict:
    """機関投資家（大口）の買いサインを検出する。

    大口の特徴:
    - 出来高が増えるが株価が大きく動かない（分割して静かに買う）
    - 陽線の割合が高いが1日の値幅は小さい（じわじわ買い上げる）
    - 終値が日中レンジの上位に来る（引け際に買いを入れる）

    Returns:
        {
            "detected": bool,
            "confidence": 0-100,
            "signals": [検出されたシグナル],
            "buying_pressure": 買い圧力スコア,
            "description": 説明,
        }
    """
    if len(df) < window + 10:
        return {"detected": False, "confidence": 0, "description": "データ不足"}

    recent = df.tail(window)
    close = recent["Close"]
    open_ = recent["Open"]
    high = recent["High"]
    low = recent["Low"]
    volume = recent["Volume"]

    signals = []
    score = 0

    # 1. Close Location Value（終値が日中レンジのどこにあるか）
    # +1に近い = 高値引け（大口が引け際に買い上げた）
    range_ = high - low
    clv = ((close - low) - (high - close)) / (range_ + 1e-10)
    avg_clv = float(clv.mean())

    if avg_clv > 0.3:
        signals.append(f"終値が日中レンジの上位に集中（CLV={avg_clv:.2f}）→ 引け際の買い=大口の特徴")
        score += 30
    elif avg_clv > 0.1:
        signals.append(f"終値がやや上位寄り（CLV={avg_clv:.2f}）")
        score += 10

    # 2. 出来高増加 + 値幅小 = 大口の分割買い
    vol_baseline = float(df["Volume"].iloc[:-window].tail(window).mean()) if len(df) > window * 2 else float(volume.mean())
    vol_ratio = float(volume.mean()) / vol_baseline if vol_baseline > 0 else 1
    avg_range_pct = float((range_ / close).mean()) * 100

    if vol_ratio > 1.2 and avg_range_pct < 3:
        signals.append(f"出来高{vol_ratio:.1f}倍に増加するも値幅{avg_range_pct:.1f}%と小さい → 大口が静かに買い集めている可能性")
        score += 25

    # 3. 陽線の割合
    bullish = (close > open_).sum()
    bullish_pct = bullish / len(recent) * 100
    if bullish_pct >= 65:
        signals.append(f"陽線比率{bullish_pct:.0f}%（{window}日中{bullish}日が上昇）→ 継続的な買い圧力")
        score += 20

    # 4. On-Balance Volume（OBV）の傾向
    # 株価が横ばいなのにOBVが上昇 = 水面下で買い集め
    obv = (np.sign(close.diff()) * volume).cumsum()
    price_change = (float(close.iloc[-1]) - float(close.iloc[0])) / float(close.iloc[0]) * 100
    obv_change = float(obv.iloc[-1]) - float(obv.iloc[0])

    if abs(price_change) < 5 and obv_change > 0:
        signals.append(f"株価横ばい（{price_change:+.1f}%）なのにOBVが上昇 → 水面下で買い集め")
        score += 25

    confidence = min(100, score)
    detected = confidence >= 40

    desc = ""
    if detected:
        desc = f"大口の買い集めの兆候あり（確信度{confidence}%）。" + signals[0] if signals else ""

    return {
        "detected": detected,
        "confidence": confidence,
        "signals": signals,
        "buying_pressure": round(avg_clv * 50 + 50),  # 0-100スケール
        "description": desc,
    }


def detect_algo_thresholds(df: pd.DataFrame) -> dict:
    """アルゴリズムの判定閾値を検出する。

    JPXで稼働するアルゴの主要な判定基準:
    - VWAP基準（機関の注文の大半がVWAPベース）
    - 移動平均（5/25/75/200日。25/75クロスが日本版ゴールデンクロス）
    - キリ番（100円/1000円刻みに注文が集中）
    - 出来高閾値（平均+2σでアルゴがブレイクアウト判定）
    - 板の偏り（買/売比率2:1超で方向性判定）
    """
    close = df["Close"]
    volume = df["Volume"]
    current = float(close.iloc[-1])

    # 1. VWAP（機関投資家の最重要ベンチマーク）
    # 日本市場ではVWAPからの乖離±0.1%がアルゴの売買トリガー
    recent_20 = df.tail(20)
    total_vol = float(recent_20["Volume"].sum())
    vwap = float((recent_20["Close"] * recent_20["Volume"]).sum() / total_vol) if total_vol > 0 else current
    vwap_deviation = (current - vwap) / vwap * 100

    # 2. キリ番（アルゴの指値が集中する価格）
    round_levels = []
    for step in [50, 100, 500, 1000]:
        if step > current * 0.5:
            continue
        base = int(current / step) * step
        for offset in [-2, -1, 0, 1, 2]:
            level = base + offset * step
            if level > 0 and abs(level - current) / current < 0.2:
                round_levels.append(level)

    # 3. 移動平均（日本市場: 5/25/75/200日が標準。25/75クロスが重要）
    ma_levels = {}
    for period in [5, 25, 75, 200]:
        if len(close) >= period:
            ma = float(close.rolling(period).mean().iloc[-1])
            if not np.isnan(ma):
                distance_pct = (current - ma) / current * 100
                ma_levels[f"MA{period}"] = {
                    "price": round(ma),
                    "distance_pct": round(distance_pct, 1),
                    "is_support": current > ma,
                    "is_resistance": current < ma,
                }

    # 25/75クロス検出（アルゴの重要判定）
    ma_cross = None
    if len(close) >= 75:
        ma25 = close.rolling(25).mean()
        ma75 = close.rolling(75).mean()
        if len(ma25.dropna()) >= 2 and len(ma75.dropna()) >= 2:
            prev_diff = float(ma25.iloc[-2] - ma75.iloc[-2])
            curr_diff = float(ma25.iloc[-1] - ma75.iloc[-1])
            if prev_diff < 0 and curr_diff > 0:
                ma_cross = "golden_cross"
            elif prev_diff > 0 and curr_diff < 0:
                ma_cross = "dead_cross"

    # 4. 出来高閾値（平均+2σでアルゴがブレイクアウト判定）
    vol_20avg = float(volume.rolling(20).mean().iloc[-1])
    vol_std = float(volume.rolling(20).std().iloc[-1])
    algo_vol_threshold = round(vol_20avg + vol_std * 2)

    # 5. 小型株のモメンタム点火リスク
    # 日次出来高5000万円未満 → アルゴがモメンタム点火を仕掛けやすい
    daily_value = current * vol_20avg
    momentum_ignition_risk = daily_value < 50_000_000

    return {
        "vwap": round(vwap),
        "vwap_deviation_pct": round(vwap_deviation, 2),
        "vwap_note": f"VWAP ¥{round(vwap):,}（乖離{vwap_deviation:+.2f}%）。機関は±0.1%以内で売買",
        "round_number_levels": sorted(set(round_levels)),
        "moving_avg_levels": ma_levels,
        "ma_cross": ma_cross,
        "ma_cross_note": {"golden_cross": "25/75日ゴールデンクロス検出（アルゴの買い判定）", "dead_cross": "25/75日デッドクロス検出（アルゴの売り判定）"}.get(ma_cross, ""),
        "volume_threshold": algo_vol_threshold,
        "volume_threshold_note": f"出来高{algo_vol_threshold:,}株超でアルゴがブレイクアウト判定",
        "momentum_ignition_risk": momentum_ignition_risk,
    }


def detect_algo_phase(df: pd.DataFrame) -> dict:
    """アルゴの参入フェーズを検出する。

    アルゴは出来高と価格の閾値で機械的に動く。
    今この銘柄にアルゴが入っているか、いないかを判定する。

    Returns:
        {
            "phase": "pre_algo" | "algo_entering" | "algo_active" | "algo_exiting",
            "description": 説明,
            "opportunity": 投資機会としての評価,
        }
    """
    close = df["Close"]
    volume = df["Volume"]

    if len(df) < 30:
        return {"phase": "unknown", "description": "データ不足", "opportunity": "unknown"}

    vol_25avg = float(volume.rolling(25).mean().iloc[-1])
    vol_today = float(volume.iloc[-1])
    vol_3d = float(volume.tail(3).mean())
    vol_ratio = vol_today / vol_25avg if vol_25avg > 0 else 1
    vol_3d_ratio = vol_3d / vol_25avg if vol_25avg > 0 else 1

    # 出来高の変化パターン
    vol_increasing = vol_3d_ratio > 1.5
    vol_explosive = vol_ratio > 5
    vol_declining_from_peak = False
    if len(volume) >= 10:
        peak_vol = float(volume.tail(10).max())
        vol_declining_from_peak = vol_today < peak_vol * 0.5 and peak_vol > vol_25avg * 3

    # 値動きの速度（日中ボラティリティ）
    intraday_range = (df["High"] - df["Low"]) / df["Close"]
    avg_range = float(intraday_range.tail(5).mean())
    normal_range = float(intraday_range.tail(60).mean())
    range_spike = avg_range > normal_range * 2

    # 判定
    if vol_explosive and range_spike:
        return {
            "phase": "algo_active",
            "description": f"アルゴが活発に動いている（出来高{vol_ratio:.0f}倍、値幅2倍超）。今から入ると高値掴みリスク大",
            "opportunity": "dangerous",
        }
    elif vol_increasing and not vol_explosive:
        return {
            "phase": "algo_entering",
            "description": f"アルゴが参入し始めている（出来高{vol_3d_ratio:.1f}倍）。モメンタム加速の初期段階",
            "opportunity": "caution",
        }
    elif vol_declining_from_peak:
        return {
            "phase": "algo_exiting",
            "description": "アルゴが撤退中（出来高がピークから半減）。売り圧力が増している",
            "opportunity": "exit",
        }
    else:
        return {
            "phase": "pre_algo",
            "description": "アルゴ未参入。静かな状態。仕込みのチャンス",
            "opportunity": "best",
        }


def detect_whale_accumulation(df: pd.DataFrame, info: dict = None) -> dict:
    """クジラ（大口投資家）の買い集めを総合判定する。

    Returns:
        {
            "whale_score": 大口介入スコア (0-100),
            "institutional": 機関投資家買いの検出結果,
            "algo": アルゴ閾値の検出結果,
            "summary": 総合判定,
        }
    """
    institutional = detect_institutional_buying(df)
    algo = detect_algo_thresholds(df)

    whale_score = institutional.get("confidence", 0)

    # EDINET情報があれば追加（大量保有報告）
    # TODO: EDINET APIから実際のデータを取得して判定

    summary_parts = []
    if institutional["detected"]:
        summary_parts.append(institutional["description"])

    # アルゴの近接レベル
    ma_levels = algo.get("moving_avg_levels", {})
    for ma_name, ma_data in ma_levels.items():
        dist = abs(ma_data["distance_pct"])
        if dist < 3:
            role = "サポート" if ma_data["is_support"] else "レジスタンス"
            summary_parts.append(f"{ma_name}(¥{ma_data['price']:,})が{role}として機能中（乖離{ma_data['distance_pct']:+.1f}%）。アルゴの判定ラインに近い")

    summary = " / ".join(summary_parts) if summary_parts else "大口の顕著な動きは検出されていない"

    return {
        "whale_score": whale_score,
        "institutional": institutional,
        "algo": algo,
        "summary": summary,
    }
