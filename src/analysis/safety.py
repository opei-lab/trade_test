"""安全性・下方リスク評価モジュール

「純粋仕手」と「ファンダ裏付き銘柄」を区別する。
下方リスクが限定的で、上方リターンが非対称な銘柄を高評価する。
"""

import pandas as pd
import numpy as np


def calc_downside_floor(df: pd.DataFrame, info: dict) -> dict:
    """下値の「床」を推定する。

    ファンダメンタルの裏付けがある銘柄は、下落しても一定水準で止まる。
    過去の値動き + 財務データから「床」を推定する。

    Returns:
        {
            "floor_price": 推定下値（これ以下は下がりにくい）,
            "floor_confidence": 床の信頼度 (0-100),
            "max_downside_pct": 現在値からの最大下落率,
            "floor_type": 床の根拠,
        }
    """
    close = df["Close"]
    current = float(close.iloc[-1])

    # 1. 過去の強いサポート（出来高集中価格帯）
    price_bins = pd.cut(close, bins=30)
    vol_by_price = df.groupby(price_bins, observed=True)["Volume"].sum()
    # 現在値以下で最も出来高が集中する価格帯 = 強いサポート
    below_current = {
        interval: vol
        for interval, vol in vol_by_price.items()
        if (interval.left + interval.right) / 2 < current
    }

    if below_current:
        strongest_support_interval = max(below_current, key=below_current.get)
        volume_floor = (strongest_support_interval.left + strongest_support_interval.right) / 2
    else:
        volume_floor = float(close.min())

    # 2. PBR1倍ライン（解散価値）
    book_value_per_share = 0
    pbr = info.get("priceToBook", 0)
    if pbr and pbr > 0:
        book_value_per_share = current / pbr

    # 3. 過去の底値実績
    historical_low = float(close.tail(252).min()) if len(close) >= 252 else float(close.min())

    # 4. 床の選定（最も信頼できるものを採用）
    floors = []
    if volume_floor > 0:
        floors.append(("volume_support", volume_floor, 70))
    if book_value_per_share > 0:
        floors.append(("book_value", book_value_per_share, 60))
    if historical_low > 0:
        floors.append(("historical_low", historical_low, 50))

    if not floors:
        return {
            "floor_price": current * 0.7,
            "floor_confidence": 20,
            "max_downside_pct": 30.0,
            "floor_type": "no_data",
        }

    # 最も高い（最も楽観的な）床を採用
    floors.sort(key=lambda x: x[1], reverse=True)
    best_floor = floors[0]

    # 複数の床が近い位置にあれば信頼度UP
    if len(floors) >= 2:
        spread = abs(floors[0][1] - floors[1][1]) / current
        if spread < 0.05:  # 5%以内に複数の床 → 高信頼
            confidence = min(95, best_floor[2] + 20)
        else:
            confidence = best_floor[2]
    else:
        confidence = best_floor[2]

    floor_price = best_floor[1]
    max_downside = (current - floor_price) / current * 100

    return {
        "floor_price": round(floor_price),
        "floor_confidence": confidence,
        "max_downside_pct": round(max(0, max_downside), 1),
        "floor_type": best_floor[0],
    }


def calc_asymmetry_score(upside_pct: float, downside_pct: float) -> float:
    """リターンの非対称性スコアを算出する。

    上方リターンが下方リスクを大きく上回るほど高スコア。
    2倍以上を狙いつつ下方-20%以内なら最高評価。

    Returns:
        0-100のスコア。高いほど非対称（有利）。
    """
    if downside_pct < 0:
        return 0
    if downside_pct == 0:
        # 下落リスクゼロは非現実的だが、データ上なら最高評価
        return 95.0 if upside_pct > 0 else 0

    ratio = upside_pct / downside_pct

    # ratio 1 = 五分五分（スコア30）
    # ratio 3 = 良い（スコア60）
    # ratio 5+ = 優秀（スコア80+）
    # ratio 10+ = 最高（スコア95+）
    score = min(100, 20 + ratio * 12)
    return round(score, 1)


def is_pure_manipulation(df: pd.DataFrame, info: dict) -> dict:
    """純粋仕手（ファンダ裏付けなし）かどうかを判定する。

    純粋仕手の特徴:
    - 時価総額が極端に小さい（10億未満）
    - 売上/利益がほぼゼロで改善傾向なし
    - 過去に急騰→急落を繰り返している
    - 浮動株が極端に少なく操作しやすい

    Returns:
        {
            "is_pure": True/Falseの判定,
            "risk_factors": リスク要因のリスト,
            "safety_score": 安全性スコア (0-100, 高いほど安全),
        }
    """
    risk_factors = []
    safety = 100

    # 1. 時価総額チェック
    market_cap = info.get("market_cap", 0)
    if market_cap > 0 and market_cap < 1e9:  # 10億未満
        risk_factors.append(f"時価総額{market_cap/1e8:.0f}億（極小）")
        safety -= 25
    elif market_cap > 0 and market_cap < 5e9:  # 50億未満
        risk_factors.append(f"時価総額{market_cap/1e8:.0f}億（小型）")
        safety -= 10

    # 2. 急騰→急落の繰り返しチェック
    close = df["Close"]
    daily_returns = close.pct_change()
    big_up = (daily_returns > 0.15).sum()  # +15%以上の日
    big_down = (daily_returns < -0.15).sum()  # -15%以上の日

    if big_up >= 3 and big_down >= 3:
        risk_factors.append(f"急騰{big_up}回・急落{big_down}回（乱高下）")
        safety -= 20
    elif big_up >= 2 and big_down >= 2:
        risk_factors.append(f"急騰{big_up}回・急落{big_down}回")
        safety -= 10

    # 3. 浮動株比率チェック
    float_shares = info.get("float_shares", 0)
    outstanding = info.get("shares_outstanding", 0)
    if outstanding > 0 and float_shares > 0:
        float_ratio = float_shares / outstanding
        if float_ratio < 0.1:  # 10%未満
            risk_factors.append(f"浮動株比率{float_ratio*100:.0f}%（操作容易）")
            safety -= 15

    # 4. 売上チェック（売上ゼロ = 事業実態なし）
    revenue = info.get("totalRevenue", info.get("revenue", 0))
    if revenue is not None and revenue == 0 and market_cap > 0:
        risk_factors.append("売上ゼロ（事業実態なし）")
        safety -= 20
    elif revenue is not None and 0 < revenue < 1e8 and market_cap > 0:  # 売上1億未満
        risk_factors.append(f"売上{revenue/1e8:.2f}億（極小）")
        safety -= 10

    # 5. 52週高値/安値の比率（ボラティリティ）
    w52_high = info.get("fifty_two_week_high", 0)
    w52_low = info.get("fifty_two_week_low", 0)
    if w52_low > 0 and w52_high > 0:
        range_ratio = w52_high / w52_low
        if range_ratio > 5:  # 5倍以上の変動
            risk_factors.append(f"52週変動{range_ratio:.1f}倍（極端）")
            safety -= 15

    safety = max(0, safety)
    is_pure = safety < 40

    return {
        "is_pure": is_pure,
        "risk_factors": risk_factors,
        "safety_score": safety,
    }
