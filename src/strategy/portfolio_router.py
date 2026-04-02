"""ポートフォリオルーティング

確度ベースで投資戦略を統合管理する。

原則:
- 確度が全て。出口が見える = 確度が高い
- 低位株に集中。銘柄を分けすぎない
- リスクヘッジを最小にする代わりに確度を上げる
- リターンがリスクより明らかに大きい非対称な状態を作る
- 恩株化: 利益で元本回収後、残りをタダ乗り
"""

from datetime import date, timedelta
import math


def classify_strategy(result: dict) -> dict:
    """銘柄を最適な戦略に分類する。

    Returns:
        {
            "strategy": "long_term" | "swing" | "watch",
            "reason": 分類理由,
            "expected_value": 期待値,
            "confidence_adjusted_ev": 確度調整済み期待値,
        }
    """
    conv_score = result.get("conviction", {}).get("conviction_score", 0)
    reward_pct = result.get("reward_pct", 0)
    target_days = result.get("expectation", {}).get("target_days")
    whale_phase = result.get("whale_phase", "none")
    stage_score = result.get("stage_score", 0)
    staged_targets = result.get("staged_targets", [])

    current_price = result.get("current_price", 0)

    # 期待値 = リターン × 確度 / 100
    ev = reward_pct * conv_score / 100

    # 年率換算の期待値（短期ほど回転効率が良い）
    if target_days and target_days > 0:
        annualized_ev = ev * (365 / target_days)
    else:
        annualized_ev = ev

    # 出口の可視性
    timeline_clarity = result.get("expectation", {}).get("timeline_clarity", 0) if isinstance(result.get("expectation"), dict) else 0
    has_prior_evidence = stage_score >= 10 or len(result.get("positive_catalysts", [])) > 0

    # 低位株ボーナス（元手が少なくても枚数を持てる）
    low_price_bonus = current_price > 0 and current_price < 1000

    # 分類ロジック
    # 短期/中期の「見える」トレード: 出口が明確 + 確度が一定以上
    # → これが最優先（出口が見える = リスク管理できる = 確度が高い）
    is_swing_candidate = (
        conv_score >= 30
        and reward_pct >= 30
        and timeline_clarity >= 40
    )

    # 長期100倍候補: ステージ変化がある + パイプライン段階目標がある + 大口が仕込み中
    # → ただし「前半だけ取る」戦略も提示する
    is_long_candidate = (
        (stage_score >= 20 or len(staged_targets) >= 3)
        and whale_phase in ("accumulating", "holding")
    )

    if is_swing_candidate and is_long_candidate:
        # 両方 → 出口が見える前半を短期で取り、後半は任意
        strategy = "swing"
        reason = f"出口が見える前半を短期で取る（確度{conv_score}pt、{target_days or '?'}日）。後半の長期保有は結果を見て判断"
    elif is_swing_candidate:
        strategy = "swing"
        if has_prior_evidence:
            reason = f"前座の実績+出口明確（確度{conv_score}pt、{target_days or '?'}日で+{reward_pct:.0f}%）。リスク管理しやすい"
        else:
            reason = f"出口が見える（確度{conv_score}pt、{target_days or '?'}日で+{reward_pct:.0f}%）"
    elif is_long_candidate:
        strategy = "long_term"
        reason = f"段階的な上昇ポテンシャル（{len(staged_targets)}段階）。ただし後半は出口が不明確"
    elif timeline_clarity >= 60:
        strategy = "swing"
        reason = f"出口が見える（{target_days or '?'}日）が確度やや不足。条件改善を待つ"
    else:
        strategy = "watch"
        reason = "出口が見えない or 確度不足。監視継続"

    return {
        "strategy": strategy,
        "reason": reason,
        "expected_value": round(ev, 1),
        "annualized_ev": round(annualized_ev, 1),
        "confidence_adjusted_ev": round(ev, 1),
    }


def plan_relay_route(swing_candidates: list[dict], capital: float = 100) -> dict:
    """渡り鳥の最適経路を算出する。

    渡り鳥 = 全資金を1銘柄に集中 → 2倍 → 全額を次の1銘柄に → 2倍 → ...
    分散しない。1銘柄ずつ直列に乗り換える。
    資金が遊ばないように乗り換えタイミングを最短化する。

    Returns:
        {
            "route": [直列の銘柄リスト],
            "compound_multiplier": 複利倍率,
            "final_capital": 最終資金,
        }
    """
    if not swing_candidates:
        return {"route": [], "total_expected_return": 0, "compound_multiplier": 1,
                "initial_capital": capital, "final_capital": capital}

    today = date.today()

    # 各銘柄のイベント日でソート（直近イベントから順に取る）
    candidates = []
    for s in swing_candidates:
        exp = s.get("expectation", {})
        target_days = exp.get("target_days")
        target_date = exp.get("target_date")
        conv_score = s.get("conviction", {}).get("conviction_score", 0) if isinstance(s.get("conviction"), dict) else 0
        reward_pct = s.get("reward_pct", 0)
        ev = reward_pct * conv_score / 100

        if not target_days:
            target_days = 90

        candidates.append({
            "code": s.get("code", ""),
            "name": s.get("name", ""),
            "current_price": s.get("current_price", 0),
            "entry_price": s.get("entry", 0),
            "target_price": s.get("target", 0),
            "stop_loss": s.get("stop_loss", 0),
            "reward_pct": reward_pct,
            "conviction_score": conv_score,
            "conviction_grade": s.get("conviction_grade", "?"),
            "ev": round(ev, 1),
            "target_days": target_days,
            "target_date": target_date,
            "exit_date": (today + timedelta(days=target_days)),
            "timeline_clarity": exp.get("timeline_clarity", 0),
            "milestones": exp.get("milestones", []),
        })

    # ソート: イベント日が近い順（出口が早く見える順）
    # ただし出口が見えないものは後回し
    def route_sort_key(c):
        clarity = c.get("timeline_clarity", 0)
        days = c.get("target_days", 999)
        ev = c.get("ev", 0)
        return (-clarity, days, -ev)  # 明確度高い → 日数短い → EV高い

    candidates.sort(key=route_sort_key)

    # 直列ルーティング: 1銘柄ずつ全資金投入
    route = []
    running_capital = capital
    current_exit = today

    for c in candidates:
        entry_date = max(current_exit, today)
        exit_date = entry_date + timedelta(days=c["target_days"])

        c["entry_date"] = entry_date.isoformat()
        c["exit_date"] = exit_date.isoformat()
        c["capital_in"] = round(running_capital, 1)

        # 全資金を集中投入
        expected_gain = running_capital * c["ev"] / 100
        c["expected_gain"] = round(expected_gain, 1)
        running_capital += expected_gain
        c["capital_out"] = round(running_capital, 1)

        route.append(c)
        current_exit = exit_date

    compound = running_capital / capital if capital > 0 else 1

    return {
        "route": route,
        "total_expected_return": round((compound - 1) * 100, 1),
        "compound_multiplier": round(compound, 2),
        "initial_capital": capital,
        "final_capital": round(running_capital, 1),
        "total_days": (current_exit - today).days,
    }


def format_portfolio_plan(
    long_term: list[dict],
    swing_route: dict,
    capital: float = 100,
) -> str:
    """統合ポートフォリオプランをMarkdownで出力する。"""
    lines = ["## 統合投資プラン\n"]
    lines.append(f"投資資金: ¥{capital}万\n")

    # 渡り鳥ルート（全資金を1銘柄ずつ直列に集中投入）
    route = swing_route.get("route", [])
    if route:
        total_days = swing_route.get("total_days", 0)
        lines.append(f"### 渡り鳥プラン（全資金集中 → 直列乗り換え）")
        lines.append(f"¥{swing_route['initial_capital']}万 → {len(route)}銘柄直列 → **¥{swing_route['final_capital']}万（{swing_route['compound_multiplier']}倍）** 約{total_days}日\n")

        for i, r in enumerate(route):
            milestone_str = ""
            if r.get("milestones"):
                ms = r["milestones"][0]
                milestone_str = f" ← {ms.get('event', '')}"

            lines.append(f"**{i+1}. {r['name']}({r['code']}) [{r.get('conviction_grade', '?')}]**{milestone_str}")
            lines.append(f"- 全資金¥{r['capital_in']}万を投入 → ¥{r['entry_price']:,}で買い → ¥{r['target_price']:,}で売り（+{r['reward_pct']:.0f}%）")
            lines.append(f"- 期間: {r['target_days']}日（{r.get('entry_date', '')}〜{r.get('exit_date', '')}）")
            lines.append(f"- 期待値: +{r['ev']}% → ¥{r['capital_in']}万が¥{r['capital_out']}万に")
            if r.get("stop_loss"):
                loss_amt = round(r["capital_in"] * (r["entry_price"] - r["stop_loss"]) / r["entry_price"])
                lines.append(f"- 損切り: ¥{r['stop_loss']:,}（資金-¥{loss_amt}万）")
            if i < len(route) - 1:
                lines.append(f"- → 売却後、**全額を{route[i+1]['name']}に移動**")
            lines.append("")

    # 長期保有（恩株化戦略）
    if long_term:
        lines.append(f"### 長期保有プラン（恩株化視野）")
        lines.append("前半（出口が見える期間）で元本を回収し、残りを恩株として長期保有。\n")
        for s in long_term:
            name = s.get("name", s.get("code", ""))
            code = s.get("code", "")
            current = s.get("current_price", 0)
            staged = s.get("staged_targets", [])
            lines.append(f"\n**{name}({code})** 現在値¥{current:,.0f}")

            # 前半（見える）と後半（見えない）を分離
            visible_stages = []
            invisible_stages = []
            for st in staged:
                # 確率30%以上 or 1年以内のイベントと紐付く = 見える
                if st.get("probability", 0) >= 25:
                    visible_stages.append(st)
                else:
                    invisible_stages.append(st)

            if visible_stages:
                lines.append("前半（出口が見える、ここで元本回収）:")
                for st in visible_stages:
                    lines.append(f"- {st.get('step', '')}: ¥{st.get('target_price', 0):,}（{st.get('multiplier', 0)}倍、確率{st.get('probability', 0)}%）")
                # 恩株化ライン
                if visible_stages:
                    first_target = visible_stages[0].get("target_price", 0)
                    if first_target > current and current > 0:
                        shares_to_sell_pct = round(current / first_target * 100)
                        lines.append(f"→ ¥{first_target:,}到達時に{shares_to_sell_pct}%売却で元本回収。残り{100-shares_to_sell_pct}%が恩株")

            if invisible_stages:
                lines.append("後半（出口見えない、恩株で放置）:")
                for st in invisible_stages:
                    lines.append(f"- {st.get('step', '')}: ¥{st.get('target_price', 0):,}（{st.get('multiplier', 0)}倍、確率{st.get('probability', 0)}%）")

            wp = s.get("whale_plan", {})
            if wp.get("detected"):
                lines.append(f"大口: {wp.get('description', '')[:100]}")

    # 投資方針の注記
    lines.append("\n---")
    lines.append("**方針: 低位株に集中。確度が高い1-2銘柄に資金を集中投入。恩株化で元本リスクをゼロにしてから長期保有。**")

    return "\n".join(lines)
