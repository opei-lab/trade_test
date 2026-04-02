"""Stage 2: 深層分析モジュール

Stage 1（高速フィルタ）を通過した候補に対して、
外部情報（ニュース、IR、信用残、イベントカレンダー）を統合し、
「いつまでに上昇するか」の期待値を出す。

Stage 1: 数値フィルタ（株価+出来高のみ、高速）→ 20-30候補
Stage 2: 本モジュール（外部データ統合、遅い）→ 5銘柄に厳選
"""

from datetime import date, timedelta
from src.data.price import fetch_price, get_stock_info
from src.data.margin import fetch_margin_data
from src.data.tdnet import detect_dilution_risk, detect_positive_catalysts
from src.analysis.resistance import calc_ceiling_score, detect_volume_vacuum
from src.analysis.sector_strength import calc_relative_strength
from src.analysis.whale_detection import detect_whale_accumulation
from src.analysis.whale_plan import reconstruct_whale_plan
from src.analysis.market_structure import analyze_full_structure, format_structure_report
from src.analysis.scenario import build_scenario
from src.analysis.stage_change import detect_financial_stage_change, format_stage_summary
from src.analysis.event_proximity import find_upcoming_events, calc_event_proximity_score
from src.analysis.timing import calc_timing_score
from src.analysis.safety import calc_downside_floor, calc_asymmetry_score, is_pure_manipulation
from src.strategy.conviction import calc_conviction
from src.strategy.multi_trade import generate_multi_trade_plan
from src.analysis.pipeline_value import calc_staged_targets_bio, calc_staged_targets_generic
from src.llm.news_analyzer import analyze_news_for_stage_change


def deep_analyze(candidate: dict) -> dict:
    """Stage 1の候補を深層分析する。

    外部データを取得し、確度判定・期間推定・レポート材料を追加する。
    """
    code = candidate["code"]
    result = dict(candidate)  # Stage 1の結果をベースに

    # --- 銘柄情報（キャッシュ済み） ---
    info = {}
    try:
        info = get_stock_info(code)
        result["name"] = info.get("name") or result.get("name", code)
        result["market_cap"] = info.get("market_cap", 0)
    except Exception:
        pass

    # --- 信用残 ---
    margin_data = {}
    try:
        margin_data = fetch_margin_data(code)
        result["margin_ratio"] = margin_data.get("margin_ratio", 0)
        result["margin_buy_change"] = margin_data.get("margin_buy_change", 0)
    except Exception:
        pass

    # --- しこり + 真空地帯 ---
    try:
        df = fetch_price(code, period_days=365)
        if not df.empty:
            ceiling = calc_ceiling_score(df, margin_data)
            result["ceiling_score"] = ceiling.get("ceiling_score", 0)
            result["overhead_pct"] = ceiling.get("overhead_supply", {}).get("total_overhead_pct", 0)

            vacuum = detect_volume_vacuum(df)
            result["has_vacuum"] = vacuum.get("has_vacuum", False)
            result["vacuum_desc"] = vacuum.get("description", "")
    except Exception:
        pass

    # --- ステージ変化（ファンダメンタル） ---
    stage = {}
    try:
        stage = detect_financial_stage_change(code)
        result["stage_score"] = stage.get("stage_score", 0)
        result["stage_summary"] = format_stage_summary(stage)
        result["market_gap"] = stage.get("market_gap", "none")
        result["stage_risks"] = stage.get("risks", [])
        result["dilution_risk_count"] = len([r for r in stage.get("risks", []) if r.get("type") in ("high_float", "cash_burn")])
    except Exception:
        pass

    # --- ニュース分析（LLMまたはキーワード） ---
    try:
        news_result = analyze_news_for_stage_change(code)
        result["news"] = news_result.get("news", [])
        result["news_signals"] = news_result.get("stage_signals", [])
    except Exception:
        pass

    # --- IR開示（希薄化リスク + ポジティブカタリスト） ---
    try:
        dilution = detect_dilution_risk(code)
        if dilution.get("has_risk"):
            result["dilution_risk_count"] = result.get("dilution_risk_count", 0) + len(dilution.get("details", []))
            result.setdefault("risk_factors", []).append("直近のワラント/増資IR検出")

        positive_ir = detect_positive_catalysts(code)
        if positive_ir:
            result["positive_catalysts"] = [d["title"] for d in positive_ir[:3]]
    except Exception:
        pass

    # --- イベント接近 ---
    sector = info.get("sector", "")
    industry = info.get("industry", "")
    events = find_upcoming_events(sector, industry)
    event_prox = calc_event_proximity_score(events)
    result["event_proximity_score"] = event_prox["score"]
    result["event_description"] = event_prox.get("description", "")
    result["upcoming_events"] = events[:3]

    # --- 市場構造分析 ---
    try:
        df_struct = fetch_price(code, period_days=365)
        if df_struct is not None and not df_struct.empty:
            structure = analyze_full_structure(df_struct)
            result["structure"] = structure
            result["structure_score"] = structure.get("structure_score", 0)
            result["structure_report"] = format_structure_report(structure, result.get("current_price", 0))

            # 構造分析からの具体的な値
            result["safe_sell"] = structure.get("safe_sell", {}).get("safe_sell", 0)
            result["optimal_stop"] = structure.get("stop_loss", {}).get("stop_price", 0)
            result["days_to_sell"] = structure.get("safe_sell", {}).get("days_to_sell", 0)

            # 構造分析の損切り/売りラインをtradeに反映
            if result.get("optimal_stop") and result["optimal_stop"] > 0:
                result["stop_loss"] = result["optimal_stop"]
            if result.get("safe_sell") and result["safe_sell"] > 0:
                result["target"] = result["safe_sell"]
                if result.get("entry", 0) > 0:
                    result["reward_pct"] = (result["safe_sell"] - result["entry"]) / result["entry"] * 100
                    result["risk_reward"] = result["reward_pct"] / ((result["entry"] - result["stop_loss"]) / result["entry"] * 100) if result["stop_loss"] > 0 else 0
    except Exception:
        pass

    # --- セクター相対強弱 ---
    try:
        from src.data.stocklist import get_stocks_by_sector as _gsbs
        # セクターキーワードを抽出
        sector_kw = sector.split("・")[0] if sector else industry.split(" ")[0] if industry else ""
        if sector_kw:
            rel_strength = calc_relative_strength(code, sector_kw)
            result["sector_rank"] = rel_strength.get("relative_rank", 0)
            result["sector_percentile"] = rel_strength.get("percentile", 50)
            result["sector_description"] = rel_strength.get("description", "")
            result["is_sector_leader"] = rel_strength.get("is_sector_leader", False)
    except Exception:
        pass

    # --- 大口・アルゴ検出 ---
    try:
        df = fetch_price(code, period_days=365)
        if not df.empty:
            whale = detect_whale_accumulation(df, info)
            result["whale_score"] = whale.get("whale_score", 0)
            result["whale_summary"] = whale.get("summary", "")
            result["algo_levels"] = whale.get("algo", {}).get("moving_avg_levels", {})
            result["institutional_detected"] = whale.get("institutional", {}).get("detected", False)
            result["institutional_signals"] = whale.get("institutional", {}).get("signals", [])

            # 大口の売買計画を逆算
            whale_plan = reconstruct_whale_plan(df, info)
            result["whale_plan"] = whale_plan
            result["whale_plan_detected"] = whale_plan.get("detected", False)
            result["whale_plan_description"] = whale_plan.get("description", "")
            result["whale_phase"] = whale_plan.get("remaining", {}).get("phase", "none")
    except Exception:
        pass

    # --- 直近シグナル ---
    try:
        df = fetch_price(code, period_days=365)
        if not df.empty:
            timing_result = calc_timing_score(df)
            result["timing_score"] = timing_result["timing_score"]
            result["urgency"] = timing_result["urgency"]
            result["timing_signals"] = timing_result["signals"]
            result["timing_desc"] = timing_result["description"]
    except Exception:
        pass

    # --- 安全性 ---
    try:
        df = fetch_price(code, period_days=365)
        if not df.empty:
            manip = is_pure_manipulation(df, info)
            result["safety_score"] = manip["safety_score"]
            result["risk_factors"] = result.get("risk_factors", []) + manip.get("risk_factors", [])

            floor = calc_downside_floor(df, info)
            result["floor_price"] = floor.get("floor_price", 0)
            result["max_downside_pct"] = floor.get("max_downside_pct", 0)

            result["asymmetry"] = calc_asymmetry_score(
                result.get("reward_pct", 0),
                floor.get("max_downside_pct", result.get("risk_pct", 10)),
            )
    except Exception:
        pass

    # --- 段階トレードプラン ---
    try:
        trade_plan = generate_multi_trade_plan(
            current_price=result.get("current_price", 0),
            market_cap=info.get("market_cap", 0),
            sector=sector,
            industry=industry,
        )
        result["trade_plan"] = trade_plan
    except Exception:
        pass

    # --- 段階目標 ---
    is_bio = any(kw in f"{sector} {industry}".lower() for kw in ["healthcare", "biotech", "医薬品", "drug"])
    try:
        if is_bio and info.get("market_cap", 0) > 0:
            staged = calc_staged_targets_bio(
                current_price=result.get("current_price", 0),
                market_cap=info.get("market_cap", 0),
                target_market_size=1e12,
                current_phase="phase2",
            )
            result["staged_targets"] = staged
        else:
            from src.strategy.screener import find_price_targets
            df = fetch_price(code, period_days=365)
            if not df.empty:
                levels = find_price_targets(df)
                staged = calc_staged_targets_generic(
                    current_price=result.get("current_price", 0),
                    historical_high=levels["historical_high"],
                    prev_highs=levels.get("prev_highs", []),
                    stage_score=stage.get("stage_score", 0),
                )
                result["staged_targets"] = staged
    except Exception:
        pass

    # --- Stage 3: シナリオ構築（IR/ニュースからストーリーを作る） ---
    try:
        scenario = build_scenario(
            code, result.get("name", code), result.get("current_price", 0),
            structure=result.get("structure"),
        )
        result["scenario"] = scenario
        result["has_story"] = scenario.get("has_story", False)
        result["impact_score"] = scenario.get("impact_score", 0)
        result["scenario_text"] = scenario.get("scenario", "")
        result["ir_summary"] = scenario.get("ir_summary", [])
    except Exception:
        result["has_story"] = True  # エラー時は除外しない
        result["impact_score"] = 0

    # --- 確度（全情報統合後に判定） ---
    result["conviction"] = calc_conviction(result)
    result["conviction_grade"] = result["conviction"]["grade"]
    result["conviction_count"] = result["conviction"]["conviction_count"]

    # --- 期待値サマリー ---
    result["expectation"] = build_expectation(result)

    return result


def build_expectation(r: dict) -> dict:
    """全情報を統合して「いつまでに・いくらまで・確度は」を算出する。

    期間が明確なほど価値が高い。
    「6月のASCOで結果が出る → そこで上がる」のように
    イベントと紐付いた期間は最も信頼度が高い。
    """
    target = r.get("target", 0)
    reward_pct = r.get("reward_pct", 0)

    # 期間推定（優先順位付き）
    milestones = []  # {date, event, confidence}
    events = r.get("upcoming_events", [])
    tf = r.get("timeframe", {})
    positive_ir = r.get("positive_catalysts", [])

    # 1) 具体的なイベント（最も明確）
    for ev in events[:3]:
        days = ev.get("days_until", 0)
        if days > 0:
            milestones.append({
                "date": (date.today() + timedelta(days=days)).isoformat(),
                "days": days,
                "event": ev.get("event_name", ""),
                "impact": ev.get("impact", ""),
                "confidence": "high" if days <= 60 else "medium",
                "source": "event_calendar",
            })

    # 2) ポジティブIRの後続イベント
    if positive_ir:
        milestones.append({
            "date": None,
            "days": None,
            "event": f"IR: {positive_ir[0][:30]}",
            "impact": "IRの内容次第で市場が反応",
            "confidence": "medium",
            "source": "ir",
        })

    # 3) 過去パターンからの推定
    if tf.get("estimated_days"):
        days = tf["estimated_days"]
        milestones.append({
            "date": (date.today() + timedelta(days=days)).isoformat(),
            "days": days,
            "event": "過去パターンからの推定",
            "impact": tf.get("description", ""),
            "confidence": tf.get("confidence", "low"),
            "source": "backtest",
        })

    # 最も近い確定イベント
    dated_milestones = [m for m in milestones if m.get("date")]
    nearest_dated = min(dated_milestones, key=lambda m: m["days"]) if dated_milestones else None

    # 期間の明確度スコア（0-100）
    if nearest_dated and nearest_dated["confidence"] == "high":
        timeline_clarity = 90
    elif nearest_dated and nearest_dated["confidence"] == "medium":
        timeline_clarity = 60
    elif nearest_dated:
        timeline_clarity = 30
    else:
        timeline_clarity = 0

    # 期待リターン = 目標リターン × 確度スコア / 100
    conv_score = r.get("conviction", {}).get("conviction_score", 0)
    expected_return = reward_pct * conv_score / 100

    # サマリー生成
    if nearest_dated:
        period_str = f"{nearest_dated['event']}（{nearest_dated['days']}日後、{nearest_dated['date']}）"
    else:
        period_str = "期間不明（イベント未検出）"

    summary = f"目標¥{target:,}（+{reward_pct:.0f}%）、{period_str}、確度{r.get('conviction_grade', '?')}（期待値+{expected_return:.0f}%）"

    return {
        "target_price": target,
        "target_date": nearest_dated["date"] if nearest_dated else None,
        "target_days": nearest_dated["days"] if nearest_dated else None,
        "period_source": period_str,
        "milestones": milestones,
        "timeline_clarity": timeline_clarity,
        "expected_return_pct": round(expected_return, 1),
        "conviction_grade": r.get("conviction_grade", "?"),
        "conviction_score": conv_score,
        "summary": summary,
    }


def run_deep_analysis(candidates: list[dict], progress_callback=None) -> list[dict]:
    """Stage 1候補リストに対してStage 2の深層分析を実行する。

    件数制限なし。確度B以上を全て返す。
    期間が明確な銘柄を上位にソートする。
    """
    results = []
    total = len(candidates)

    for i, c in enumerate(candidates):
        if progress_callback:
            progress_callback(i, total, c.get("code", ""))

        try:
            deep = deep_analyze(c)
            results.append(deep)
        except Exception:
            results.append(c)

    # ソート: 出口可視性 × 確度 × 非対称リターン × 低位株ボーナス
    def sort_key(x):
        clarity = x.get("expectation", {}).get("timeline_clarity", 0) if isinstance(x.get("expectation"), dict) else 0
        conv = x.get("conviction", {}).get("conviction_score", 0) if isinstance(x.get("conviction"), dict) else 0
        ev = x.get("expectation", {}).get("expected_return_pct", 0) if isinstance(x.get("expectation"), dict) else 0
        price = x.get("current_price", 9999)
        low_price_bonus = 20 if 0 < price < 500 else 10 if price < 1000 else 0
        return clarity * 10 + conv * 5 + ev + low_price_bonus

    results.sort(key=sort_key, reverse=True)

    # Stage 3フィルタ: ストーリーがない銘柄を除外
    with_story = [r for r in results if r.get("has_story", True)]
    without_story = [r for r in results if not r.get("has_story", True)]

    return with_story
