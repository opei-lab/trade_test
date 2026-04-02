"""ニュース・IR分析モジュール

ニュースやIRテキストから事実を抽出し、
テーマ検出やステージ変化の判定に使う。
"""

import requests
from bs4 import BeautifulSoup
from src.llm.client import analyze_text, is_available, extract_themes_from_text


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def fetch_kabutan_news(code: str, max_items: int = 5) -> list[dict]:
    """株探からニュースを取得する。

    Returns:
        [{"title": str, "date": str, "url": str}]
    """
    url = f"https://kabutan.jp/stock/news?code={code}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        news = []
        table = soup.find("table", class_="stock_news_table")
        if not table:
            return []

        rows = table.find_all("tr")
        for row in rows[:max_items]:
            cells = row.find_all("td")
            if len(cells) >= 2:
                date_text = cells[0].get_text(strip=True)
                link = cells[1].find("a")
                if link:
                    title = link.get_text(strip=True)
                    href = link.get("href", "")
                    if href and not href.startswith("http"):
                        href = "https://kabutan.jp" + href
                    news.append({"title": title, "date": date_text, "url": href})

        return news
    except Exception:
        return []


def analyze_news_for_stage_change(code: str) -> dict:
    """ニュースからステージ変化を検出する。

    LLMが利用可能な場合はニュースタイトルを分析。
    不可の場合はキーワードマッチングで簡易判定。

    Returns:
        {
            "news": ニュースリスト,
            "stage_signals": 検出されたシグナル,
            "themes_detected": 検出されたテーマ,
        }
    """
    news = fetch_kabutan_news(code)
    if not news:
        return {"news": [], "stage_signals": [], "themes_detected": []}

    titles = " / ".join([n["title"] for n in news])

    # LLMが使える場合
    if is_available():
        analysis = analyze_text(
            titles,
            "以下のニュースタイトルから、企業のステージ変化（売上急増、黒字転換、大型提携、承認取得、増資、ワラント等）を示す事実を抽出してください。事実のみを箇条書きで。",
        )
        themes = extract_themes_from_text(titles)

        signals = []
        if analysis:
            for line in analysis.split("\n"):
                line = line.strip().lstrip("- ・")
                if line:
                    signals.append(line)

        return {"news": news, "stage_signals": signals, "themes_detected": themes}

    # LLMなし: キーワードマッチング
    positive_keywords = ["黒字", "上方修正", "増収増益", "最高益", "提携", "契約", "承認", "受注", "採用"]
    negative_keywords = ["下方修正", "赤字", "減損", "ワラント", "増資", "希薄化", "廃止"]

    signals = []
    for n in news:
        title = n["title"]
        for kw in positive_keywords:
            if kw in title:
                signals.append(f"[+] {title}")
                break
        for kw in negative_keywords:
            if kw in title:
                signals.append(f"[-] {title}")
                break

    return {"news": news, "stage_signals": signals, "themes_detected": []}
