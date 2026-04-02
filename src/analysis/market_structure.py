"""市場構造分析モジュール

株価・出来高データだけから市場の構造を読み解く。
IRやニュースに頼らず、売買の痕跡から判断する。

分析項目:
1. 需給の締まり度合い
2. 売買の動き（誰が動いているか）
3. しこりの位置と影響
4. 大口の仕込み量と保有推定
5. 上値の余地（しこり+保有量から）
6. 売り抜け安全ライン
7. 損切りの妥当なライン
"""

import pandas as pd
import numpy as np


def analyze_full_structure(df: pd.DataFrame) -> dict:
    """株価・出来高データから市場構造を総合分析する。

    Returns:
        全分析結果を統合した辞書
    """
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]
    open_ = df["Open"]
    current = float(close.iloc[-1])

    result = {}

    # === 1. 需給の締まり ===
    result["tightness"] = calc_tightness(df)

    # === 2. 売買の動き ===
    result["flow"] = calc_order_flow(df)

    # === 3. しこりマップ ===
    result["resistance_map"] = calc_resistance_map(df, current)

    # === 4. 大口の推定 ===
    result["whale"] = estimate_whale_position(df)

    # === 5. 上値余地 ===
    result["upside"] = calc_upside_room(df, current, result["resistance_map"], result["whale"])

    # === 6. 売り抜け安全ライン ===
    result["safe_sell"] = calc_safe_sell_line(df, current, result["whale"])

    # === 7. 損切りライン ===
    result["stop_loss"] = calc_optimal_stop(df, current, result["resistance_map"])

    # === 総合スコア（0-100。高いほど買い） ===
    result["structure_score"] = calc_structure_score(result)

    return result


def calc_tightness(df: pd.DataFrame) -> dict:
    """需給の締まり度合い。

    売りが枯れて、わずかな買いで動く状態かどうか。
    """
    volume = df["Volume"]
    close = df["Close"]

    # 出来高の枯渇度
    vol_20 = float(volume.tail(20).mean())
    vol_60 = float(volume.tail(60).mean())
    vol_ratio = vol_20 / vol_60 if vol_60 > 0 else 1

    # ボリンジャーバンド幅
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_width = float((4 * std20 / sma20).iloc[-1]) if not sma20.empty else 0

    # 値幅の縮小
    daily_range = (df["High"] - df["Low"]) / df["Close"]
    range_20 = float(daily_range.tail(20).mean())
    range_60 = float(daily_range.tail(60).mean())
    range_ratio = range_20 / range_60 if range_60 > 0 else 1

    # 締まりスコア（出来高枯渇 + バンド幅縮小 + 値幅縮小）
    score = 0
    if vol_ratio < 0.7: score += 35  # 出来高が30%以上減少
    elif vol_ratio < 0.9: score += 20
    if bb_width < 0.05: score += 35  # バンド幅が非常に狭い
    elif bb_width < 0.10: score += 20
    if range_ratio < 0.7: score += 30  # 値幅が30%以上縮小
    elif range_ratio < 0.9: score += 15

    return {
        "score": min(100, score),
        "vol_ratio": round(vol_ratio, 2),
        "bb_width": round(bb_width, 4),
        "range_ratio": round(range_ratio, 2),
        "description": f"出来高{vol_ratio:.0%}（60日比）、バンド幅{bb_width:.3f}、値幅{range_ratio:.0%}",
    }


def calc_order_flow(df: pd.DataFrame) -> dict:
    """売買の流れ。誰が動いているか。"""
    close = df["Close"]
    open_ = df["Open"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]
    recent = df.tail(20)

    # CLV（終値位置。+1=高値引け、-1=安値引け）
    clv = ((close - low) - (high - close)) / (high - low + 1e-10)
    avg_clv = float(clv.tail(20).mean())

    # OBV（出来高ベースの累積売買圧力）
    obv = (np.sign(close.diff()) * volume).cumsum()
    obv_20 = float(obv.iloc[-1] - obv.iloc[-20]) if len(obv) >= 20 else 0
    price_20 = float(close.iloc[-1] - close.iloc[-20]) if len(close) >= 20 else 0

    # 乖離: 株価横ばいなのにOBV上昇 = 水面下の買い
    obv_divergence = obv_20 > 0 and abs(price_20 / float(close.iloc[-1])) < 0.03

    # 陽線比率
    bullish = float((recent["Close"] > recent["Open"]).mean())

    # 出来高増+値幅小 = 大口の分割買い
    vol_up = float(volume.tail(20).mean()) > float(volume.tail(60).mean()) * 1.2
    range_small = float(((recent["High"] - recent["Low"]) / recent["Close"]).mean()) < 0.02
    quiet_accumulation = vol_up and range_small

    score = 0
    signals = []
    if avg_clv > 0.2:
        score += 25
        signals.append(f"引け際に買い上げ（CLV {avg_clv:.2f}）")
    if obv_divergence:
        score += 30
        signals.append("株価横ばいでOBV上昇（水面下の買い集め）")
    if bullish >= 0.65:
        score += 20
        signals.append(f"陽線率{bullish:.0%}")
    if quiet_accumulation:
        score += 25
        signals.append("出来高増+値幅小（大口の分割買い）")

    return {
        "score": min(100, score),
        "clv": round(avg_clv, 3),
        "obv_divergence": obv_divergence,
        "bullish_ratio": round(bullish, 2),
        "quiet_accumulation": quiet_accumulation,
        "signals": signals,
    }


def calc_resistance_map(df: pd.DataFrame, current: float) -> dict:
    """しこりマップ。時間減衰付き。

    古い出来高ほどしこりとしての影響が薄い:
    - 直近3ヶ月: 100%（まだ残っている）
    - 3-6ヶ月前: 50%（信用は解消済み。現物は半分残る）
    - 6ヶ月-1年前: 20%（ほぼ解消済み）
    - 1年超: 5%（ほぼ影響なし）
    """
    close = df["Close"]
    volume = df["Volume"]

    bins = 30
    price_min, price_max = float(close.min()), float(close.max())
    if price_min >= price_max:
        return {"overhead_pct": 0, "zones": [], "clear_until": current * 2}

    # 時間減衰の重み付き出来高を計算
    n = len(df)
    decay_weights = np.ones(n)
    for i in range(n):
        days_ago = n - 1 - i
        if days_ago <= 60:
            decay_weights[i] = 1.0    # 直近3ヶ月: 100%
        elif days_ago <= 120:
            decay_weights[i] = 0.5    # 3-6ヶ月: 50%
        elif days_ago <= 250:
            decay_weights[i] = 0.2    # 6ヶ月-1年: 20%
        else:
            decay_weights[i] = 0.05   # 1年超: 5%

    weighted_volume = volume * decay_weights

    edges = np.linspace(price_min, price_max, bins + 1)
    zones = []
    total_vol = float(weighted_volume.sum())

    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        mid = (lo + hi) / 2
        mask = (close >= lo) & (close < hi)
        vol = float(weighted_volume[mask].sum())
        pct = vol / total_vol * 100 if total_vol > 0 else 0
        zones.append({"price_low": round(lo), "price_high": round(hi), "mid": round(mid), "vol_pct": round(pct, 1)})

    # 上方のしこり
    overhead = [z for z in zones if z["mid"] > current]
    overhead_pct = sum(z["vol_pct"] for z in overhead)

    # 最初の重いしこり（3%以上の出来高集中）
    heavy_above = [z for z in overhead if z["vol_pct"] >= 3]
    first_heavy = heavy_above[0]["mid"] if heavy_above else current * 2

    # 真空地帯（出来高が薄い連続帯）
    avg_pct = 100 / bins
    vacuum_start = None
    vacuum_end = None
    for z in overhead:
        if z["vol_pct"] < avg_pct * 0.3:
            if vacuum_start is None:
                vacuum_start = z["price_low"]
            vacuum_end = z["price_high"]
        else:
            if vacuum_start is not None:
                break

    return {
        "overhead_pct": round(overhead_pct, 1),
        "zones": zones,
        "first_heavy_resistance": round(first_heavy),
        "vacuum_start": round(vacuum_start) if vacuum_start else None,
        "vacuum_end": round(vacuum_end) if vacuum_end else None,
        "clear_until": round(first_heavy),
    }


def estimate_whale_position(df: pd.DataFrame) -> dict:
    """大口の推定保有量とコスト。"""
    close = df["Close"]
    volume = df["Volume"]
    current = float(close.iloc[-1])

    # ベースライン出来高（中央値ベース）
    baseline = volume.rolling(60).median()

    # 異常出来高の累積（15%が大口の純買いと推定）
    excess = (volume - baseline).clip(lower=0)
    total_excess = float(excess.tail(90).sum())
    estimated_shares = int(total_excess * 0.15)

    if estimated_shares <= 0:
        return {"detected": False, "shares": 0, "cost": 0, "holding_pct": 0}

    # VWAP（直近90日の出来高加重平均 = 大口の推定コスト）
    recent_90 = df.tail(90)
    total_vol = float(recent_90["Volume"].sum())
    vwap = float((recent_90["Close"] * recent_90["Volume"]).sum() / total_vol) if total_vol > 0 else current

    # 現在値とコストの関係
    cost_ratio = current / vwap if vwap > 0 else 1

    return {
        "detected": estimated_shares > 0,
        "shares": estimated_shares,
        "cost": round(vwap),
        "cost_ratio": round(cost_ratio, 3),
        "position": "含み益" if cost_ratio > 1.05 else "含み損" if cost_ratio < 0.95 else "コスト付近",
        "target_low": round(vwap * 2),
        "target_high": round(vwap * 3),
    }


def calc_upside_room(df: pd.DataFrame, current: float, resistance: dict, whale: dict) -> dict:
    """上値の余地。しこり vs 大口の力関係で判断。"""
    clear_until = resistance.get("clear_until", current * 2)
    overhead_pct = resistance.get("overhead_pct", 50)
    vacuum_start = resistance.get("vacuum_start")
    vacuum_end = resistance.get("vacuum_end")
    zones = resistance.get("zones", [])

    whale_shares = whale.get("shares", 0)
    whale_target = whale.get("target_low", 0)

    # しこりの総売り圧力（上方の出来高の推定株数）
    total_vol = sum(z.get("vol_pct", 0) for z in zones)
    overhead_zones = [z for z in zones if z.get("mid", 0) > current]
    overhead_shares_est = 0
    can_absorb = True
    absorb_until = current * 2  # デフォルト

    for z in sorted(overhead_zones, key=lambda x: x.get("mid", 0)):
        zone_vol_pct = z.get("vol_pct", 0)
        # この価格帯のしこり株数を推定（全出来高に対する割合で推定）
        zone_shares = zone_vol_pct * 10000  # 簡易推定

        overhead_shares_est += zone_shares

        # 大口がこのしこりを吸収できるか
        if whale_shares > 0 and overhead_shares_est > whale_shares * 0.5:
            # 大口の保有量の50%超のしこり = 吸収困難
            can_absorb = False
            absorb_until = z.get("mid", current * 1.5)
            break

    # しこりがない区間の幅
    clear_room_pct = (clear_until - current) / current * 100 if current > 0 else 0

    # スコア
    score = 0
    if overhead_pct < 20:
        score += 30
    elif overhead_pct < 35:
        score += 15
    if clear_room_pct > 20:
        score += 25
    elif clear_room_pct > 10:
        score += 10
    if vacuum_start:
        score += 25
    if can_absorb:
        score += 20  # 大口がしこりを消化できる

    # 目標価格の判定
    if can_absorb and whale_target > 0:
        realistic_target = whale_target  # 大口の目標まで到達可能
    elif not can_absorb:
        realistic_target = round(absorb_until * 0.95)  # 吸収できないしこりの手前
    else:
        realistic_target = clear_until

    return {
        "score": min(100, score),
        "clear_until": round(clear_until),
        "clear_room_pct": round(clear_room_pct, 1),
        "overhead_pct": overhead_pct,
        "first_target": round(min(realistic_target, clear_until)),
        "realistic_target": round(realistic_target),
        "can_absorb_shikori": can_absorb,
        "vacuum": f"¥{vacuum_start:,}〜¥{vacuum_end:,}" if vacuum_start and vacuum_end else None,
        "absorb_note": "大口の保有量でしこりを消化可能" if can_absorb else f"¥{round(absorb_until):,}付近のしこりが大口保有量を超える",
    }


def calc_safe_sell_line(df: pd.DataFrame, current: float, whale: dict) -> dict:
    """売り抜け安全ライン。"""
    cost = whale.get("cost", 0)
    shares = whale.get("shares", 0)
    vol_20 = float(df["Volume"].tail(20).mean())

    if cost <= 0 or shares <= 0:
        return {"safe_sell": round(current * 1.3), "days_to_sell": 0, "note": "大口未検出"}

    # 大口の売り抜けに必要な日数
    daily_sell = vol_20 * 0.15  # 出来高の15%で売る
    days_to_sell = int(shares / daily_sell) if daily_sell > 0 else 999

    # 安全売りライン = 大口がまだ売り切っていない間は支えられる
    # 大口コストの1.5倍が一旦の安全ライン
    safe_sell = round(cost * 1.5)

    return {
        "safe_sell": safe_sell,
        "days_to_sell": days_to_sell,
        "whale_cost": cost,
        "note": f"大口コスト¥{cost:,}の1.5倍。売り抜け所要{days_to_sell}日",
    }


def calc_optimal_stop(df: pd.DataFrame, current: float, resistance: dict) -> dict:
    """損切りの妥当なライン。"""
    close = df["Close"]

    # 直近のサポート（出来高が集中する下方の価格帯）
    zones = resistance.get("zones", [])
    supports_below = [z for z in zones if z["mid"] < current and z["vol_pct"] >= 2]
    if supports_below:
        strongest_support = max(supports_below, key=lambda z: z["vol_pct"])
        support_price = strongest_support["price_low"]
    else:
        support_price = current * 0.85

    # 直近20日の安値
    low_20 = float(df["Low"].tail(20).min())

    # 損切りライン = サポートのやや下（割ったら次のサポートまで落ちる）
    stop = min(round(support_price * 0.97), round(low_20 * 0.98))
    stop_pct = (current - stop) / current * 100

    return {
        "stop_price": stop,
        "stop_pct": round(stop_pct, 1),
        "support_price": round(support_price) if supports_below else None,
        "note": f"サポート¥{round(support_price):,}の下。-{stop_pct:.0f}%",
    }


def calc_structure_score(result: dict) -> int:
    """市場構造の総合スコア。"""
    score = 0
    score += result["tightness"]["score"] * 0.25
    score += result["flow"]["score"] * 0.30
    score += result["upside"]["score"] * 0.25
    score += (100 - result["resistance_map"]["overhead_pct"]) * 0.20
    return round(min(100, max(0, score)))


def format_structure_report(result: dict, current: float) -> str:
    """市場構造レポートを生成する。"""
    lines = []

    # 需給の締まり
    t = result["tightness"]
    lines.append(f"**需給の締まり** ({t['score']}/100)")
    lines.append(f"- {t['description']}")

    # 売買の動き
    f = result["flow"]
    lines.append(f"\n**売買の動き** ({f['score']}/100)")
    for sig in f["signals"]:
        lines.append(f"- {sig}")
    if not f["signals"]:
        lines.append("- 顕著な動きなし")

    # しこり
    r = result["resistance_map"]
    u = result["upside"]
    lines.append(f"\n**上値の状況** ({u['score']}/100)")
    lines.append(f"- しこり: 上方に出来高{r['overhead_pct']:.0f}%")
    lines.append(f"- 最初の重い抵抗: ¥{r['first_heavy_resistance']:,}（現在値から+{(r['first_heavy_resistance']-current)/current*100:.0f}%）")
    if u.get("vacuum"):
        lines.append(f"- 真空地帯: {u['vacuum']}（ここに入れば一気に上昇）")

    # 大口
    w = result["whale"]
    if w["detected"]:
        lines.append(f"\n**大口の推定**")
        lines.append(f"- 推定保有: {w['shares']:,}株、コスト¥{w['cost']:,}（{w['position']}）")
        lines.append(f"- 目標圏: ¥{w['target_low']:,}〜¥{w['target_high']:,}")

    # 売り抜け
    s = result["safe_sell"]
    lines.append(f"\n**売り抜け安全ライン**: ¥{s['safe_sell']:,}（{s['note']}）")

    # 損切り
    sl = result["stop_loss"]
    lines.append(f"**損切りライン**: ¥{sl['stop_price']:,}（{sl['note']}）")

    # 総合
    lines.append(f"\n**市場構造スコア: {result['structure_score']}/100**")

    return "\n".join(lines)
