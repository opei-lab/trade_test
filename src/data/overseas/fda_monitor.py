"""FDA承認・プレスリリース監視モジュール

FDA公式RSSフィードを定期ポーリングし、マッピングテーブルの薬品名/企業名にマッチする
承認・却下・安全性警告を検出する。

データソース:
  - FDA Press Releases RSS (当日反映)
  - FDA Drug Approvals RSS
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import requests
import yaml

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_MAP_FILE = _CONFIG_DIR / "overseas_map.yaml"
_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
_ALERT_FILE = _DATA_DIR / "overseas_alerts.yaml"

FDA_FEEDS = {
    "press": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml",
    "drugs": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml",
    "biologics": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/biologics/rss.xml",
}

_HEADERS = {"User-Agent": "Mozilla/5.0 (StockScreener/1.0; research)"}
_TIMEOUT = 15


def _load_company_map() -> dict:
    """overseas_map.yamlを読み込む"""
    if not _MAP_FILE.exists():
        return {}
    with open(_MAP_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_keyword_index(company_map: dict) -> list[tuple[str, str, str]]:
    """(keyword_lower, code, company_name)のリストを構築"""
    index = []
    for code, info in company_map.items():
        name = info.get("name", "")
        # 英語社名
        en = info.get("en_name", "")
        if en:
            index.append((en.lower(), code, name))
        # パイプラインの薬品名
        for pipe in info.get("pipeline", []):
            drug = pipe.get("drug", "")
            if drug and len(drug) >= 3:
                index.append((drug.lower(), code, name))
        # キーワード
        for kw in info.get("keywords", []):
            if kw and len(kw) >= 3:
                index.append((kw.lower(), code, name))
        # パートナー名
        for partner in info.get("partners", []):
            pname = partner.get("name", "")
            if pname:
                index.append((pname.lower(), code, name))
    return index


def _fetch_rss(url: str) -> list[dict]:
    """RSSフィードを取得してアイテムリストを返す"""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        items = []
        # RSS 2.0
        for item in root.iter("item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")
            desc = item.findtext("description", "")
            items.append({
                "title": title.strip(),
                "link": link.strip(),
                "date": pub_date.strip(),
                "description": desc.strip()[:500],
            })
        # Atom
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = entry.findtext("{http://www.w3.org/2005/Atom}title", "")
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.get("href", "") if link_el is not None else ""
            updated = entry.findtext("{http://www.w3.org/2005/Atom}updated", "")
            summary = entry.findtext("{http://www.w3.org/2005/Atom}summary", "")
            items.append({
                "title": title.strip(),
                "link": link.strip(),
                "date": updated.strip(),
                "description": (summary or "").strip()[:500],
            })
        return items
    except Exception as e:
        logging.warning(f"FDA RSS fetch failed ({url}): {e}")
        return []


def _match_item(item: dict, keyword_index: list) -> list[dict]:
    """RSSアイテムがマッピングテーブルのキーワードにマッチするか判定"""
    text = f"{item['title']} {item['description']}".lower()
    matches = []
    seen_codes = set()

    for keyword, code, company_name in keyword_index:
        if code in seen_codes:
            continue
        kw_lower = keyword.lower()
        # 単語境界チェック（"changes"に"anges"がマッチしないように）
        if len(kw_lower) <= 4:
            # 短いキーワードは完全一致に近い形で（前後がアルファベットでない）
            if not re.search(r'(?<![a-z])' + re.escape(kw_lower) + r'(?![a-z])', text):
                continue
        elif kw_lower not in text:
            continue
        else:
            # 5文字以上でも単語境界チェック
            if not re.search(r'(?<![a-z])' + re.escape(kw_lower) + r'(?![a-z])', text):
                continue
            seen_codes.add(code)

            # 影響度の推定
            title_lower = item["title"].lower()
            impact = "medium"
            if any(w in title_lower for w in ["approv", "granted", "clearance", "authorize"]):
                impact = "high_positive"
            elif any(w in title_lower for w in ["reject", "refuse", "withdraw", "crl", "complete response"]):
                impact = "high_negative"
            elif any(w in title_lower for w in ["recall", "safety", "warning", "adverse"]):
                impact = "negative"
            elif any(w in title_lower for w in ["breakthrough", "fast track", "priority review", "orphan"]):
                impact = "positive"
            elif any(w in title_lower for w in ["phase 3", "phase iii", "pivotal", "primary endpoint"]):
                impact = "positive"
            elif any(w in title_lower for w in ["failed", "did not meet", "negative result"]):
                impact = "high_negative"

            matches.append({
                "code": code,
                "company": company_name,
                "matched_keyword": keyword,
                "impact": impact,
                "title": item["title"],
                "link": item["link"],
                "date": item["date"],
                "source": "FDA",
            })
    return matches


def check_fda_feeds() -> list[dict]:
    """全FDAフィードをチェックし、マッチするアラートを返す"""
    company_map = _load_company_map()
    if not company_map:
        return []

    keyword_index = _build_keyword_index(company_map)
    all_alerts = []

    for feed_name, url in FDA_FEEDS.items():
        items = _fetch_rss(url)
        for item in items:
            matches = _match_item(item, keyword_index)
            for m in matches:
                m["feed"] = feed_name
            all_alerts.extend(matches)

    # 重複除去（同じtitle+codeの組み合わせ）
    seen = set()
    unique = []
    for a in all_alerts:
        key = (a["code"], a["title"])
        if key not in seen:
            seen.add(key)
            unique.append(a)

    if unique:
        logging.info(f"FDA monitor: {len(unique)} alerts detected")
    return unique


def _load_existing_alerts() -> list[dict]:
    """既存のアラートを読み込む"""
    if not _ALERT_FILE.exists():
        return []
    try:
        with open(_ALERT_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("alerts", []) if data else []
    except Exception:
        return []


def _save_alerts(alerts: list[dict]):
    """アラートを保存"""
    _DATA_DIR.mkdir(exist_ok=True)
    data = {
        "updated": datetime.now().isoformat(),
        "alerts": alerts[-100:],  # 直近100件のみ保持
    }
    with open(_ALERT_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def run_fda_check() -> list[dict]:
    """FDAチェックを実行し、新規アラートのみ返す"""
    new_alerts = check_fda_feeds()
    if not new_alerts:
        return []

    existing = _load_existing_alerts()
    existing_keys = {(a["code"], a["title"]) for a in existing}

    fresh = [a for a in new_alerts if (a["code"], a["title"]) not in existing_keys]

    if fresh:
        all_alerts = existing + fresh
        _save_alerts(all_alerts)
        logging.info(f"FDA: {len(fresh)} new alerts saved")

    return fresh


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    alerts = run_fda_check()
    print(f"\n=== FDA Alerts: {len(alerts)} ===")
    for a in alerts:
        print(f"  [{a['impact']}] {a['code']} {a['company']}: {a['title'][:80]}")
        print(f"    matched: {a['matched_keyword']}, link: {a['link']}")
