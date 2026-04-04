"""ファンダメンタル評価モジュール

セクター別の適正値に基づいて、0-100の独立スコアを算出する。
グロース市場特有の評価基準（割安=弱い、成長期待=強い）に対応。

スコアは他の軸（需給/IR/テーマ）と独立。
「まぁまぁ」は低点、「尖ってる」は高点の非線形評価。
"""

# セクター別の適正PBR/PER（バックテスト検証済み）
# グロース市場: PBR割安=見捨てられてる。成長期待が乗ってる方が強い
SECTOR_BENCHMARKS = {
    "Healthcare": {
        "pbr_sweet": (1.0, 5.0),
        "pbr_elite": (0.5, 2.0),
        "per_irrelevant": True,     # バイオ: PER無意味。パイプラインで評価
        "per_overvalued": None,     # 割高判定なし
        "note": "パイプラインの価値。PER無視。PBR低い=市場が過小評価",
    },
    "Technology": {
        "pbr_sweet": (1.5, 4.0),
        "pbr_elite": (1.0, 2.0),
        "per_sweet": (0, 0.01),     # 赤字の方が勝つ
        "per_elite_high": 100,      # PER100超=急成長
        "per_overvalued": None,     # Tech: 割高判定なし。高PERは成長の証
        "note": "今の利益は無意味。成長投資中の赤字はむしろ良い",
    },
    "Communication Services": {
        "pbr_sweet": (1.5, 4.0),
        "per_sweet": (15, 30),
        "per_overvalued": 80,       # PER80超で割高
        "note": "PBRは効かない。PER中程度が安定",
    },
    "Industrials": {
        "pbr_sweet": (2.0, 6.0),
        "pbr_elite": (3.0, 8.0),
        "per_sweet": (10, 25),
        "per_overvalued": 40,       # 製造業: PER40超で割高
        "note": "成長期待が正しく機能するセクター",
    },
    "Consumer Cyclical": {
        "pbr_sweet": (1.0, 3.0),
        "per_sweet": (10, 25),
        "per_overvalued": 35,       # 景気循環: PER35超で割高
        "note": "景気循環。PBR<1は妥当な場合あり",
    },
    "Real Estate": {
        "pbr_sweet": (0.8, 2.0),
        "pbr_elite": (0.5, 1.0),
        "per_sweet": (8, 20),
        "per_overvalued": 30,       # 不動産: PER30超で割高
        "note": "PBR≒NAV。1倍割れは本当の割安",
    },
    "Consumer Defensive": {
        "pbr_sweet": (1.0, 3.0),
        "per_sweet": (10, 20),
        "per_overvalued": 30,       # 安定業種: PER30超で割高
        "note": "安定型。バリュエーション通りに機能",
    },
    "Financial Services": {
        "pbr_sweet": (0.5, 1.5),
        "pbr_elite": (0.3, 0.8),
        "per_sweet": (8, 15),
        "per_overvalued": 25,       # 金融: PER25超で割高
        "note": "PBRが最重要。ROEとの相関が強い",
    },
}

DEFAULT_BENCHMARK = {
    "pbr_sweet": (1.0, 4.0),
    "per_sweet": (10, 30),
}

# 信用倍率の適正値（バックテスト検証済み）
# 5-10倍が最悪（勝率10%）。なし/軽いが安全
MARGIN_SCORE_TABLE = {
    # (lo, hi): score
    (0, 0.01): 70,      # 信用なし = ニュートラル〜良い
    (0.01, 1.0): 80,    # 売り長 = 踏み上げ燃料
    (1.0, 3.0): 50,     # 軽い
    (3.0, 5.0): 20,     # 重い
    (5.0, 10.0): 0,     # 致命的（勝率10%）
    (10.0, 20.0): 10,   # 異常。バイオ特殊事情あり
    (20.0, 999.0): 5,   # 極端。投機的
}


def calc_funda_score(info: dict, sector: str = "") -> dict:
    """ファンダメンタルの独立スコア（0-100）を算出する。

    セクター別の適正値に基づく。「まぁまぁ」は低く、「尖ってる」は高く。
    """
    pbr = info.get("priceToBook", 0) or info.get("pbr", 0) or 0
    per = info.get("trailingPE", 0) or info.get("per", 0) or 0
    mcap = info.get("market_cap", 0) or info.get("mcap", 0) or 0
    revenue = info.get("totalRevenue", 0) or info.get("revenue", 0) or 0

    bench = SECTOR_BENCHMARKS.get(sector, DEFAULT_BENCHMARK)
    score = 0
    reasons = []

    # === PBR評価 ===
    if pbr > 0:
        elite = bench.get("pbr_elite")
        sweet = bench.get("pbr_sweet", (1.0, 4.0))

        if elite and elite[0] <= pbr <= elite[1]:
            score += 40  # エリートゾーン
            reasons.append(f"PBR{pbr:.1f}（{sector}で最適帯）")
        elif sweet[0] <= pbr <= sweet[1]:
            score += 15  # スイートスポット
            reasons.append(f"PBR{pbr:.1f}（適正帯）")
        elif pbr < sweet[0]:
            # 割安が良いセクター（Healthcare, Real Estate）vs 悪いセクター
            if sector in ("Healthcare", "Real Estate"):
                score += 35
                reasons.append(f"PBR{pbr:.1f}（{sector}で割安=バリュー）")
            else:
                score += 5  # グロースで割安=見捨てられてる
                reasons.append(f"PBR{pbr:.1f}（グロースで低すぎ）")
        else:
            score += 0  # 割高
    else:
        score += 5  # PBR取得不可

    # === PER評価（セクター別割高ライン）===
    if bench.get("per_irrelevant"):
        if per == 0:
            score += 20
            reasons.append("赤字（バイオでは正常。成長投資中）")
    elif per > 0:
        sweet = bench.get("per_sweet", (10, 30))
        elite_high = bench.get("per_elite_high", 0)
        overvalued = bench.get("per_overvalued")

        if overvalued and per >= overvalued:
            score -= 20
            reasons.append(f"PER{per:.0f}（{sector}で割高。{overvalued}超）")
        elif elite_high and per >= elite_high:
            score += 30
            reasons.append(f"PER{per:.0f}（急成長）")
        elif sweet[0] <= per <= sweet[1]:
            score += 15
            reasons.append(f"PER{per:.0f}（{sector}適正帯）")
        elif per < sweet[0] and per > 0:
            score += 20
            reasons.append(f"PER{per:.0f}（割安）")
        else:
            score += 5
    elif per == 0:
        if sector in ("Technology", "Healthcare"):
            score += 25
            reasons.append(f"赤字（{sector}では正常。成長投資中）")
        else:
            score += 5
            reasons.append("赤字")

    # === 時価総額 ===
    if 0 < mcap < 3e9:
        score += 15  # 超小型 = 少ない資金で大きく動く
        reasons.append(f"時価総額{mcap/1e8:.0f}億（超小型）")
    elif mcap < 10e9:
        score += 10
        reasons.append(f"時価総額{mcap/1e8:.0f}億（小型）")
    elif mcap < 30e9:
        score += 5
    elif mcap > 100e9:
        score -= 5  # 大型は動きにくい

    # === PSR（売上ある場合）===
    if revenue > 0 and mcap > 0:
        psr = mcap / revenue
        if psr < 1:
            score += 10
            reasons.append(f"PSR{psr:.1f}（売上以下で割安）")
        elif psr < 3:
            score += 5

    score = max(0, min(100, score))

    return {
        "funda_score": score,
        "funda_reasons": reasons,
        "pbr": round(pbr, 2),
        "per": round(per, 1),
        "mcap": mcap,
        "sector": sector,
    }


def calc_margin_score(margin_ratio: float) -> dict:
    """信用倍率の独立スコア（0-100）を算出する。"""
    score = 50  # デフォルト
    reason = ""

    for (lo, hi), s in MARGIN_SCORE_TABLE.items():
        if lo <= margin_ratio < hi:
            score = s
            break

    if margin_ratio == 0:
        reason = "信用取引なし"
    elif margin_ratio < 1:
        reason = f"売り長{margin_ratio:.1f}倍（踏み上げ燃料）"
    elif margin_ratio < 3:
        reason = f"信用{margin_ratio:.1f}倍（軽い）"
    elif margin_ratio < 5:
        reason = f"信用{margin_ratio:.1f}倍（重い）"
    elif margin_ratio < 10:
        reason = f"信用{margin_ratio:.1f}倍（致命的。返済売りの壁）"
    else:
        reason = f"信用{margin_ratio:.1f}倍（極端。投機的）"

    return {
        "margin_score": score,
        "margin_reason": reason,
        "margin_ratio": margin_ratio,
        "is_fatal": 5 <= margin_ratio < 10,  # 致命的ゾーン
    }
