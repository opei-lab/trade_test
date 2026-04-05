"""海外情報統合モニター

FDA、SEC EDGAR、ClinicalTrials.gov、Google News、PubMedを
統合的に監視し、日本IRに先行するシグナルを検出する。

スケジューラーからは run_overseas_check() を呼ぶだけ。
"""

import logging
from datetime import datetime
from pathlib import Path

import yaml

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
_ALERT_FILE = _DATA_DIR / "overseas_alerts.yaml"


def run_overseas_check(full: bool = False) -> dict:
    """海外情報チェックを実行

    Args:
        full: True=全ソースチェック（15-20分）。False=FDA+EDGARのみ（2-3分）

    Returns:
        {
            "alerts": [全アラート],
            "high_impact": [high_positive/high_negativeのみ],
            "by_code": {code: [アラート]},
            "checked_at": str,
        }
    """
    all_alerts = []

    # 1. FDA（最重要。承認/却下は即株価に影響）
    try:
        from src.data.overseas.fda_monitor import run_fda_check
        fda = run_fda_check()
        all_alerts.extend(fda)
        logging.info(f"FDA: {len(fda)} alerts")
    except Exception as e:
        logging.warning(f"FDA check failed: {e}")

    # 2. SEC EDGAR（提携先の8-K。リアルタイム性最高）
    try:
        from src.data.overseas.edgar_monitor import run_edgar_check
        edgar = run_edgar_check(days=7)
        all_alerts.extend(edgar)
        logging.info(f"EDGAR: {len(edgar)} alerts")
    except Exception as e:
        logging.warning(f"EDGAR check failed: {e}")

    # 3. ClinicalTrials.gov（Phase変化検出。初回は全件保存）
    try:
        from src.data.overseas.clinical_monitor import check_clinical_trials
        clinical = check_clinical_trials()
        all_alerts.extend(clinical)
        logging.info(f"ClinicalTrials: {len(clinical)} alerts")
    except Exception as e:
        logging.warning(f"ClinicalTrials check failed: {e}")

    if full:
        # 4. Google News（広範囲カバー。レート制限に注意）
        try:
            from src.data.overseas.news_monitor import check_news_all
            news = check_news_all()
            all_alerts.extend(news)
            logging.info(f"News: {len(news)} items")
        except Exception as e:
            logging.warning(f"News check failed: {e}")

        # 5. PubMed（論文。週1で十分）
        try:
            from src.data.overseas.news_monitor import check_pubmed_all
            pubmed = check_pubmed_all(days=14)
            all_alerts.extend(pubmed)
            logging.info(f"PubMed: {len(pubmed)} articles")
        except Exception as e:
            logging.warning(f"PubMed check failed: {e}")

    # 集計
    high_impact = [a for a in all_alerts if a.get("impact", "").startswith("high")]

    by_code = {}
    for a in all_alerts:
        code = a.get("code", "")
        if code not in by_code:
            by_code[code] = []
        by_code[code].append(a)

    result = {
        "alerts": all_alerts,
        "high_impact": high_impact,
        "by_code": by_code,
        "total": len(all_alerts),
        "high_count": len(high_impact),
        "checked_at": datetime.now().isoformat(),
    }

    # 保存
    _save_summary(result)

    if high_impact:
        logging.warning(
            f"🚨 {len(high_impact)} high-impact overseas alerts: "
            + ", ".join(f"{a['code']} {a['company']}" for a in high_impact[:5])
        )

    return result


def _save_summary(result: dict):
    """直近の結果サマリーを保存"""
    _DATA_DIR.mkdir(exist_ok=True)
    summary = {
        "checked_at": result["checked_at"],
        "total": result["total"],
        "high_count": result["high_count"],
        "high_impact": result["high_impact"][:20],
        "alerts": result["alerts"][:50],
    }
    with open(_ALERT_FILE, "w", encoding="utf-8") as f:
        yaml.dump(summary, f, allow_unicode=True, default_flow_style=False)


def load_overseas_alerts() -> dict:
    """保存済みアラートを読み込む"""
    if not _ALERT_FILE.exists():
        return {"alerts": [], "high_impact": [], "checked_at": None}
    try:
        with open(_ALERT_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {"alerts": [], "high_impact": [], "checked_at": None}


def get_alerts_for_code(code: str) -> list[dict]:
    """特定銘柄の海外アラートを取得"""
    data = load_overseas_alerts()
    return [a for a in data.get("alerts", []) if a.get("code") == code]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("Running overseas monitor (full scan)...")
    result = run_overseas_check(full=True)
    print(f"\nTotal: {result['total']} alerts, {result['high_count']} high-impact")
    for a in result["high_impact"]:
        print(f"  🚨 [{a['impact']}] {a['code']} {a['company']}: {a.get('title', '')[:80]}")
