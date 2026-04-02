"""TDnet（適時開示）データ取得モジュール

IR・適時開示情報を取得する。
決算発表、業績修正、提携、ワラント等の開示を検出。
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def fetch_recent_disclosures(code: str, max_items: int = 20) -> list[dict]:
    """銘柄の直近の適時開示を取得する。

    株探のIRページから取得する。

    Returns:
        [{"date": str, "title": str, "category": str}]
    """
    url = f"https://kabutan.jp/stock/news?code={code}&category=1"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        disclosures = []
        table = soup.find("table", class_="stock_news_table")
        if not table:
            return []

        rows = table.find_all("tr")
        for row in rows[:max_items]:
            cells = row.find_all("td")
            if len(cells) >= 2:
                date_text = cells[0].get_text(strip=True)
                title = cells[1].get_text(strip=True)

                # カテゴリ判定
                category = classify_disclosure(title)
                disclosures.append({
                    "date": date_text,
                    "title": title,
                    "category": category,
                })

        return disclosures
    except Exception:
        return []


def classify_disclosure(title: str) -> str:
    """開示タイトルからカテゴリを判定する。"""
    # ネガティブ（最優先で検出）
    negative = {
        "ワラント": "dilution",
        "新株予約権": "dilution",
        "公募増資": "dilution",
        "売出": "dilution",
        "株式の売出": "dilution",
        "下方修正": "downward_revision",
        "減損": "impairment",
        "特別損失": "special_loss",
        "廃止": "delisting_risk",
    }
    for kw, cat in negative.items():
        if kw in title:
            return cat

    # ポジティブ
    positive = {
        "上方修正": "upward_revision",
        "増配": "dividend_increase",
        "自己株式": "buyback",
        "自社株買": "buyback",
        "業務提携": "alliance",
        "資本提携": "capital_alliance",
        "契約": "contract",
        "受注": "order",
        "承認": "approval",
        "最高益": "record_profit",
        "黒字": "turnaround",
    }
    for kw, cat in positive.items():
        if kw in title:
            return cat

    # 決算系
    if "決算" in title or "業績" in title:
        return "earnings"

    return "other"


def detect_dilution_risk(code: str) -> dict:
    """ワラント・増資等の希薄化リスクを検出する。

    Returns:
        {
            "has_risk": bool,
            "details": 検出された希薄化リスクのリスト,
        }
    """
    disclosures = fetch_recent_disclosures(code)
    dilution_items = [d for d in disclosures if d["category"] in ("dilution",)]

    return {
        "has_risk": len(dilution_items) > 0,
        "details": dilution_items,
        "total_disclosures": len(disclosures),
    }


def detect_positive_catalysts(code: str) -> list[dict]:
    """ポジティブな開示（上方修正、提携、承認等）を検出する。"""
    disclosures = fetch_recent_disclosures(code)
    positive_cats = {"upward_revision", "dividend_increase", "buyback", "alliance",
                     "capital_alliance", "contract", "order", "approval", "record_profit", "turnaround"}

    return [d for d in disclosures if d["category"] in positive_cats]
