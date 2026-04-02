"""確度（コンビクション）判定モジュール

条件の数を絞り、バックテストで実証されたもの + 大口分析のみに集約。
過学習を避け、本当に効く条件だけを残す。
"""

# 確度条件（厳選12条件）
# バックテスト実証済み or 構造的に効く理由が明確なもののみ
CONVICTION_CHECKS = [
    # === 大口の動き（最重要。数値指標だけでは捉えられない） ===
    {
        "id": "whale_accumulating",
        "name": "大口仕込み中",
        "weight": 5,
        "category": "大口",
        "description": "出来高パターンから大口の仕込みを検出。まだ売りに出ていない=下支えあり",
        "check": lambda ctx: ctx.get("whale_phase", "none") in ("accumulating", "holding"),
    },
    {
        "id": "ml_contrarian",
        "name": "ML逆張り",
        "weight": 5,
        "category": "大口",
        "description": "統計モデルが負けと判定+大口が仕込み中=市場が見落としている",
        "check": lambda ctx: (ctx.get("ml_win_prob") is not None and ctx.get("ml_win_prob", 1) < 0.1) and ctx.get("whale_phase", "none") in ("accumulating", "holding"),
    },

    # === 出口の可視性（確度の本質） ===
    {
        "id": "exit_visible",
        "name": "出口が見える",
        "weight": 5,
        "category": "出口",
        "description": "具体的イベントで出口時期が明確+前座の実績あり",
        "check": lambda ctx: ctx.get("event_proximity_score", 0) >= 40 and ctx.get("timeline_clarity", 0) >= 60,
    },
    {
        "id": "stage_change_unpriced",
        "name": "ステージ変化+未織込",
        "weight": 5,
        "category": "ファンダ",
        "description": "数値で確定したステージ変化+株価が底値圏（市場が反応していない）",
        "check": lambda ctx: ctx.get("stage_score", 0) >= 20 and ctx["supply"].get("price_position", 50) < 40,
    },

    # === 需給構造（バックテスト実証済み） ===
    {
        "id": "pattern_85pct",
        "name": "底値+売り枯れ+大口",
        "weight": 5,
        "category": "需給",
        "description": "底値15%以下+売り枯れ(div>40)+大口買い検出(inst>20)。勝率85%、DD-6%",
        "check": lambda ctx: ctx["supply"].get("price_position", 50) < 15 and ctx["supply"].get("divergence", 0) > 40 and ctx.get("whale_score", 0) >= 20,
    },
    {
        "id": "pattern_83pct",
        "name": "深底値+ボラ+大口",
        "weight": 5,
        "category": "需給",
        "description": "深底値10%以下+ボラ2.5倍以上+大口買い。勝率83%、DD-4%（最小DD）",
        "check": lambda ctx: ctx["supply"].get("price_position", 50) < 10 and ctx.get("historical_range", 0) >= 2.5 and ctx.get("whale_score", 0) >= 20,
    },
    {
        "id": "pattern_81pct",
        "name": "低位+収縮+押し目",
        "weight": 5,
        "category": "需給",
        "description": "¥200以下+ボラ収縮(sq>70)+直近3日で-3%以上の押し目。勝率81%、+79%",
        "check": lambda ctx: ctx.get("current_price", 9999) < 200 and ctx["supply"].get("squeeze", 0) > 70 and ctx.get("ret_3d", 0) < -3,
    },
    {
        "id": "vol_up_and_dry",
        "name": "出来高増+売り枯れ",
        "weight": 5,
        "category": "需給",
        "description": "出来高1.5倍超+売り枯れ（バックテスト即効勝ち率16%）",
        "check": lambda ctx: ctx["supply"].get("volume_anomaly", 0) >= 1.5 and ctx["supply"].get("divergence", 0) > 20,
    },
    {
        "id": "blowoff_triggered",
        "name": "吹き上がり発火",
        "weight": 5,
        "category": "需給",
        "description": "準備完了（収縮+底値+売り枯れ）かつトリガー発火（出来高変化 or 直近シグナル）。準備だけでは動かない。トリガーとセットで初めて有効",
        "check": lambda ctx: ctx["supply"].get("squeeze", 0) > 70 and ctx["supply"].get("price_position", 50) < 25 and ctx["supply"].get("divergence", 0) >= 20 and (ctx["supply"].get("volume_anomaly", 0) >= 1.3 or ctx.get("timing_score", 0) >= 20),
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

    # === 信用（アルゴ・市場構造） ===
    {
        "id": "margin_favorable",
        "name": "信用良好",
        "weight": 3,
        "category": "信用",
        "description": "信用倍率3倍未満で上値の重しなし",
        "check": lambda ctx: 0 < ctx.get("margin_ratio", 0) < 3 or ctx.get("margin_ratio", 0) == 0,
    },

    # === タイミング（買った直後から上がるかどうかの鍵） ===
    {
        "id": "timing_signal",
        "name": "直近シグナル",
        "weight": 4,
        "category": "タイミング",
        "description": "ボラ収縮+出来高変化シグナル（バックテスト: 即UP率62%）。買った直後から上がりやすい",
        "check": lambda ctx: ctx.get("timing_score", 0) >= 25 and ctx["supply"].get("squeeze", 0) > 40,
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
