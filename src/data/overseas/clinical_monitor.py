"""ClinicalTrials.gov API v2 監視モジュール

マッピングテーブルの企業名・NCT番号で治験ステータスの変化を検出する。
Phase 3完了、結果公開、中止等は日本IRに先行する可能性が高い。

データソース:
  - ClinicalTrials.gov API v2 (無料、認証不要)
"""

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import yaml

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_MAP_FILE = _CONFIG_DIR / "overseas_map.yaml"
_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
_STATE_FILE = _DATA_DIR / "clinical_state.yaml"

CT_API = "https://clinicaltrials.gov/api/v2/studies"
_TIMEOUT = 15
_RATE_LIMIT = 0.2  # ~5 req/sec


def _load_company_map() -> dict:
    if not _MAP_FILE.exists():
        return {}
    with open(_MAP_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_state() -> dict:
    """前回チェック時のステータスを読み込む"""
    if not _STATE_FILE.exists():
        return {}
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _save_state(state: dict):
    _DATA_DIR.mkdir(exist_ok=True)
    state["_updated"] = datetime.now().isoformat()
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        yaml.dump(state, f, allow_unicode=True, default_flow_style=False)


def search_trials(query: str, max_results: int = 10) -> list[dict]:
    """ClinicalTrials.gov API v2で治験を検索"""
    params = {
        "query.term": query,
        "pageSize": max_results,
        "sort": "LastUpdatePostDate:desc",
        "fields": "NCTId,BriefTitle,OverallStatus,Phase,LastUpdatePostDate,"
                  "StartDate,CompletionDate,StudyType,Condition,InterventionName,"
                  "LeadSponsorName,CollaboratorName,ResultsFirstPostDate",
        "format": "json",
    }

    try:
        time.sleep(_RATE_LIMIT)
        resp = requests.get(CT_API, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for study in data.get("studies", []):
            proto = study.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            design = proto.get("designModule", {})
            sponsor = proto.get("sponsorCollaboratorsModule", {})
            arms = proto.get("armsInterventionsModule", {})
            conditions = proto.get("conditionsModule", {})
            results_section = study.get("resultsSection")

            # スポンサー/コラボレーター
            lead = sponsor.get("leadSponsor", {}).get("name", "")
            collabs = [c.get("name", "") for c in sponsor.get("collaborators", [])]

            # 介入名
            interventions = [i.get("name", "") for i in arms.get("interventions", [])]

            results.append({
                "nct_id": ident.get("nctId", ""),
                "title": ident.get("briefTitle", ""),
                "status": status_mod.get("overallStatus", ""),
                "phase": ", ".join(design.get("phases", [])),
                "last_update": status_mod.get("lastUpdatePostDateStruct", {}).get("date", ""),
                "completion_date": status_mod.get("completionDateStruct", {}).get("date", ""),
                "lead_sponsor": lead,
                "collaborators": collabs,
                "conditions": conditions.get("conditions", []),
                "interventions": interventions,
                "has_results": results_section is not None,
            })
        return results
    except Exception as e:
        logging.warning(f"ClinicalTrials.gov search failed ({query}): {e}")
        return []


def search_by_nct(nct_id: str) -> dict:
    """NCT番号で直接検索"""
    url = f"{CT_API}/{nct_id}"
    params = {
        "fields": "NCTId,BriefTitle,OverallStatus,Phase,LastUpdatePostDate,"
                  "CompletionDate,ResultsFirstPostDate",
        "format": "json",
    }

    try:
        time.sleep(_RATE_LIMIT)
        resp = requests.get(url, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        proto = data.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        design = proto.get("designModule", {})

        return {
            "nct_id": ident.get("nctId", ""),
            "title": ident.get("briefTitle", ""),
            "status": status_mod.get("overallStatus", ""),
            "phase": ", ".join(design.get("phases", [])),
            "last_update": status_mod.get("lastUpdatePostDateStruct", {}).get("date", ""),
            "has_results": data.get("resultsSection") is not None,
        }
    except Exception as e:
        logging.warning(f"ClinicalTrials.gov NCT fetch failed ({nct_id}): {e}")
        return {}


# ステータス変化で重要なもの
STATUS_IMPACT = {
    "COMPLETED": "positive",           # 治験完了
    "ACTIVE_NOT_RECRUITING": "info",   # 登録終了、結果待ち
    "TERMINATED": "negative",          # 中止
    "WITHDRAWN": "negative",           # 取り下げ
    "SUSPENDED": "negative",           # 一時中断
}


def check_clinical_trials() -> list[dict]:
    """全マッピング企業の治験ステータスをチェック"""
    company_map = _load_company_map()
    if not company_map:
        return []

    prev_state = _load_state()
    new_state = {}
    alerts = []

    for code, info in company_map.items():
        name = info.get("name", "")
        en_name = info.get("en_name", "")

        # 1. NCT番号で直接監視
        for nct_id in info.get("nct_ids", []):
            trial = search_by_nct(nct_id)
            if not trial:
                continue

            state_key = nct_id
            new_state[state_key] = trial.get("status", "")
            prev_status = prev_state.get(state_key)

            if prev_status and prev_status != trial["status"]:
                impact = STATUS_IMPACT.get(trial["status"], "info")
                alerts.append({
                    "code": code,
                    "company": name,
                    "nct_id": nct_id,
                    "title": trial["title"],
                    "prev_status": prev_status,
                    "new_status": trial["status"],
                    "phase": trial["phase"],
                    "impact": impact,
                    "source": "ClinicalTrials.gov",
                    "change": f"{prev_status} → {trial['status']}",
                })

            # 結果公開の検出
            if trial.get("has_results") and not prev_state.get(f"{nct_id}_results"):
                new_state[f"{nct_id}_results"] = True
                alerts.append({
                    "code": code,
                    "company": name,
                    "nct_id": nct_id,
                    "title": trial["title"],
                    "impact": "high_positive",
                    "source": "ClinicalTrials.gov",
                    "change": "Results posted",
                })

        # 2. 企業名で検索（パイプライン更新検出）
        if en_name:
            trials = search_trials(en_name, max_results=5)
            for trial in trials:
                state_key = trial.get("nct_id", "")
                if not state_key:
                    continue

                new_state[state_key] = trial.get("status", "")
                prev_status = prev_state.get(state_key)

                if prev_status and prev_status != trial["status"]:
                    impact = STATUS_IMPACT.get(trial["status"], "info")
                    alerts.append({
                        "code": code,
                        "company": name,
                        "nct_id": state_key,
                        "title": trial["title"],
                        "prev_status": prev_status,
                        "new_status": trial["status"],
                        "phase": trial["phase"],
                        "impact": impact,
                        "source": "ClinicalTrials.gov",
                        "change": f"{prev_status} → {trial['status']}",
                    })

    _save_state(new_state)

    if alerts:
        logging.info(f"ClinicalTrials.gov: {len(alerts)} status changes detected")
    return alerts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    alerts = check_clinical_trials()
    print(f"\n=== ClinicalTrials.gov Alerts: {len(alerts)} ===")
    for a in alerts:
        print(f"  [{a['impact']}] {a['code']} {a['company']}")
        print(f"    {a.get('nct_id', '')} {a.get('change', '')}")
        print(f"    {a.get('title', '')[:80]}")
