"""ウォッチリスト管理

screener → analysis → watch → monitor → alert → optimize のサイクル。

各銘柄は以下を保持:
- なぜ上がるか（根拠）
- 出口戦略（いつ・いくらで売るか）
- プランとの乖離チェック（想定通りか）
- アラート条件（買い/売りシグナル）
"""

import json
from pathlib import Path
from datetime import date, datetime

WATCHLIST_FILE = Path(__file__).parent.parent.parent / "data" / "watchlist.json"


def load_watchlist() -> dict:
    if not WATCHLIST_FILE.exists():
        return {"stocks": {}, "updated": None, "removed": []}
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"stocks": {}, "updated": None, "removed": []}


def save_watchlist(data: dict):
    WATCHLIST_FILE.parent.mkdir(exist_ok=True)
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def add_from_screening(r: dict):
    """スクリーニング+分析結果からウォッチリストに追加する。

    根拠、出口戦略、アラート条件を全て保存。
    """
    code = r.get("code", "")
    if not code:
        return

    today = date.today().isoformat()
    wl = load_watchlist()
    stocks = wl.get("stocks", {})

    # 出口戦略の構築
    exp = r.get("expectation", {}) if isinstance(r.get("expectation"), dict) else {}
    milestones = exp.get("milestones", [])
    events = r.get("upcoming_events", [])

    exit_strategy = {
        "target": r.get("target", 0),
        "stop_loss": r.get("stop_loss", 0),
        "target_date": exp.get("target_date"),
        "target_days": exp.get("target_days"),
        "exit_event": milestones[0].get("event", "") if milestones else (events[0].get("event_name", "") if events else ""),
        "exit_trigger": "イベント結果 + 目標価格到達",
    }

    # 根拠
    conv = r.get("conviction", {}) if isinstance(r.get("conviction"), dict) else {}
    passed = conv.get("passed", [])
    why_list = [p.get("name", "") for p in passed if p.get("weight", 0) >= 4]

    # アラート条件
    alerts = {
        "buy_below": r.get("entry", 0),       # この価格以下で買い
        "sell_above": r.get("target", 0),      # この価格以上で売り
        "stop_at": r.get("stop_loss", 0),      # この価格で損切り
        "volume_surge": True,                   # 出来高急増で通知
    }

    stocks[code] = {
        "name": r.get("name", code),
        "added_date": today,
        "added_price": r.get("current_price", 0),
        "status": "watching",  # watching → attention → action → holding → removed
        "conviction_grade": r.get("conviction_grade", "?"),
        "conviction_score": conv.get("conviction_score", 0),

        # なぜ上がるか
        "why": why_list,
        "why_detail": r.get("whale_plan_description", ""),
        "stage_summary": r.get("stage_summary", ""),

        # 出口戦略
        "exit_strategy": exit_strategy,

        # アラート条件
        "alerts": alerts,

        # プラン（初期状態を記録。乖離チェック用）
        "initial_plan": {
            "entry": r.get("entry", 0),
            "target": r.get("target", 0),
            "stop_loss": r.get("stop_loss", 0),
            "reward_pct": r.get("reward_pct", 0),
            "timing": r.get("timing", "WAIT"),
            "whale_phase": r.get("whale_phase", "none"),
        },

        # 追跡履歴
        "history": [{
            "date": today,
            "price": r.get("current_price", 0),
            "grade": r.get("conviction_grade", "?"),
            "score": conv.get("conviction_score", 0),
            "note": "ウォッチ追加",
        }],
    }

    wl["stocks"] = stocks
    wl["updated"] = datetime.now().isoformat()
    save_watchlist(wl)


def update_daily(code: str, current_price: float, conviction_grade: str, conviction_score: float,
                 whale_phase: str = "none", volume_anomaly: float = 1.0):
    """日次の追跡更新。プランとの乖離をチェックする。"""
    wl = load_watchlist()
    stocks = wl.get("stocks", {})
    if code not in stocks:
        return

    today = date.today().isoformat()
    stock = stocks[code]
    plan = stock.get("initial_plan", {})
    alerts = stock.get("alerts", {})

    # 履歴追加
    history = stock.get("history", [])
    if not history or history[-1].get("date") != today:
        note = ""

        # アラートチェック
        triggered = []
        if current_price <= alerts.get("buy_below", 0) and stock["status"] in ("watching", "attention"):
            triggered.append("買いライン到達")
        if current_price >= alerts.get("sell_above", 0):
            triggered.append("売り目標到達")
        if current_price <= alerts.get("stop_at", 0):
            triggered.append("損切りライン到達")
        if volume_anomaly >= 1.5:
            triggered.append("出来高変化検出 → まだ買わない。ちょい上げ後の押し目を待つ")
        if volume_anomaly >= 2.8:
            triggered.append("出来高急増 → 既に動き始めた。押し目（-5〜-15%）に指値を入れて待つ")

        if triggered:
            note = "ALERT: " + " / ".join(triggered)

        # 確度の変化
        prev_score = history[-1].get("score", 0) if history else 0
        if conviction_score > prev_score + 5:
            note += " 確度上昇"
        elif conviction_score < prev_score - 10:
            note += " 確度低下"

        history.append({
            "date": today,
            "price": current_price,
            "grade": conviction_grade,
            "score": conviction_score,
            "note": note.strip(),
        })

        # ステータス自動更新
        if "買いライン到達" in note or "出来高急増" in note:
            if stock["status"] in ("watching", "attention"):
                stock["status"] = "action"
        elif "売り目標到達" in note or "損切りライン到達" in note:
            stock["status"] = "holding"  # 判断が必要

        # 確度上昇トレンドチェック
        if len(history) >= 3:
            recent_scores = [h["score"] for h in history[-3:]]
            if all(recent_scores[i] <= recent_scores[i+1] for i in range(len(recent_scores)-1)):
                if stock["status"] == "watching":
                    stock["status"] = "attention"

    # プランとの乖離チェック（履歴2件以上で初めてチェック。追加直後は何も判定しない）
    if len(history) >= 2:
        stock["plan_deviation"] = check_plan_deviation(stock, current_price, whale_phase)
    else:
        stock["plan_deviation"] = {"deviations": [], "severity": "ok", "should_remove": False}

    stock["history"] = history
    stocks[code] = stock
    wl["stocks"] = stocks
    wl["updated"] = datetime.now().isoformat()
    save_watchlist(wl)


def check_plan_deviation(stock: dict, current_price: float, whale_phase: str) -> dict:
    """プランとの乖離をチェックする。

    プランと現実が大きく異なる場合は警告。
    外している可能性がある場合はcritical → ウォッチ除外候補。
    """
    plan = stock.get("initial_plan", {})
    deviations = []
    severity = "ok"

    plan_entry = plan.get("entry", 0)
    plan_target = plan.get("target", 0)
    plan_stop = plan.get("stop_loss", 0)

    # 1. 損切りラインを割った
    if plan_stop > 0 and current_price < plan_stop:
        deviations.append(f"損切りライン¥{plan_stop:,}を割った（現在¥{current_price:,.0f}）")
        severity = "critical"

    # 2. 大口が売りに転じた（仕込み→売り抜け）
    if plan.get("whale_phase") in ("accumulating", "holding") and whale_phase in ("distributing", "exited"):
        # 理由の推定
        if current_price > plan.get("entry", 0) * 1.3:
            deviations.append("大口が利確に転じた（目標圏に近い可能性。こちらも利確検討）")
            severity = "warning"
        elif current_price < plan.get("entry", 0) * 0.9:
            deviations.append("大口が損切りで撤退した可能性（想定が外れた。即撤退推奨）")
            severity = "critical"
        else:
            deviations.append("大口の動きが鈍化。仕込み→様子見に転じた可能性（監視継続）")
            severity = "warning"

    # 3. 確度が大幅に低下
    history = stock.get("history", [])
    if len(history) >= 2:
        initial_score = history[0].get("score", 0)
        current_score = history[-1].get("score", 0)
        if initial_score > 0 and current_score < initial_score * 0.5:
            deviations.append(f"確度が半減（{initial_score}→{current_score}）")
            severity = "warning" if severity == "ok" else severity

    # 4. 想定期間を過ぎている
    exit_strategy = stock.get("exit_strategy", {})
    target_date = exit_strategy.get("target_date")
    if target_date:
        from datetime import date as _date
        try:
            td = _date.fromisoformat(target_date)
            days_overdue = (_date.today() - td).days
            if days_overdue > 14:
                deviations.append(f"想定期限を{days_overdue}日超過。プランの前提が崩れている可能性")
                severity = "warning" if severity == "ok" else severity
            elif days_overdue > 0:
                deviations.append(f"想定期限を{days_overdue}日経過。状況を再確認")
        except Exception:
            pass

    # 5. 株価がエントリー時と真逆の方向に動いている
    added_price = stock.get("added_price", 0)
    if added_price > 0 and plan_target > plan_entry:
        # 上がる想定なのに下がっている
        change_pct = (current_price - added_price) / added_price * 100
        if change_pct < -15:
            deviations.append(f"追加時¥{added_price:,.0f}から{change_pct:.0f}%下落。プランと逆方向")
            severity = "warning" if severity == "ok" else severity

    # 6. 出来高が完全に枯れた（準備完了後に誰も来なかった）
    if len(history) >= 5:
        recent_notes = [h.get("note", "") for h in history[-5:]]
        if not any("ALERT" in n or "変化" in n or "上昇" in n for n in recent_notes):
            deviations.append("5回連続でシグナルなし。動きがない")
            if severity == "ok":
                severity = "stale"

    return {
        "deviations": deviations,
        "severity": severity,
        "should_remove": severity == "critical",
    }


def get_watchlist_summary() -> list[dict]:
    """ウォッチリストのサマリーを返す。"""
    wl = load_watchlist()
    stocks = wl.get("stocks", {})

    summary = []
    for code, data in stocks.items():
        if data.get("status") == "removed":
            continue

        history = data.get("history", [])
        latest = history[-1] if history else {}
        exit_s = data.get("exit_strategy", {})
        dev = data.get("plan_deviation", {})

        summary.append({
            "code": code,
            "name": data.get("name", code),
            "status": data.get("status", "watching"),
            "grade": data.get("conviction_grade", "?"),
            "score": data.get("conviction_score", 0),
            "added_price": data.get("added_price", 0),
            "latest_price": latest.get("price", 0),
            "target": exit_s.get("target", 0),
            "stop_loss": exit_s.get("stop_loss", 0),
            "exit_event": exit_s.get("exit_event", ""),
            "target_date": exit_s.get("target_date", ""),
            "why": data.get("why", []),
            "latest_note": latest.get("note", ""),
            "deviation_severity": dev.get("severity", "ok"),
            "deviations": dev.get("deviations", []),
            "history_count": len(history),
        })

    status_order = {"action": 0, "attention": 1, "watching": 2, "holding": 3}
    summary.sort(key=lambda x: (status_order.get(x["status"], 9), -x["score"]))
    return summary


def remove_from_watchlist(code: str, reason: str = ""):
    """ウォッチリストから外す（履歴は残す）。"""
    wl = load_watchlist()
    stocks = wl.get("stocks", {})
    if code in stocks:
        stocks[code]["status"] = "removed"
        stocks[code]["removed_date"] = date.today().isoformat()
        stocks[code]["removed_reason"] = reason

        # 除外履歴に追加（最適化の学習データ用）
        removed = wl.get("removed", [])
        removed.append({
            "code": code,
            "name": stocks[code].get("name", ""),
            "reason": reason,
            "date": date.today().isoformat(),
            "final_grade": stocks[code].get("conviction_grade", ""),
            "added_price": stocks[code].get("added_price", 0),
            "removed_price": stocks[code].get("history", [{}])[-1].get("price", 0),
        })
        wl["removed"] = removed

    wl["stocks"] = stocks
    save_watchlist(wl)


def update_from_screening(results: list[dict]):
    """スクリーニング結果でウォッチリストを更新する。

    新規追加: 確度A以上 or 大口仕込み検出
    既存更新: 日次の追跡データを更新
    """
    wl = load_watchlist()
    stocks = wl.get("stocks", {})

    for r in results:
        code = r.get("code", "")
        if not code:
            continue

        grade = r.get("conviction_grade", "D")
        conv = r.get("conviction", {}) if isinstance(r.get("conviction"), dict) else {}
        score = conv.get("conviction_score", 0)
        whale_phase = r.get("whale_phase", "none")
        vol = r.get("volume_anomaly", 1)
        price = r.get("current_price", 0)

        if code in stocks and stocks[code].get("status") != "removed":
            # 既存: 追跡更新
            update_daily(code, price, grade, score, whale_phase, vol)
        elif grade in ("S", "A") or whale_phase in ("accumulating", "holding"):
            # 新規: 自動追加
            add_from_screening(r)
