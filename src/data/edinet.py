"""EDINET（大量保有報告書）データ取得モジュール

大口投資家の売買動向を検出する。
EDINET APIは無料・登録不要。
"""

import requests
from datetime import datetime, timedelta

EDINET_API = "https://api.edinet-fsa.go.jp/api/v2"


def fetch_large_holdings(days_back: int = 30) -> list[dict]:
    """直近の大量保有報告書を取得する。

    Args:
        days_back: 何日前まで遡るか

    Returns:
        [{"filer": 提出者, "company": 対象企業, "shares_pct": 保有割合, "date": 提出日}]
    """
    results = []
    end = datetime.now()
    start = end - timedelta(days=days_back)
    current = start

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        try:
            resp = requests.get(
                f"{EDINET_API}/documents.json",
                params={"date": date_str, "type": 2},
                timeout=15,
            )
            if resp.status_code != 200:
                current += timedelta(days=1)
                continue

            data = resp.json()
            for doc in data.get("results", []):
                doc_type = doc.get("docTypeCode", "")
                # 大量保有報告書: 060, 変更報告書: 070
                if doc_type in ("060", "070"):
                    results.append({
                        "doc_id": doc.get("docID", ""),
                        "filer": doc.get("filerName", ""),
                        "company": doc.get("securityName", ""),
                        "security_code": doc.get("secCode", ""),
                        "doc_type": "大量保有" if doc_type == "060" else "変更報告",
                        "date": date_str,
                        "description": doc.get("docDescription", ""),
                    })

        except Exception:
            pass

        current += timedelta(days=1)

    return results


def find_holdings_for_code(code: str, days_back: int = 90) -> list[dict]:
    """特定銘柄の大量保有報告書を検索する。"""
    all_holdings = fetch_large_holdings(days_back=days_back)
    code_str = str(code).zfill(4)

    return [
        h for h in all_holdings
        if code_str in str(h.get("security_code", ""))
    ]
