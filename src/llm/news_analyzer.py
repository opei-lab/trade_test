"""ニュース・IR分析モジュール

ニュースやIRテキストから事実を抽出し、
テーマ検出やステージ変化の判定に使う。
"""

import time

import requests
from bs4 import BeautifulSoup
from src.llm.client import analyze_text, is_available, extract_themes_from_text


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

_FALLBACK_NOISE = [
    "本日の【", "均衡表", "ゴールデンクロス", "デッドクロス",
    "上抜け／下抜け", "3役好転", "3役逆転", "銘柄　",
    "GC＝", "DC＝", "好転＝", "逆転＝",
]


def _load_noise_patterns() -> list[str]:
    """impact_keywords.yamlからノイズパターンを読み込む。"""
    try:
        import yaml
        from pathlib import Path
        path = Path(__file__).parent.parent / "config" / "impact_keywords.yaml"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            return config.get("noise_patterns", _FALLBACK_NOISE)
    except Exception:
        pass
    return _FALLBACK_NOISE


def fetch_news_body(url: str, max_chars: int = 500) -> str:
    """kabutanのニュース詳細ページから本文を取得する。"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        body = (
            soup.find("div", id="newsDetail")
            or soup.find("div", class_="newsDetail")
            or soup.find("div", class_="body")
            or soup.find("article")
        )
        if not body:
            return ""

        text = body.get_text(separator=" ", strip=True)
        return text[:max_chars]
    except Exception:
        return ""


def fetch_kabutan_news(code: str, max_items: int = 5, max_pages: int = 2, fetch_body: bool = False) -> list[dict]:
    """株探からニュースを取得する。

    複数ページを取得してノイズ除外後、max_items件返す。
    fetch_body=Trueの場合、先頭3件のニュース本文も取得する。

    Returns:
        [{"title": str, "date": str, "url": str, "body"?: str}]
    """
    all_news = []
    for page in range(1, max_pages + 1):
        url = f"https://kabutan.jp/stock/news?code={code}" + (f"&page={page}" if page > 1 else "")
        news_from_page = _fetch_kabutan_page(url, max_items - len(all_news))
        all_news.extend(news_from_page)
        if len(all_news) >= max_items:
            break
    result = all_news[:max_items]

    if fetch_body:
        for i, item in enumerate(result[:3]):
            if item.get("url"):
                item["body"] = fetch_news_body(item["url"])
                if i < 2:
                    time.sleep(0.3)

    return result


def _fetch_kabutan_page(url: str, remaining: int) -> list[dict]:
    """kabutanの1ページ分を取得する。"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        news = []
        # kabutanのニューステーブルはクラス名が変わることがある
        table = (soup.find("table", class_="stock_news_table")
                 or soup.find("table", class_="s_news_list"))
        if not table:
            # テーブルが見つからない場合、ニュースリンクから取得
            links = soup.find_all("a", href=True)
            for a in links:
                href = a.get("href", "")
                if "/news/" in href and a.get_text(strip=True):
                    title = a.get_text(strip=True)
                    if len(title) > 10:  # 短すぎるリンクは除外
                        if not href.startswith("http"):
                            href = "https://kabutan.jp" + href
                        news.append({"title": title, "date": "", "url": href})
                        if len(news) >= remaining:
                            break
            return news

        # ノイズフィルタ（config/impact_keywords.yamlから読み込み、フォールバックあり）
        noise_patterns = _load_noise_patterns()

        rows = table.find_all("tr")
        for row in rows:
            if len(news) >= remaining:
                break
            cells = row.find_all("td")
            if len(cells) >= 3:
                # 3セル: 日付, カテゴリ, タイトル（kabutanの現行構造）
                date_text = cells[0].get_text(strip=True)
                link = cells[2].find("a")
                if link:
                    title = link.get_text(strip=True)
                    # ノイズ除外
                    if any(noise in title for noise in noise_patterns):
                        continue
                    href = link.get("href", "")
                    if href and not href.startswith("http"):
                        href = "https://kabutan.jp" + href
                    news.append({"title": title, "date": date_text, "url": href})
            elif len(cells) >= 2:
                date_text = cells[0].get_text(strip=True)
                link = cells[1].find("a")
                if link:
                    title = link.get_text(strip=True)
                    if any(noise in title for noise in noise_patterns):
                        continue
                    href = link.get("href", "")
                    if href and not href.startswith("http"):
                        href = "https://kabutan.jp" + href
                    news.append({"title": title, "date": date_text, "url": href})

        return news
    except Exception:
        return []


def analyze_news_for_stage_change(code: str, news: list[dict] | None = None) -> dict:
    """ニュースからステージ変化を検出する。

    LLMが利用可能な場合はニュースタイトルを分析。
    不可の場合はキーワードマッチングで簡易判定。

    Args:
        news: 事前取得済みのニュースリスト。Noneなら内部で取得する。
    """
    if news is None:
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
