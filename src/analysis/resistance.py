"""しこり・上値抵抗分析モジュール

過去の高値圏で大量に買われた株（＝しこり）がどこにあるかを検出する。
現在値より上にしこりがあると、そこで売りが出て上値が重くなる。
"""

import pandas as pd
import numpy as np


def calc_volume_profile(df: pd.DataFrame, bins: int = 50) -> pd.DataFrame:
    """価格帯別出来高を算出する。

    Returns:
        DataFrame with columns: price_low, price_high, price_mid, volume, pct
    """
    close = df["Close"]
    volume = df["Volume"]

    price_min = float(close.min())
    price_max = float(close.max())
    bin_edges = np.linspace(price_min, price_max, bins + 1)

    result = []
    for i in range(len(bin_edges) - 1):
        low = bin_edges[i]
        high = bin_edges[i + 1]
        mask = (close >= low) & (close < high)
        vol = float(volume[mask].sum())
        result.append({
            "price_low": round(low),
            "price_high": round(high),
            "price_mid": round((low + high) / 2),
            "volume": vol,
        })

    result_df = pd.DataFrame(result)
    total_vol = result_df["volume"].sum()
    result_df["pct"] = (result_df["volume"] / total_vol * 100) if total_vol > 0 else 0

    return result_df


def detect_overhead_supply(df: pd.DataFrame, top_n: int = 5) -> dict:
    """現在値より上にある「しこり」（大量の含み損ポジション）を検出する。

    しこり = 過去に高値圏で大量に売買された → そこに到達すると売りが殺到
    → 上値が重くなる

    Returns:
        {
            "overhead_zones": [{"price": 価格, "volume_pct": 全出来高の何%, "distance_pct": 現在値からの距離%}],
            "total_overhead_pct": 現在値より上の出来高の割合,
            "heaviest_zone": 最も重いしこり帯,
            "is_clear": 上値が軽いかどうか,
        }
    """
    profile = calc_volume_profile(df)
    current = float(df["Close"].iloc[-1])

    # 現在値より上の出来高
    overhead = profile[profile["price_mid"] > current].copy()
    below = profile[profile["price_mid"] <= current]

    total_vol = profile["volume"].sum()
    overhead_vol = overhead["volume"].sum()
    total_overhead_pct = (overhead_vol / total_vol * 100) if total_vol > 0 else 0

    # 上にあるしこり帯（出来高上位）
    overhead_sorted = overhead.nlargest(top_n, "volume")
    zones = []
    for _, row in overhead_sorted.iterrows():
        if row["volume"] > 0:
            distance_pct = (row["price_mid"] - current) / current * 100
            zones.append({
                "price": int(row["price_mid"]),
                "volume_pct": round(row["pct"], 1),
                "distance_pct": round(distance_pct, 1),
            })

    heaviest = zones[0] if zones else None

    # 上値が軽い = 上方の出来高が全体の20%未満
    is_clear = total_overhead_pct < 20

    return {
        "overhead_zones": zones,
        "total_overhead_pct": round(total_overhead_pct, 1),
        "heaviest_zone": heaviest,
        "is_clear": is_clear,
    }


def calc_ceiling_score(df: pd.DataFrame, margin_data: dict = None) -> dict:
    """上値の重さを総合スコア化する。

    出来高しこり + 信用倍率を総合して「上がりにくさ」を数値化。
    0 = 上値軽い（良い）、100 = 上値激重（悪い）

    Returns:
        {
            "ceiling_score": 上値の重さスコア (0-100),
            "overhead_supply": しこり情報,
            "reasons": 重い理由のリスト,
        }
    """
    overhead = detect_overhead_supply(df)
    reasons = []
    score = 0

    # しこり評価
    overhead_pct = overhead["total_overhead_pct"]
    if overhead_pct > 50:
        score += 40
        reasons.append(f"上方に出来高{overhead_pct:.0f}%集中（しこり大）")
    elif overhead_pct > 30:
        score += 20
        reasons.append(f"上方に出来高{overhead_pct:.0f}%（しこりあり）")

    # 近いしこりの評価
    for zone in overhead.get("overhead_zones", []):
        if zone["distance_pct"] < 20 and zone["volume_pct"] > 5:
            score += 15
            reasons.append(f"¥{zone['price']:,}付近にしこり（+{zone['distance_pct']:.0f}%、出来高{zone['volume_pct']:.1f}%）")
            break  # 最も近いもの1つだけ

    # 信用倍率評価
    if margin_data:
        ratio = margin_data.get("margin_ratio", 0)
        if ratio > 5:
            score += 30
            reasons.append(f"信用倍率{ratio:.1f}倍（極めて重い）")
        elif ratio > 3:
            score += 15
            reasons.append(f"信用倍率{ratio:.1f}倍（重い）")
        elif ratio > 0 and ratio < 1:
            score -= 10  # 売り長 = 踏み上げ期待
            reasons.append(f"信用倍率{ratio:.1f}倍（売り長、踏み上げ期待）")

        if margin_data.get("is_heavy"):
            reasons.append(margin_data.get("heaviness_reason", ""))

    score = max(0, min(100, score))

    return {
        "ceiling_score": score,
        "overhead_supply": overhead,
        "reasons": [r for r in reasons if r],
    }


def detect_volume_vacuum(df: pd.DataFrame) -> dict:
    """出来高プロファイルの真空地帯を検出する（先行指標）。

    現在値のすぐ上に出来高が極端に薄い価格帯がある
    = そこに価格が入ったら売り圧力がないので一気に上がる。
    これは価格に先行する指標（遅行テクニカルではない）。

    Returns:
        {
            "has_vacuum": bool,
            "vacuum_start": 真空地帯の始まり,
            "vacuum_end": 真空地帯の終わり,
            "vacuum_width_pct": 真空地帯の幅（%）,
            "next_resistance": 真空地帯の先のレジスタンス,
            "description": 説明,
        }
    """
    profile = calc_volume_profile(df, bins=40)
    current = float(df["Close"].iloc[-1])
    total_vol = profile["volume"].sum()

    if total_vol == 0:
        return {"has_vacuum": False, "description": "データ不足"}

    # 現在値の上方で出来高が極端に薄い連続帯を探す
    overhead = profile[profile["price_mid"] > current].sort_values("price_mid")
    if overhead.empty:
        return {"has_vacuum": False, "description": "上方データなし"}

    avg_vol_pct = total_vol / len(profile)  # 1ビンあたりの平均出来高
    thin_threshold = avg_vol_pct * 0.3  # 平均の30%以下 = 「薄い」

    # 連続して薄い価格帯を探す
    vacuum_start = None
    vacuum_end = None
    max_vacuum_width = 0

    current_start = None
    current_end = None

    for _, row in overhead.iterrows():
        if row["volume"] < thin_threshold:
            if current_start is None:
                current_start = float(row["price_low"])
            current_end = float(row["price_high"])
        else:
            if current_start is not None:
                width = current_end - current_start
                if width > max_vacuum_width:
                    max_vacuum_width = width
                    vacuum_start = current_start
                    vacuum_end = current_end
                current_start = None
                current_end = None

    # 最後のチェック
    if current_start is not None:
        width = current_end - current_start
        if width > max_vacuum_width:
            vacuum_start = current_start
            vacuum_end = current_end

    if vacuum_start is None:
        return {"has_vacuum": False, "description": "上方に真空地帯なし"}

    vacuum_width_pct = (vacuum_end - vacuum_start) / current * 100

    # 真空地帯の先のレジスタンスを探す
    above_vacuum = profile[profile["price_mid"] > vacuum_end]
    next_resistance = None
    if not above_vacuum.empty:
        heaviest = above_vacuum.nlargest(1, "volume").iloc[0]
        next_resistance = float(heaviest["price_mid"])

    has_meaningful_vacuum = vacuum_width_pct >= 5  # 5%以上の幅がある場合のみ有効

    desc = ""
    if has_meaningful_vacuum:
        desc = f"¥{vacuum_start:,.0f}〜¥{vacuum_end:,.0f}に出来高の真空地帯（幅{vacuum_width_pct:.0f}%）。この価格帯に入れば売り圧力なしで一気に上昇する可能性。"
        if next_resistance:
            desc += f" 次のレジスタンスは¥{next_resistance:,.0f}。"

    return {
        "has_vacuum": has_meaningful_vacuum,
        "vacuum_start": round(vacuum_start) if vacuum_start else None,
        "vacuum_end": round(vacuum_end) if vacuum_end else None,
        "vacuum_width_pct": round(vacuum_width_pct, 1),
        "next_resistance": round(next_resistance) if next_resistance else None,
        "description": desc,
    }
