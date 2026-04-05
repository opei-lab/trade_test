"""Google News RSS + PubMed 監視モジュール

マッピングテーブルのキーワードで英語ニュースと医学論文を監視する。
FDA承認やSEC提出よりカバレッジが広い（学会発表、メディア報道等）。

データソース:
  - Google News RSS (無料、無制限)
  - PubMed E-utilities (無料、APIキー推奨)
"""

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_MAP_FILE = _CONFIG_DIR / "overseas_map.yaml"

_HEADERS = {"User-Agent": "Mozilla/5.0 (StockScreener/1.0; research)"}
_TIMEOUT = 15


def _load_company_map() -> dict:
    if not _MAP_FILE.exists():
        return {}
    with open(_MAP_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def fetch_google_news(query: str, max_items: int = 10) -> list[dict]:
    """Google News RSSで検索"""
    encoded = quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        items = []
        for item in root.iter("item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")
            source = item.findtext("source", "")

            items.append({
                "title": title.strip(),
                "link": link.strip(),
                "date": pub_date.strip(),
                "source_name": source.strip(),
            })
            if len(items) >= max_items:
                break
        return items
    except Exception as e:
        logging.warning(f"Google News fetch failed ({query}): {e}")
        return []


def fetch_pubmed(query: str, days: int = 30, max_results: int = 5) -> list[dict]:
    """PubMed E-utilitiesで論文検索"""
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    # Step 1: esearch
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "sort": "date",
        "reldate": days,
        "datetype": "edat",
        "retmode": "json",
    }

    try:
        time.sleep(0.4)  # rate limit: 3/sec without key
        resp = requests.get(f"{base}/esearch.fcgi", params=search_params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []

        # Step 2: esummary
        time.sleep(0.4)
        summary_params = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "json",
        }
        resp2 = requests.get(f"{base}/esummary.fcgi", params=summary_params, timeout=_TIMEOUT)
        resp2.raise_for_status()
        sdata = resp2.json()

        results = []
        for pmid in ids:
            article = sdata.get("result", {}).get(pmid, {})
            if not article:
                continue

            title = article.get("title", "")
            source = article.get("source", "")  # journal name
            pubdate = article.get("pubdate", "")
            authors = article.get("authors", [])
            first_author = authors[0].get("name", "") if authors else ""

            # 高インパクトジャーナルの判定
            high_impact = any(j in source.lower() for j in [
                "lancet", "n engl j med", "jama", "nature", "science",
                "cell", "j clin oncol", "blood", "ann oncol",
            ])

            results.append({
                "pmid": pmid,
                "title": title,
                "journal": source,
                "date": pubdate,
                "first_author": first_author,
                "high_impact": high_impact,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })
        return results
    except Exception as e:
        logging.warning(f"PubMed fetch failed ({query}): {e}")
        return []


# ニュースの影響度判定キーワード
POSITIVE_KEYWORDS = [
    "approval", "approved", "breakthrough", "fda clears", "positive results",
    "primary endpoint", "met primary", "phase 3", "phase iii", "pivotal",
    "partnership", "collaboration", "license agreement", "milestone",
    "fast track", "priority review", "orphan drug", "accelerated approval",
]
NEGATIVE_KEYWORDS = [
    "failed", "reject", "discontinue", "terminate", "withdraw", "recall",
    "safety concern", "adverse event", "clinical hold", "complete response letter",
    "did not meet", "negative results", "suspend",
]


def _classify_impact(title: str) -> str:
    """ニュースタイトルから影響度を推定"""
    t = title.lower()
    if any(kw in t for kw in NEGATIVE_KEYWORDS):
        return "negative"
    if any(kw in t for kw in POSITIVE_KEYWORDS):
        return "positive"
    return "info"


def check_news_all() -> list[dict]:
    """全マッピング企業のニュースをチェック"""
    company_map = _load_company_map()
    if not company_map:
        return []

    all_alerts = []
    searched = set()

    for code, info in company_map.items():
        name = info.get("name", "")
        keywords = info.get("keywords", [])

        for kw in keywords[:2]:  # 企業あたり2キーワードまで（レート制限対策）
            if kw in searched:
                continue
            searched.add(kw)
            time.sleep(0.5)

            # Google News
            news = fetch_google_news(kw, max_items=5)
            for n in news:
                impact = _classify_impact(n["title"])
                all_alerts.append({
                    "code": code,
                    "company": name,
                    "matched_keyword": kw,
                    "impact": impact,
                    "title": n["title"],
                    "link": n["link"],
                    "date": n["date"],
                    "source": f"Google News ({n.get('source_name', '')})",
                })

    if all_alerts:
        logging.info(f"News monitor: {len(all_alerts)} items found")
    return all_alerts


def check_pubmed_all(days: int = 14) -> list[dict]:
    """全マッピング企業の薬品名でPubMed検索"""
    company_map = _load_company_map()
    if not company_map:
        return []

    all_alerts = []
    searched = set()

    for code, info in company_map.items():
        name = info.get("name", "")
        for pipe in info.get("pipeline", []):
            drug = pipe.get("drug", "")
            if not drug or drug in searched or len(drug) < 4:
                continue
            searched.add(drug)

            articles = fetch_pubmed(drug, days=days, max_results=3)
            for a in articles:
                impact = "high_positive" if a["high_impact"] else "info"
                all_alerts.append({
                    "code": code,
                    "company": name,
                    "drug": drug,
                    "impact": impact,
                    "title": a["title"],
                    "journal": a["journal"],
                    "date": a["date"],
                    "link": a["url"],
                    "source": f"PubMed ({a['journal']})",
                    "high_impact_journal": a["high_impact"],
                })

    if all_alerts:
        logging.info(f"PubMed monitor: {len(all_alerts)} articles found")
    return all_alerts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== Google News ===")
    news = check_news_all()
    for a in news[:10]:
        print(f"  [{a['impact']}] {a['code']} {a['company']}: {a['title'][:80]}")

    print(f"\n=== PubMed ===")
    pubs = check_pubmed_all(days=30)
    for a in pubs[:10]:
        marker = " ★" if a.get("high_impact_journal") else ""
        print(f"  [{a['impact']}] {a['code']} {a['company']} ({a['drug']}): {a['title'][:60]}{marker}")
