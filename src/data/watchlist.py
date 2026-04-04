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


def add_from_screening(r: dict, source: str = "auto"):
    """スクリーニング+分析結果からウォッチリストに追加する。

    根拠、出口戦略、アラート条件を全て保存。

    Args:
        source: "auto"（自動判定で追加）or "manual"（手動ボタンで追加）
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

    added_price = r.get("current_price", 0)

    stocks[code] = {
        "name": r.get("name", code),
        "added_date": today,
        "added_price": added_price,
        "added_source": source,  # "auto" or "manual"
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

        # プラン（凍結。変更不可。評価はこのプランに対して行う）
        "initial_plan": {
            "entry": r.get("entry", 0),
            "target": r.get("target", 0),
            "stop_loss": r.get("stop_loss", 0),
            "reward_pct": r.get("reward_pct", 0),
            "timing": r.get("timing", "WAIT"),
            "whale_phase": r.get("whale_phase", "none"),
        },

        # ストーリー凍結（追加時のシナリオ/トリガーを保存。後から変更しない）
        "frozen_story": {
            "scenario_text": r.get("scenario_text", ""),
            "triggers": r.get("triggers", []),
            "themes": r.get("themes_detected", []),
            "ir_summary": r.get("ir_summary", [])[:5],
        },

        # MFE/MAE（期中最大含み益/含み損。プラン精度の事後分析用）
        "mfe_price": added_price,  # 最高到達価格
        "mae_price": added_price,  # 最安到達価格

        # トレード結果（完了時に記録）
        "trade_result": None,

        # 追跡履歴
        "history": [{
            "date": today,
            "price": added_price,
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

    # MFE/MAE更新
    if current_price > stock.get("mfe_price", 0):
        stock["mfe_price"] = current_price
    if current_price < stock.get("mae_price", float("inf")):
        stock["mae_price"] = current_price

    # トレード完了判定（凍結プランに対して。未完了のみ）
    # 利確: 場中指値で約定（current_priceが目標超なら約定済みとみなす）
    # 損切: 引け値判定（ヒゲ狩り回避。引け値が損切ライン割ったら翌朝成行）
    # バックテスト検証: 場中損切→引け値損切で損切率35%→19%に半減、勝率+7%
    if stock.get("trade_result") is None:
        plan = stock.get("initial_plan", {})
        plan_target = plan.get("target", 0)
        plan_stop = plan.get("stop_loss", 0)

        if plan_target > 0 and current_price >= plan_target:
            # 利確（指値約定。場中にタッチしたと判断）
            _complete_trade(stock, plan_target, "target_hit", today)
        elif plan_stop > 0 and current_price <= plan_stop:
            # 損切（引け値判定。current_priceは引け値）
            # 翌朝成行売りなので実際の約定価格はずれる可能性あり
            _complete_trade(stock, current_price, "stop_hit", today)
        else:
            # 期限切れチェック
            exit_s = stock.get("exit_strategy", {})
            target_date = exit_s.get("target_date")
            if target_date:
                try:
                    td = date.fromisoformat(target_date[:10])
                    if (date.today() - td).days > 30:
                        _complete_trade(stock, current_price, "expired", today)
                except Exception:
                    pass

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


def _complete_trade(stock: dict, exit_price: float, reason: str, exit_date: str):
    """トレード完了を記録する。凍結プランに対する結果。"""
    plan = stock.get("initial_plan", {})
    entry = plan.get("entry", 0) or stock.get("added_price", 0)
    pnl_pct = ((exit_price - entry) / entry * 100) if entry > 0 else 0
    mfe = stock.get("mfe_price", exit_price)
    mae = stock.get("mae_price", exit_price)

    stock["trade_result"] = {
        "exit_price": exit_price,
        "exit_date": exit_date,
        "exit_reason": reason,  # target_hit / stop_hit / expired
        "entry_price": entry,
        "pnl_pct": round(pnl_pct, 1),
        "mfe_price": mfe,
        "mae_price": mae,
        "mfe_pct": round(((mfe - entry) / entry * 100) if entry > 0 else 0, 1),
        "mae_pct": round(((mae - entry) / entry * 100) if entry > 0 else 0, 1),
    }
    stock["status"] = "removed"
    stock["removed_date"] = exit_date
    stock["removed_reason"] = reason


def get_trade_stats() -> dict:
    """全完了トレードの実績を集計する。"""
    wl = load_watchlist()
    stocks = wl.get("stocks", {})
    removed = wl.get("removed", [])

    trades = []
    for code, data in stocks.items():
        tr = data.get("trade_result")
        if tr:
            trades.append({**tr, "code": code, "name": data.get("name", code)})

    if not trades:
        return {"count": 0}

    # 勝ちの定義: 高ボラ銘柄で+15%以上。+5%は誤差レベル
    WIN_THRESHOLD = 15.0
    LOSS_THRESHOLD = 5.0  # -5%以下は負け
    wins = [t for t in trades if t["pnl_pct"] >= WIN_THRESHOLD]
    losses = [t for t in trades if t["pnl_pct"] < -LOSS_THRESHOLD]
    draws = [t for t in trades if -LOSS_THRESHOLD <= t["pnl_pct"] < WIN_THRESHOLD]

    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    win_rate = len(wins) / len(trades) * 100 if trades else 0

    # 期待値 = 勝率×平均利益 + 敗率×平均損失（引き分けは0として計算）
    loss_rate = len(losses) / len(trades) if trades else 0
    expected_value = (win_rate / 100 * avg_win) + (loss_rate * avg_loss)

    # MFE分析: 目標に近づいたが約定しなかったケース
    non_wins = [t for t in trades if t["pnl_pct"] < WIN_THRESHOLD]
    near_miss = [t for t in non_wins if t.get("mfe_pct", 0) >= 20]

    # 総損益
    total_pnl = sum(t["pnl_pct"] for t in trades)

    return {
        "count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "draws": len(draws),
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 1),
        "avg_loss_pct": round(avg_loss, 1),
        "expected_value": round(expected_value, 1),
        "total_pnl": round(total_pnl, 1),
        "near_misses": len(near_miss),
        "trades": trades,
    }


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
            "why_detail": data.get("why_detail", ""),
            "scenario_text": data.get("frozen_story", {}).get("scenario_text", ""),
            "triggers": data.get("frozen_story", {}).get("triggers", []),
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


def refresh_watchlist():
    """ウォッチ中の全銘柄を独立して追跡更新する。

    スキャン対象に関係なく、ウォッチ銘柄の株価を取得して
    プラン乖離チェック・アラート判定・自動除外を行う。
    全自動。手動介在なし。
    """
    from src.data.price import fetch_price

    wl = load_watchlist()
    stocks = wl.get("stocks", {})
    updated = 0
    auto_removed = []

    for code, data in list(stocks.items()):
        if data.get("status") == "removed":
            continue

        try:
            df = fetch_price(code, period_days=60)
            if df is None or df.empty:
                continue

            current_price = float(df["Close"].iloc[-1])
            vol_recent = float(df["Volume"].tail(5).mean())
            vol_avg = float(df["Volume"].tail(20).mean())
            vol_anomaly = vol_recent / vol_avg if vol_avg > 0 else 1.0

            prev_grade = data.get("conviction_grade", "?")
            prev_score = data.get("conviction_score", 0)

            update_daily(code, current_price, prev_grade, prev_score,
                         data.get("initial_plan", {}).get("whale_phase", "none"),
                         vol_anomaly)
            updated += 1

            # 自動除外: criticalなら即除外（手動介在なし）
            dev = data.get("plan_deviation", {})
            if dev.get("should_remove"):
                reason = " / ".join(dev.get("deviations", ["自動除外"]))
                remove_from_watchlist(code, reason=reason)
                auto_removed.append(f"{data.get('name', code)}: {reason}")

        except Exception:
            continue

    return {"updated": updated, "auto_removed": auto_removed}


def update_from_screening(results: list[dict]):
    """スクリーニング結果でウォッチリストを更新する。

    新規追加: 確度A以上 or 大口仕込み検出
    既存更新: スキャン結果に含まれる銘柄のみ（追跡はrefresh_watchlistで別途）
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

        # 除外済みの銘柄は再追加しない（ループ防止）
        if code in stocks and stocks[code].get("status") == "removed":
            continue

        if code in stocks:
            # 既存: 追跡更新
            update_daily(code, price, grade, score, whale_phase, vol)
        elif grade in ("S", "A") and score >= 40:
            # 新規: 自動追加（除外済みでないもののみ）
            add_from_screening(r)
