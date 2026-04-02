"""詳細トレードプラン生成モジュール

複数銘柄を跨いだ売買タイミングを提案する。
「銘柄Aを○円で買い、○月に売り、その資金でBを買う」

各銘柄に対して:
- エントリー価格と理由
- アルゴにポジションを切られないための期限
- 損切りライン（切るか、切らないかの判断材料）
- 下限の見込みと回復期間
- ホールド/売りの判断ポイント
"""

from datetime import date, timedelta


def generate_detailed_trade_plan(stocks: list[dict], total_capital: float = 100) -> dict:
    """複数銘柄の詳細トレードプランを生成する。

    Args:
        stocks: deep_analysisの結果リスト（確度順）
        total_capital: 投資資金（万円）

    Returns:
        全体のトレードプラン
    """
    today = date.today()
    plans = []
    remaining_capital = total_capital

    # 銘柄をイベント日付順にソート
    dated_stocks = []
    undated_stocks = []
    for s in stocks:
        exp = s.get("expectation", {})
        target_date = exp.get("target_date")
        if target_date:
            dated_stocks.append(s)
        else:
            undated_stocks.append(s)

    dated_stocks.sort(key=lambda x: x.get("expectation", {}).get("target_date", "9999"))

    # 期間が明確な銘柄を優先的に配置
    all_stocks = dated_stocks + undated_stocks

    for i, s in enumerate(all_stocks):
        if remaining_capital <= 0:
            break

        plan = build_single_plan(s, today, remaining_capital, total_capital, i)
        if plan:
            plans.append(plan)
            # 次の銘柄への資金配分
            if plan.get("sell_date") and i < len(all_stocks) - 1:
                # 売却後に資金が回収される想定
                pass  # 同時保有の場合は分割
            else:
                remaining_capital -= plan.get("allocated_capital", 0)

    # 全体サマリー
    total_expected_profit = sum(p.get("expected_profit", 0) for p in plans)
    total_allocated = sum(p.get("allocated_capital", 0) for p in plans)

    return {
        "plans": plans,
        "total_capital": total_capital,
        "total_allocated": round(total_allocated, 1),
        "total_expected_profit": round(total_expected_profit, 1),
        "expected_return_pct": round(total_expected_profit / total_capital * 100, 1) if total_capital > 0 else 0,
        "timeline_summary": build_timeline_summary(plans),
    }


def build_single_plan(s: dict, today: date, remaining_capital: float, total_capital: float, index: int) -> dict:
    """1銘柄の詳細トレードプランを生成する。"""
    code = s.get("code", "")
    name = s.get("name", code)
    current = s.get("current_price", 0)
    entry = s.get("entry", 0)
    target = s.get("target", 0)
    stop_loss = s.get("stop_loss", 0)
    floor_price = s.get("floor_price", 0)
    reward_pct = s.get("reward_pct", 0)
    risk_pct = s.get("risk_pct", 0)

    exp = s.get("expectation", {})
    target_date_str = exp.get("target_date")
    target_days = exp.get("target_days")
    milestones = exp.get("milestones", [])
    conv_grade = s.get("conviction_grade", "?")
    conv_score = s.get("conviction", {}).get("conviction_score", 0)

    # 資金配分（集中投資: 確度が高ければ大きく張る。分散しすぎない）
    if conv_grade in ("S", "A"):
        alloc_pct = 0.50  # 確度最高なら資金の半分
    elif conv_grade == "B":
        alloc_pct = 0.35
    else:
        alloc_pct = 0.20
    allocated = min(remaining_capital, total_capital * alloc_pct)

    if entry <= 0 or current <= 0:
        return None

    # エントリー判断
    entry_guidance = build_entry_guidance(s, current, entry, today)

    # 損切り判断
    stoploss_guidance = build_stoploss_guidance(s, entry, stop_loss, floor_price, current)

    # 売りタイミング判断
    sell_guidance = build_sell_guidance(s, entry, target, milestones, today)

    # 期間中の想定シナリオ
    scenarios = build_scenarios(s, entry, target, stop_loss, floor_price, target_days)

    # 売却予定日
    sell_date = None
    if target_date_str:
        try:
            sell_date = target_date_str
        except Exception:
            pass

    expected_profit = allocated * (reward_pct / 100) * (conv_score / 100)

    return {
        "code": code,
        "name": name,
        "conviction_grade": conv_grade,
        "conviction_score": conv_score,
        "current_price": current,
        "entry": entry,
        "target": target,
        "stop_loss": stop_loss,
        "floor_price": floor_price,
        "reward_pct": reward_pct,
        "allocated_capital": round(allocated, 1),
        "sell_date": sell_date,
        "target_days": target_days,
        "expected_profit": round(expected_profit, 1),
        "entry_guidance": entry_guidance,
        "stoploss_guidance": stoploss_guidance,
        "sell_guidance": sell_guidance,
        "scenarios": scenarios,
        "milestones": milestones,
    }


def build_entry_guidance(s: dict, current: float, entry: float, today: date) -> dict:
    """エントリーの詳細ガイダンスを生成する。"""
    timing = s.get("timing", "WAIT")
    timing_score = s.get("timing_score", 0)
    urgency = s.get("urgency", "watching")

    # アルゴにポジションを切られるリスク
    # イベント前にアルゴが入り始めると、エントリー価格が上がる
    events = s.get("upcoming_events", [])
    algo_deadline = None
    algo_warning = ""
    if events:
        nearest = events[0]
        days_to = nearest.get("days_until", 0)
        if days_to > 14:
            # イベント2週間前にアルゴが動き始める想定
            algo_deadline_date = today + timedelta(days=max(0, days_to - 14))
            algo_deadline = algo_deadline_date.isoformat()
            algo_warning = f"{nearest.get('event_name', '')}の2週間前（{algo_deadline}）までにインしないと、アルゴにポジションを先に取られる可能性がある"
        elif days_to > 0:
            algo_warning = f"{nearest.get('event_name', '')}が{days_to}日後に迫っている。既にアルゴが動いている可能性あり。出来高の変化を確認してから判断"

    # エントリー価格の判断
    if current <= entry * 1.03:
        price_guidance = f"現在値¥{current:,.0f}はエントリー圏内（目安¥{entry:,}）。即エントリー可能"
    elif current <= entry * 1.10:
        price_guidance = f"現在値¥{current:,.0f}はエントリー目安¥{entry:,}の+{(current/entry-1)*100:.0f}%上。¥{entry:,}まで引きつけたいが、{algo_warning}の場合は現在値でもエントリー検討"
    else:
        price_guidance = f"現在値¥{current:,.0f}はエントリー目安¥{entry:,}から乖離。¥{entry:,}付近まで待つ"

    # エントリー上限
    max_entry = round(entry * 1.15)

    return {
        "price_guidance": price_guidance,
        "entry_price": entry,
        "max_entry_price": max_entry,
        "max_entry_note": f"¥{max_entry:,}を超えて購入した場合、損切りライン¥{s.get('stop_loss', 0):,}が近くなりRR比が悪化する",
        "algo_deadline": algo_deadline,
        "algo_warning": algo_warning,
        "urgency": urgency,
    }


def build_stoploss_guidance(s: dict, entry: float, stop_loss: float, floor_price: float, current: float) -> dict:
    """損切りの詳細判断を生成する。"""
    max_downside = s.get("max_downside_pct", 0)
    safety_score = s.get("safety_score", 0)
    stage_score = s.get("stage_score", 0)

    # 損切りするかしないかの判断
    if floor_price > 0 and floor_price > stop_loss:
        # ファンダの床が損切りラインより上 → 損切りしない選択肢
        cut_or_hold = "hold_option"
        rationale = f"ファンダの床¥{floor_price:,}が損切りライン¥{stop_loss:,}より上にある。下値は限定的なため、損切りせずホールドする選択肢がある"
    elif stage_score >= 20:
        cut_or_hold = "hold_option"
        rationale = f"ステージ変化が検出されている。一時的な下落はあり得るが、ファンダメンタルが変わった事実は消えないため、損切りせずホールドする選択肢がある"
    elif safety_score >= 60:
        cut_or_hold = "conditional"
        rationale = f"損切りライン¥{stop_loss:,}（エントリーの-10%）を基本とするが、出来高が枯れた状態での下落は一時的な可能性がある。出来高急増+下落なら即損切り"
    else:
        cut_or_hold = "strict"
        rationale = f"¥{stop_loss:,}で損切り。ファンダの裏付けが弱いため、含み損を抱えるリスクが高い"

    # 最悪ケースの想定
    worst_case = ""
    if max_downside > 0:
        worst_price = round(current * (1 - max_downside / 100))
        worst_case = f"最悪ケース: ¥{worst_price:,}付近（現在値から-{max_downside:.0f}%）まで下落する可能性がある"

    return {
        "stop_loss": stop_loss,
        "floor_price": floor_price,
        "cut_or_hold": cut_or_hold,
        "rationale": rationale,
        "worst_case": worst_case,
    }


def build_sell_guidance(s: dict, entry: float, target: float, milestones: list, today: date) -> dict:
    """売りタイミングの詳細ガイダンスを生成する。"""
    staged_targets = s.get("staged_targets", [])

    # 段階的な利確ポイント
    sell_points = []

    if staged_targets:
        for st in staged_targets:
            sell_points.append({
                "price": st.get("target_price", 0),
                "label": st.get("step", ""),
                "probability": st.get("probability", 0),
                "action": f"¥{st.get('target_price', 0):,}到達で一部利確（{st.get('probability', 0)}%の確率で到達）",
            })

    # マイルストーンベースの売り判断
    decision_points = []
    for m in milestones[:5]:
        dp = {
            "date": m.get("date"),
            "event": m.get("event", ""),
            "action": "",
        }
        if m.get("source") == "event_calendar":
            dp["action"] = f"{m['event']}の結果を確認。ポジティブなら目標まで継続保有。ネガティブなら即売り"
        elif m.get("source") == "ir":
            dp["action"] = f"IRの内容を精査。ステージ変化が確認できれば目標を上方修正。リスク情報なら売り検討"
        else:
            dp["action"] = f"この時点での株価・出来高を確認し、継続判断"
        decision_points.append(dp)

    return {
        "target": target,
        "sell_points": sell_points,
        "decision_points": decision_points,
        "final_note": "目標到達前でも、出来高が急増→急減するパターンが出たらピーク接近のサイン。利確を検討",
    }


def build_scenarios(s: dict, entry: float, target: float, stop_loss: float, floor_price: float, target_days: int = None) -> list:
    """期間中の想定シナリオを生成する。"""
    scenarios = []

    # シナリオ1: 順調ケース
    scenarios.append({
        "name": "順調ケース",
        "probability": "30-40%",
        "description": f"エントリー後、需給改善が続き¥{target:,}に到達。途中の下落は-5%以内",
        "action": "目標到達で利確",
    })

    # シナリオ2: 時間がかかるケース
    hold_period = f"{target_days}日" if target_days else "数ヶ月"
    scenarios.append({
        "name": "時間がかかるケース",
        "probability": "30-40%",
        "description": f"エントリー後、横ばいまたは緩やかな上昇。目標到達まで{hold_period}。途中で-10〜15%の下落あり",
        "action": f"¥{floor_price:,}付近まで下げても出来高が枯れていれば回復を待つ。動揺しないこと",
    })

    # シナリオ3: 一時的な急落ケース
    scenarios.append({
        "name": "一時的な急落（振るい落とし）",
        "probability": "15-20%",
        "description": f"エントリー後に¥{stop_loss:,}付近まで急落。弱い持ち株を振るい落とす動き",
        "action": f"出来高が急増+急落 → 出来高が枯れる → 回復、のパターンならホールド。¥{floor_price:,}を明確に割り込んだら損切り検討",
    })

    # シナリオ4: 失敗ケース
    scenarios.append({
        "name": "失敗ケース",
        "probability": "10-20%",
        "description": f"ファンダメンタルの悪化（ワラント発行、下方修正等）で¥{stop_loss:,}を割る",
        "action": f"¥{stop_loss:,}で損切り。IR等のネガティブ材料が出た場合は損切りラインを待たず即売り",
    })

    return scenarios


def build_timeline_summary(plans: list) -> str:
    """全プランのタイムラインサマリーを生成する。"""
    if not plans:
        return "対象銘柄なし"

    lines = []
    today = date.today()

    for p in plans:
        code = p["code"]
        name = p["name"]
        entry = p["entry"]
        target = p["target"]
        sell_date = p.get("sell_date", "")
        grade = p["conviction_grade"]
        alloc = p["allocated_capital"]

        if sell_date:
            lines.append(f"• {name}({code}): ¥{entry:,}で購入 → {sell_date}頃に¥{target:,}で売却目標 [{grade}] 配分¥{alloc:.0f}万")
        else:
            lines.append(f"• {name}({code}): ¥{entry:,}で購入 → ¥{target:,}で売却目標（期間未定）[{grade}] 配分¥{alloc:.0f}万")

    return "\n".join(lines)


def format_trade_plan_full(plan: dict) -> str:
    """詳細トレードプランをMarkdownで出力する。"""
    lines = [
        "## 詳細トレードプラン\n",
        f"投資資金: ¥{plan['total_capital']}万 → 配分: ¥{plan['total_allocated']}万",
        f"期待利益: ¥{plan['total_expected_profit']}万（+{plan['expected_return_pct']}%）\n",
        "### タイムライン\n",
        plan["timeline_summary"],
        "",
    ]

    for i, p in enumerate(plan["plans"]):
        lines.append(f"\n---\n### {i+1}. {p['name']} ({p['code']}) 確度{p['conviction_grade']}（{p['conviction_score']}pt）")
        lines.append(f"配分: ¥{p['allocated_capital']}万\n")

        # エントリー
        eg = p["entry_guidance"]
        lines.append("**エントリー**")
        lines.append(eg["price_guidance"])
        lines.append(f"- エントリー上限: ¥{eg['max_entry_price']:,}。{eg['max_entry_note']}")
        if eg["algo_warning"]:
            lines.append(f"- アルゴ警戒: {eg['algo_warning']}")
        lines.append("")

        # 損切り
        sg = p["stoploss_guidance"]
        lines.append("**損切り判断**")
        lines.append(sg["rationale"])
        if sg["worst_case"]:
            lines.append(sg["worst_case"])
        lines.append("")

        # 売り
        sell = p["sell_guidance"]
        if sell["decision_points"]:
            lines.append("**判断ポイント**")
            for dp in sell["decision_points"]:
                date_str = dp.get("date", "")
                lines.append(f"- {date_str}: {dp['event']} → {dp['action']}")
        if sell["sell_points"]:
            lines.append("\n**段階利確**")
            for sp in sell["sell_points"]:
                lines.append(f"- {sp['label']}: {sp['action']}")
        lines.append(f"\n{sell['final_note']}")
        lines.append("")

        # シナリオ
        lines.append("**想定シナリオ**")
        for sc in p["scenarios"]:
            lines.append(f"- **{sc['name']}**（{sc['probability']}）: {sc['description']}")
            lines.append(f"  → {sc['action']}")
        lines.append("")

    return "\n".join(lines)
