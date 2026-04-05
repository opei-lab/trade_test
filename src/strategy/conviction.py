"""確度（コンビクション）判定モジュール

条件の数を絞り、バックテストで実証されたもの + 大口分析のみに集約。
過学習を避け、本当に効く条件だけを残す。
"""

# 確度条件（厳選12条件）
# バックテスト実証済み or 構造的に効く理由が明確なもののみ
CONVICTION_CHECKS = [
    # === 10年バックテスト検証済み条件のみ ===
    # 効かないもの（大口CLV/OBV、squeeze、divergence>20）は廃止済み

    # === IR/ストーリー（最大lift源: +34%）===
    {
        "id": "ir_positive",
        "name": "IR良好",
        "weight": 5,
        "category": "IR",
        "description": "IRスコア20以上。バックテスト検証: IR良で勝率71%（+34% lift）",
        "check": lambda ctx: ctx.get("ir_score", 0) >= 20,
    },
    {
        "id": "no_negative_ir",
        "name": "IRリスクなし",
        "weight": 5,
        "category": "IR",
        "description": "ネガティブIR（希薄化/下方修正）なし。ネガティブ1件で勝率0%",
        "check": lambda ctx: len(ctx.get("ir_negative", [])) == 0,
    },
    {
        "id": "has_story",
        "name": "ストーリーあり",
        "weight": 4,
        "category": "IR",
        "description": "IR/ニュースに特色がある。ストーリーあり55% vs なし29%",
        "check": lambda ctx: ctx.get("has_story", False),
    },

    # === チャート構造（10年検証済み）===
    {
        "id": "gap_frequency",
        "name": "IR銘柄体質",
        "weight": 5,
        "category": "需給",
        "description": "窓あけ頻度30%以上。IRで頻繁に動く体質。low+gf+bot15=77%",
        "check": lambda ctx: ctx.get("gap_frequency", 0) >= 0.3,
    },
    {
        "id": "bot15",
        "name": "底値圏",
        "weight": 5,
        "category": "需給",
        "description": "底値15%以下。10年検証で全コンボの基盤",
        "check": lambda ctx: ctx["supply"].get("price_position", 50) < 15,
    },
    {
        "id": "low_price",
        "name": "低位株",
        "weight": 4,
        "category": "需給",
        "description": "300円以下。少ない資金で大きく動く。全環境で効く",
        "check": lambda ctx: ctx.get("current_price", 9999) < 300,
    },
    {
        "id": "bounce_recovery",
        "name": "底打ち反発",
        "weight": 4,
        "category": "需給",
        "description": "直近安値から10%以上反発。low+bounce+bot15=75%",
        "check": lambda ctx: ctx.get("bounce_from_low", 0) >= 10,
    },

    # === 安全性（負けにくさ） ===
    {
        "id": "asymmetric_floor",
        "name": "非対称+下値の床",
        "weight": 4,
        "category": "安全性",
        "description": "上方リターン>下方リスクの3倍+下値が限定的（負けても傷が浅い）",
        "check": lambda ctx: ctx.get("asymmetry", 0) > 55 and ctx.get("max_downside_pct", 100) < 20,
    },
    {
        "id": "no_dilution",
        "name": "希薄化リスクなし",
        "weight": 4,
        "category": "安全性",
        "description": "ワラント・増資等の希薄化リスクが検出されない",
        "check": lambda ctx: ctx.get("dilution_risk_count", 0) == 0,
    },

    # === 上値の軽さ ===
    {
        "id": "ceiling_clear_vacuum",
        "name": "上値軽い+真空地帯",
        "weight": 4,
        "category": "上値",
        "description": "しこりが少なく真空地帯あり（上がり始めれば一気に抵抗なく上昇）",
        "check": lambda ctx: ctx.get("ceiling_score", 100) < 30 or ctx.get("has_vacuum", False),
    },

    # === 信用（上値圧力の有無） ===
    {
        "id": "margin_favorable",
        "name": "信用良好",
        "weight": 4,
        "category": "信用",
        "description": "信用倍率3倍未満で上値の重しなし",
        "check": lambda ctx: 0 < ctx.get("margin_ratio", 0) < 3 or ctx.get("margin_ratio", 0) == 0,
    },
    {
        "id": "margin_heavy",
        "name": "信用圧力なし",
        "weight": 5,
        "category": "信用",
        "description": "信用倍率5倍超 or 買残増加+解消20日超でない（アルゴに狩られる需給構造がない）",
        "check": lambda ctx: not (
            ctx.get("margin_ratio", 0) > 5
            or (ctx.get("margin_ratio", 0) > 3 and ctx.get("margin_buy_change", 0) > 0)
            or ctx.get("margin_days_to_unwind", 0) > 20
        ),
    },

    # === タイミング ===
    {
        "id": "timing_signal",
        "name": "直近シグナル",
        "weight": 3,
        "category": "タイミング",
        "description": "タイミングスコア25以上（出来高変化 or サポート反発 or 投げ売り検出）",
        "check": lambda ctx: ctx.get("timing_score", 0) >= 25,
    },
    {
        "id": "high_daily_vol",
        "name": "高ボラ",
        "weight": 4,
        "category": "タイミング",
        "description": "日次ボラ4%以上。10年検証: lift+8%。動いてる銘柄が勝つ",
        "check": lambda ctx: ctx.get("daily_vol", 0) >= 4,
    },
    {
        "id": "crash_rsi_turn",
        "name": "急落+下げ切り",
        "weight": 5,
        "category": "タイミング",
        "description": "直近5日-8%急落+RSI反転。80%コンボの核。81%勝率",
        "check": lambda ctx: ctx.get("ret_5d", 0) <= -8 and ctx.get("rsi_turning", False),
    },

    # === 銘柄特性 ===
    {
        "id": "small_cap",
        "name": "小型株",
        "weight": 3,
        "category": "特性",
        "description": "時価総額300億未満（少ない資金で大きく動く）",
        "check": lambda ctx: 0 < ctx.get("market_cap", 0) < 30e9,
    },
]

# 最大重みスコア
MAX_WEIGHTED_SCORE = sum(c["weight"] for c in CONVICTION_CHECKS)


def load_optimized_weights():
    """保存済みの最適化結果があれば読み込んで適用する。"""
    import json
    from pathlib import Path
    opt_file = Path(__file__).parent.parent.parent / "data" / "optimization_result.json"
    if opt_file.exists():
        try:
            with open(opt_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            weights = data.get("weights", {})
            applied = 0
            for check in CONVICTION_CHECKS:
                if check["id"] in weights:
                    check["weight"] = weights[check["id"]]["weight"]
                    applied += 1
            global MAX_WEIGHTED_SCORE
            MAX_WEIGHTED_SCORE = sum(c["weight"] for c in CONVICTION_CHECKS)
            return applied
        except Exception:
            pass
    return 0

_applied = load_optimized_weights()


def calc_conviction(result: dict) -> dict:
    """スクリーニング結果から重み付き確度を算出する。"""
    ctx = {
        "supply": {
            "price_position": result.get("price_position", 50),
            "divergence": result.get("divergence", 0),
            "squeeze": result.get("squeeze", 0),
            "accumulation": result.get("accumulation", 0),
            "volume_anomaly": result.get("volume_anomaly", 0),
        },
        "ceiling_score": result.get("ceiling_score", 50),
        "margin_ratio": result.get("margin_ratio", 0),
        "margin_buy_change": result.get("margin_buy_change", 0),
        "margin_days_to_unwind": result.get("margin_days_to_unwind", 0),
        "max_downside_pct": result.get("max_downside_pct", 50),
        "asymmetry": result.get("asymmetry", 0),
        "market_cap": result.get("market_cap", 0),
        "float_scarcity": result.get("float_scarcity", 0),
        "safety_score": result.get("safety_score", 0),
        "timing_score": result.get("timing_score", 0),
        "stage_score": result.get("stage_score", 0),
        "dilution_risk_count": result.get("dilution_risk_count", 0),
        "event_proximity_score": result.get("event_proximity_score", 0),
        "has_vacuum": result.get("has_vacuum", False),
        "whale_score": result.get("whale_score", 0),
        "whale_phase": result.get("whale_phase", "none"),
        "current_price": result.get("current_price", 0),
        "historical_range": result.get("historical_range", 0),
        "ret_3d": result.get("ret_3d", 0),
        "timeline_clarity": result.get("expectation", {}).get("timeline_clarity", 0) if isinstance(result.get("expectation"), dict) else 0,
        "positive_catalysts": result.get("positive_catalysts", []),
        "ml_win_prob": result.get("ml_win_prob"),
        # 10年検証で追加された指標
        "ir_score": result.get("ir_score", 0),
        "ir_negative": result.get("ir_negative", []),
        "has_story": result.get("has_story", False),
        "gap_frequency": result.get("gap_frequency", 0),
        "bounce_from_low": result.get("bounce_from_low", 0),
        "daily_vol": result.get("daily_vol", 0),
        "rsi_turning": result.get("rsi_turning", False),
        "ret_5d": result.get("ret_5d", 0),
    }

    passed = []
    failed = []
    weighted_sum = 0
    by_category = {}

    for check in CONVICTION_CHECKS:
        cat = check.get("category", "other")
        if cat not in by_category:
            by_category[cat] = {"passed": 0, "total": 0, "weight_passed": 0, "weight_total": 0}
        by_category[cat]["total"] += 1
        by_category[cat]["weight_total"] += check["weight"]

        try:
            if check["check"](ctx):
                passed.append({
                    "id": check["id"], "name": check["name"],
                    "weight": check["weight"], "category": cat,
                    "description": check["description"],
                })
                weighted_sum += check["weight"]
                by_category[cat]["passed"] += 1
                by_category[cat]["weight_passed"] += check["weight"]
            else:
                failed.append({
                    "id": check["id"], "name": check["name"],
                    "weight": check["weight"], "category": cat,
                    "description": check["description"],
                })
        except Exception:
            failed.append({
                "id": check["id"], "name": check["name"],
                "weight": check["weight"], "category": cat,
                "description": check["description"],
            })

    score = round(weighted_sum / MAX_WEIGHTED_SCORE * 100) if MAX_WEIGHTED_SCORE > 0 else 0

    if score >= 75:
        grade = "S"
    elif score >= 55:
        grade = "A"
    elif score >= 40:
        grade = "B"
    elif score >= 25:
        grade = "C"
    else:
        grade = "D"

    return {
        "conviction_score": score,
        "conviction_count": len(passed),
        "weighted_sum": weighted_sum,
        "max_possible": MAX_WEIGHTED_SCORE,
        "total_checks": len(CONVICTION_CHECKS),
        "passed": passed,
        "failed": failed,
        "grade": grade,
        "by_category": by_category,
    }
