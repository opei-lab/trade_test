"""テーマ特需検出モジュール

特定テーマ（AI電力、核融合、光半導体等）に関連する銘柄を検出し、
テーマ内での初動銘柄と出遅れ銘柄を特定する。
"""

import yaml
import pandas as pd
import numpy as np
from pathlib import Path
from src.data.price import fetch_price
from src.data.stocklist import fetch_stocklist

THEMES_PATH = Path(__file__).parent.parent / "config" / "themes.yaml"


def load_themes() -> dict:
    with open(THEMES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_theme_stocks(theme_key: str) -> list[dict]:
    """テーマに関連する銘柄を検出する。

    1. テーマ辞書のknown_codesから既知の銘柄を取得
    2. 全銘柄リストからセクターマッチングで候補を追加

    Returns:
        [{"code": str, "name": str, "match_type": "known" | "sector"}]
    """
    themes = load_themes()
    theme = themes.get(theme_key)
    if not theme:
        return []

    results = []

    # 既知の銘柄
    for code in theme.get("known_codes", []):
        results.append({"code": str(code), "name": "", "match_type": "known"})

    # セクターマッチ
    stocklist = fetch_stocklist()
    related_sectors = theme.get("related_sectors", [])
    for _, row in stocklist.iterrows():
        sector = str(row.get("sector", ""))
        for rs in related_sectors:
            if rs in sector:
                code = str(row["code"])
                if not any(r["code"] == code for r in results):
                    results.append({
                        "code": code,
                        "name": str(row.get("name", "")),
                        "match_type": "sector",
                    })
                break

    return results


def detect_theme_momentum(theme_key: str, period_days: int = 90, top_n: int = 20) -> dict:
    """テーマ内の銘柄の勢いを分析し、初動/出遅れを特定する。

    Returns:
        {
            "theme_name": テーマ名,
            "early_movers": 初動銘柄（既に動いている）,
            "laggards": 出遅れ銘柄（まだ動いていない）,
            "theme_strength": テーマ全体の強さ,
        }
    """
    themes = load_themes()
    theme = themes.get(theme_key, {})
    stocks = find_theme_stocks(theme_key)

    if not stocks:
        return {"theme_name": theme.get("name", ""), "early_movers": [], "laggards": [], "theme_strength": 0}

    # 各銘柄のパフォーマンスを取得
    performances = []
    for stock in stocks[:100]:  # API負荷対策
        try:
            df = fetch_price(stock["code"], period_days=period_days)
            if df.empty or len(df) < 20:
                continue

            close = df["Close"]
            current = float(close.iloc[-1])
            period_start = float(close.iloc[0])
            period_return = (current - period_start) / period_start * 100

            # 直近の勢い（20日リターン）
            recent_start = float(close.iloc[-20]) if len(close) >= 20 else period_start
            recent_return = (current - recent_start) / recent_start * 100

            # 出来高の変化
            volume = df["Volume"]
            vol_recent = float(volume.tail(10).mean())
            vol_baseline = float(volume.iloc[:-10].mean()) if len(volume) > 10 else vol_recent
            vol_change = vol_recent / vol_baseline if vol_baseline > 0 else 1

            performances.append({
                "code": stock["code"],
                "name": stock.get("name", ""),
                "match_type": stock["match_type"],
                "period_return": round(period_return, 1),
                "recent_return": round(recent_return, 1),
                "vol_change": round(vol_change, 2),
                "current_price": current,
            })
        except Exception:
            continue

    if not performances:
        return {"theme_name": theme.get("name", ""), "early_movers": [], "laggards": [], "theme_strength": 0}

    # テーマ全体の強さ = 平均リターン
    avg_return = np.mean([p["period_return"] for p in performances])
    theme_strength = round(avg_return, 1)

    # 初動銘柄: リターンが高い + 出来高増加
    sorted_by_return = sorted(performances, key=lambda x: x["period_return"], reverse=True)
    early_movers = [p for p in sorted_by_return if p["period_return"] > avg_return and p["vol_change"] > 1.2][:top_n]

    # 出遅れ銘柄: リターンが低い or マイナス（まだ動いていない）
    laggards = [p for p in sorted_by_return if p["period_return"] < avg_return * 0.5]
    laggards.reverse()  # リターンが低い順
    laggards = laggards[:top_n]

    return {
        "theme_name": theme.get("name", theme_key),
        "early_movers": early_movers,
        "laggards": laggards,
        "theme_strength": theme_strength,
        "total_stocks": len(performances),
        "avg_return": theme_strength,
    }


def scan_all_themes(period_days: int = 90) -> list[dict]:
    """全テーマをスキャンし、勢いのあるテーマを特定する。

    Returns:
        テーマ強度順にソートされたリスト
    """
    themes = load_themes()
    results = []

    for key in themes:
        try:
            momentum = detect_theme_momentum(key, period_days=period_days, top_n=5)
            if momentum["total_stocks"] > 0:
                results.append({
                    "theme_key": key,
                    "theme_name": momentum["theme_name"],
                    "strength": momentum["theme_strength"],
                    "total_stocks": momentum["total_stocks"],
                    "early_movers_count": len(momentum["early_movers"]),
                    "laggards_count": len(momentum["laggards"]),
                })
        except Exception:
            continue

    results.sort(key=lambda x: x["strength"], reverse=True)
    return results
