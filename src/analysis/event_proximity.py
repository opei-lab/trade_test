"""イベント接近検出モジュール

業界ごとの「価値確定イベント」が近づいているかを検出する。
イベント前は期待で動き、イベント後は事実で動く。
どちらのタイミングにいるかで戦略が変わる。
"""

import yaml
from pathlib import Path
from datetime import datetime, date

CONFIG_PATH = Path(__file__).parent.parent / "config" / "event_calendar.yaml"


def load_event_calendar() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_upcoming_events(
    sector: str,
    industry: str,
    today: date = None,
    lookahead_days: int = 60,
) -> list[dict]:
    """指定セクターの直近イベントを検出する。

    Args:
        sector: yfinanceのsector
        industry: yfinanceのindustry
        today: 基準日（デフォルト: 今日）
        lookahead_days: 何日先までのイベントを検出するか

    Returns:
        [{
            "event_name": イベント名,
            "estimated_date": 推定日付,
            "days_until": あと何日,
            "impact": インパクトの説明,
            "watch_window": 監視期間,
            "phase": "pre_event" | "during" | "post_event",
        }]
    """
    if today is None:
        today = date.today()

    calendar = load_event_calendar()
    combined = f"{sector} {industry}".lower()
    results = []

    for category_key, category in calendar.items():
        # セクターマッチ
        match_sectors = category.get("match_sectors", [])
        match_keywords = category.get("match_keywords", [])
        matched = False

        for ms in match_sectors:
            if ms.lower() in combined:
                matched = True
                break

        if not matched:
            for mk in match_keywords:
                if mk.lower() in combined:
                    matched = True
                    break

        # generalは全銘柄に適用
        if category_key == "general":
            matched = True

        if not matched:
            continue

        events = category.get("events", [])
        for event in events:
            months = event.get("months", [])
            month = event.get("month")
            if month and not months:
                months = [month]

            if not months:
                continue  # 月が不定のイベント（銘柄固有）はスキップ

            for m in months:
                if m is None:
                    continue

                # タイミング推定
                timing = event.get("timing", "mid")
                if timing == "early":
                    day = 7
                elif timing == "late":
                    day = 25
                else:
                    day = 15

                # 今年と来年の候補を生成
                for year in [today.year, today.year + 1]:
                    try:
                        event_date = date(year, m, min(day, 28))
                    except ValueError:
                        continue

                    days_until = (event_date - today).days
                    watch_window = event.get("watch_window_days", 7)

                    # lookahead_days以内、かつ過ぎていてもwatch_window以内なら含める
                    if -watch_window <= days_until <= lookahead_days:
                        if days_until < -watch_window:
                            continue

                        if days_until < 0:
                            phase = "post_event"
                        elif days_until <= watch_window:
                            phase = "during"
                        else:
                            phase = "pre_event"

                        results.append({
                            "event_name": event["name"],
                            "category": category_key,
                            "estimated_date": event_date.isoformat(),
                            "days_until": days_until,
                            "impact": event.get("impact", ""),
                            "watch_window": watch_window,
                            "phase": phase,
                        })

    # 日付順にソート
    results.sort(key=lambda x: abs(x["days_until"]))
    return results


def calc_event_proximity_score(events: list[dict]) -> dict:
    """イベント接近度をスコア化する。

    Returns:
        {
            "score": 0-100（高いほどイベントが近い）,
            "nearest_event": 最も近いイベント,
            "description": 説明,
        }
    """
    if not events:
        return {"score": 0, "nearest_event": None, "description": "直近のイベントなし"}

    nearest = events[0]
    days = abs(nearest["days_until"])

    # イベント中 or 直後が最高スコア
    if nearest["phase"] == "post_event":
        score = 90  # イベント直後 = 事実が出た直後
    elif nearest["phase"] == "during":
        score = 80  # イベント期間中
    elif days <= 7:
        score = 70  # 1週間以内
    elif days <= 14:
        score = 55
    elif days <= 30:
        score = 40
    elif days <= 60:
        score = 25
    else:
        score = 10

    desc_parts = []
    for e in events[:3]:
        if e["days_until"] < 0:
            desc_parts.append(f"{e['event_name']}（{abs(e['days_until'])}日前に終了）")
        elif e["days_until"] == 0:
            desc_parts.append(f"{e['event_name']}（本日）")
        else:
            desc_parts.append(f"{e['event_name']}（あと{e['days_until']}日）")

    return {
        "score": score,
        "nearest_event": nearest,
        "events_count": len(events),
        "description": " / ".join(desc_parts),
    }
