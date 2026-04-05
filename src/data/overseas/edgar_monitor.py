"""SEC EDGAR 8-K監視モジュール

提携先の米国企業が提出する8-K（重要事象開示）を監視し、
日本のバイオ企業に関連する開示を先行検出する。

データソース:
  - SEC EDGAR EFTS（全文検索）: ほぼリアルタイム
  - SEC EDGAR submissions API: 1秒以下の遅延
"""

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import yaml

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_MAP_FILE = _CONFIG_DIR / "overseas_map.yaml"

EDGAR_BASE = "https://efts.sec.gov/LATEST"
EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions"

# SEC requires contact info in User-Agent
_HEADERS = {
    "User-Agent": "StockScreener/1.0 (research@example.com)",
    "Accept": "application/json",
}
_TIMEOUT = 15
_RATE_LIMIT = 0.15  # 10 requests/sec max


def _load_company_map() -> dict:
    if not _MAP_FILE.exists():
        return {}
    with open(_MAP_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_partner_ciks(company_map: dict) -> list[dict]:
    """CIK番号を持つパートナー企業のリストを構築"""
    partners = []
    seen_ciks = set()
    for code, info in company_map.items():
        for partner in info.get("partners", []):
            cik = partner.get("sec_cik")
            if cik and cik not in seen_ciks:
                seen_ciks.add(cik)
                partners.append({
                    "cik": cik,
                    "partner_name": partner["name"],
                    "jp_code": code,
                    "jp_name": info.get("name", ""),
                    "keywords": info.get("keywords", []),
                })
    return partners


def fetch_recent_8k(cik: str, days: int = 7) -> list[dict]:
    """指定CIKの直近8-K提出を取得"""
    # CIKを10桁にパディング
    cik_padded = cik.zfill(10)
    url = f"{EDGAR_SUBMISSIONS}/CIK{cik_padded}.json"

    try:
        time.sleep(_RATE_LIMIT)
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        company_name = data.get("name", "")
        filings = data.get("filings", {}).get("recent", {})

        forms = filings.get("form", [])
        dates = filings.get("filingDate", [])
        accessions = filings.get("accessionNumber", [])
        descriptions = filings.get("primaryDocDescription", [])

        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        results = []
        for i, form in enumerate(forms):
            if form not in ("8-K", "8-K/A", "6-K"):
                continue
            filing_date = dates[i] if i < len(dates) else ""
            if filing_date < cutoff:
                break  # 古い順なのでbreakで良い場合もあるが、念のため全部見る
                # 実際は新しい順なのでbreakは正しい

            accession = accessions[i] if i < len(accessions) else ""
            desc = descriptions[i] if i < len(descriptions) else ""
            acc_formatted = accession.replace("-", "")

            results.append({
                "form": form,
                "date": filing_date,
                "accession": accession,
                "description": desc,
                "company": company_name,
                "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_formatted}/{accession}-index.htm",
            })

        return results
    except Exception as e:
        logging.warning(f"EDGAR fetch failed (CIK {cik}): {e}")
        return []


def search_edgar_fulltext(query: str, days: int = 7) -> list[dict]:
    """EDGAR全文検索（EFTS）で日本企業名を検索"""
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"{EDGAR_BASE}/search-index"
    params = {
        "q": f'"{query}"',
        "dateRange": "custom",
        "startdt": date_from,
        "enddt": datetime.now().strftime("%Y-%m-%d"),
        "forms": "8-K,8-K/A,6-K",
    }

    try:
        time.sleep(_RATE_LIMIT)
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for hit in data.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            results.append({
                "form": source.get("forms", ""),
                "date": source.get("file_date", ""),
                "company": source.get("display_names", [""])[0] if source.get("display_names") else "",
                "description": source.get("display_names", [""])[0],
                "url": f"https://www.sec.gov/Archives/edgar/data/{source.get('file_num', '')}",
            })
        return results
    except Exception as e:
        logging.warning(f"EDGAR fulltext search failed ({query}): {e}")
        return []


def check_edgar_partners(days: int = 7) -> list[dict]:
    """全パートナー企業の8-Kをチェックし、関連する開示を返す"""
    company_map = _load_company_map()
    if not company_map:
        return []

    partners = _get_partner_ciks(company_map)
    all_alerts = []

    for p in partners:
        filings = fetch_recent_8k(p["cik"], days=days)

        for f in filings:
            # 日本企業のキーワードでフィルタ
            text = f"{f['description']} {f['company']}".lower()

            # パートナー企業の8-K/6-Kの影響度判定
            impact = "info"
            desc_lower = f["description"].lower() if f["description"] else ""
            if any(kw.lower() in text for kw in p["keywords"]):
                impact = "high_positive"  # 直接言及
            elif any(w in desc_lower for w in ["approv", "positive result", "met primary",
                                                "breakthrough", "phase 3", "phase iii"]):
                impact = "positive"
            elif any(w in desc_lower for w in ["collaboration", "license", "agreement", "milestone"]):
                impact = "positive"
            elif any(w in desc_lower for w in ["terminat", "discontinu", "withdraw", "fail",
                                                "did not meet", "negative result", "safety"]):
                impact = "negative"

            all_alerts.append({
                "code": p["jp_code"],
                "company": p["jp_name"],
                "partner": p["partner_name"],
                "impact": impact,
                "form": f["form"],
                "date": f["date"],
                "description": f["description"],
                "filing_company": f["company"],
                "url": f["url"],
                "source": "SEC_EDGAR",
            })

    if all_alerts:
        logging.info(f"EDGAR monitor: {len(all_alerts)} filings from partners")
    return all_alerts


def check_edgar_fulltext(days: int = 7) -> list[dict]:
    """EDGAR全文検索で日本企業の英語名を検索"""
    company_map = _load_company_map()
    if not company_map:
        return []

    alerts = []
    searched = set()

    for code, info in company_map.items():
        en_name = info.get("en_name", "")
        if not en_name or en_name in searched:
            continue
        searched.add(en_name)

        results = search_edgar_fulltext(en_name, days=days)
        for r in results:
            alerts.append({
                "code": code,
                "company": info.get("name", ""),
                "impact": "positive",
                "source": "SEC_EDGAR_FT",
                **r,
            })

    return alerts


def run_edgar_check(days: int = 7) -> list[dict]:
    """EDGAR監視を実行"""
    alerts = []

    # 1. パートナー企業の8-K
    partner_alerts = check_edgar_partners(days=days)
    alerts.extend(partner_alerts)

    # 2. 全文検索（レート制限に注意）
    ft_alerts = check_edgar_fulltext(days=days)
    alerts.extend(ft_alerts)

    return alerts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    alerts = run_edgar_check(days=30)
    print(f"\n=== EDGAR Alerts: {len(alerts)} ===")
    for a in alerts:
        print(f"  [{a['impact']}] {a['code']} {a['company']}")
        print(f"    {a.get('form', '')} {a.get('date', '')} - {a.get('filing_company', '')}")
        print(f"    {a.get('description', '')[:80]}")
        print(f"    {a.get('url', '')}")
