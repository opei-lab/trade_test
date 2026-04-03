"""Stage 3: シナリオ構築モジュール

IR/ニュースの事実からインパクトを評価し、
銘柄ごとのストーリーを構築する。
特色がない銘柄を除外し、トリガーが明確な銘柄だけを残す。

全キーワードはconfig/impact_keywords.yamlから読み込み。
コード変更なしでキーワードの追加・修正が可能。
"""

import yaml
from pathlib import Path
from src.llm.client import is_available, generate
from src.llm.news_analyzer import fetch_kabutan_news
from src.data.tdnet import fetch_recent_disclosures, detect_positive_catalysts, detect_dilution_risk
import numpy as np

# --- Config loader ---

_CONFIG_DIR = Path(__file__).parent.parent / "config"
_keywords_cache = None
_themes_cache = None


def _load_keywords() -> dict:
    """impact_keywords.yaml をキャッシュ付きで読み込む。"""
    global _keywords_cache
    if _keywords_cache is not None:
        return _keywords_cache
    path = _CONFIG_DIR / "impact_keywords.yaml"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            _keywords_cache = yaml.safe_load(f) or {}
        return _keywords_cache
    except Exception:
        return {}


def _load_themes() -> dict:
    """themes.yaml をキャッシュ付きで読み込む。"""
    global _themes_cache
    if _themes_cache is not None:
        return _themes_cache
    path = _CONFIG_DIR / "themes.yaml"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            _themes_cache = yaml.safe_load(f) or {}
        return _themes_cache
    except Exception:
        return {}


# --- Context-aware keyword classification ---

def classify_with_context(keyword: str, text: str, config: dict) -> str:
    """文脈を考慮してキーワードをpositive/negative/neutralに分類する。

    例: 「拡大」→ 「利益拡大」ならpositive、「赤字拡大」ならnegative
    """
    rules = config.get("context_rules", {})
    if keyword not in rules:
        return "positive"  # ルールなし = デフォルトpositive

    rule = rules[keyword]
    for ctx in rule.get("negative_context", []):
        if ctx in text:
            return "negative"
    for ctx in rule.get("positive_context", []):
        if ctx in text:
            return "positive"
    return rule.get("default", "neutral")


def count_impacts(all_text: str, config: dict) -> tuple[int, int, int]:
    """キーワードをカウントする（文脈考慮付き）。

    Returns: (high_count, medium_count, negative_count)
    """
    high_kws = config.get("high_impact", [])
    medium_kws = config.get("medium_impact", [])
    negative_kws = config.get("negative_impact", [])

    # ネガティブを先にチェック（「赤字拡大」がmediumの「拡大」に誤マッチしないように）
    negative_count = sum(1 for kw in negative_kws if kw in all_text)

    # ネガティブ文脈でマッチしたキーワードをmediumから除外
    high_count = 0
    for kw in high_kws:
        if kw in all_text:
            ctx = classify_with_context(kw, all_text, config)
            if ctx != "negative":
                high_count += 1

    medium_count = 0
    for kw in medium_kws:
        if kw in all_text:
            ctx = classify_with_context(kw, all_text, config)
            if ctx == "positive":
                medium_count += 1
            elif ctx == "negative":
                negative_count += 1
            # neutral は無視

    return high_count, medium_count, negative_count


# --- Theme detection ---

_maturity_cache = None


def _load_maturity_config() -> dict:
    """theme_maturity.yaml をキャッシュ付きで読み込む。"""
    global _maturity_cache
    if _maturity_cache is not None:
        return _maturity_cache
    path = _CONFIG_DIR / "theme_maturity.yaml"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            _maturity_cache = yaml.safe_load(f) or {}
        return _maturity_cache
    except Exception:
        return {}


def assess_theme_maturity(text: str) -> dict:
    """テーマの成熟度を判定する。

    Returns:
        {
            "maturity": "confirmed" | "emerging" | "hype",
            "confirmed_hits": [...],
            "emerging_hits": [...],
            "hype_hits": [...],
        }
    """
    mc = _load_maturity_config()
    confirmed_kws = mc.get("confirmed_signals", [])
    emerging_kws = mc.get("emerging_signals", [])
    hype_kws = mc.get("hype_signals", [])

    confirmed_hits = [kw for kw in confirmed_kws if kw in text]
    emerging_hits = [kw for kw in emerging_kws if kw in text]
    hype_hits = [kw for kw in hype_kws if kw in text]

    if len(confirmed_hits) >= 2:
        maturity = "confirmed"
    elif len(confirmed_hits) >= 1 or len(emerging_hits) >= 2:
        maturity = "emerging"
    else:
        maturity = "hype"

    return {
        "maturity": maturity,
        "confirmed_hits": confirmed_hits,
        "emerging_hits": emerging_hits,
        "hype_hits": hype_hits,
    }


def detect_themes(text: str, name: str, code: str) -> list[dict]:
    """テーマキーワードとのマッチングでテーマを検出する。

    themes.yamlから読み込み。known_codesに含まれる場合は優先マッチ。
    成熟度判定付き。
    """
    themes = _load_themes()
    combined = f"{text} {name}"
    matched = []

    for theme_key, theme_data in themes.items():
        if not isinstance(theme_data, dict):
            continue

        # known_codesに含まれる場合は即マッチ
        known = theme_data.get("known_codes", [])
        if code in [str(c) for c in known]:
            maturity = assess_theme_maturity(text)
            matched.append({
                "theme_key": theme_key,
                "theme_name": theme_data.get("name", theme_key),
                "matched_keywords": ["known_code"],
                "catalysts": theme_data.get("catalysts", []),
                "maturity": maturity["maturity"],
                "maturity_detail": maturity,
            })
            continue

        # キーワードマッチ（2個以上で判定。1個だけだと過剰マッチ）
        keywords = theme_data.get("keywords", [])
        hits = [kw for kw in keywords if kw in combined]
        if len(hits) >= 2 or (len(hits) >= 1 and len(keywords) <= 3):
            maturity = assess_theme_maturity(text)
            matched.append({
                "theme_key": theme_key,
                "theme_name": theme_data.get("name", theme_key),
                "matched_keywords": hits,
                "catalysts": theme_data.get("catalysts", []),
                "maturity": maturity["maturity"],
                "maturity_detail": maturity,
            })

    return matched


# --- Bio pipeline detection ---

def detect_bio_pipeline(all_text: str, name: str, config: dict) -> dict:
    """ニューステキストからバイオのパイプライン情報を自動検出する。

    Returns:
        {
            "phases_detected": ["Phase 2", "Phase 3"],
            "highest_phase": "Phase 3",
            "diseases": ["肺がん"],
            "trial_positive": [...],
            "trial_negative": [...],
            "has_pipeline_info": bool,
        }
    """
    bio = config.get("bio_pipeline", {})
    if not bio:
        return {"has_pipeline_info": False}

    combined = f"{all_text} {name}"

    # フェーズ検出
    phase_kws = bio.get("phase_keywords", {})
    phases = set()
    for kw, phase_name in phase_kws.items():
        if kw in combined:
            phases.add(phase_name)

    # 疾患検出
    disease_kws = bio.get("disease_keywords", [])
    diseases = [d for d in disease_kws if d in combined]

    # 試験結果
    pos_kws = bio.get("positive_trial", [])
    neg_kws = bio.get("negative_trial", [])
    trial_pos = [kw for kw in pos_kws if kw in combined]
    trial_neg = [kw for kw in neg_kws if kw in combined]

    # 最高フェーズ
    phase_order = ["Preclinical", "Phase 1", "Clinical", "Phase 2", "Phase 3"]
    highest = None
    for p in phase_order:
        if p in phases:
            highest = p

    return {
        "phases_detected": sorted(phases),
        "highest_phase": highest,
        "diseases": diseases[:5],
        "trial_positive": trial_pos,
        "trial_negative": trial_neg,
        "has_pipeline_info": bool(phases or diseases or trial_pos or trial_neg),
    }


# --- Sector pattern matching ---

def match_sector_patterns(text: str, name: str = "") -> list:
    """IRテキストからセクター別の勝ち確パターンにマッチするか判定。"""
    config_path = _CONFIG_DIR / "sector_patterns.yaml"
    if not config_path.exists():
        return []

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            sectors = yaml.safe_load(f)
    except Exception:
        return []

    matched = []
    combined = f"{text} {name}".lower()

    for sector_key, sector_data in sectors.items():
        if not isinstance(sector_data, dict):
            continue
        match_keywords = sector_data.get("match", [])
        sector_match = any(kw.lower() in combined for kw in match_keywords)
        if not sector_match:
            continue

        for pattern in sector_data.get("patterns", []):
            ir_keywords = pattern.get("ir_keywords", [])
            if any(kw in text for kw in ir_keywords):
                matched.append({
                    "sector": sector_data.get("name", sector_key),
                    "pattern": pattern.get("name", ""),
                    "description": pattern.get("description", ""),
                    "confidence": pattern.get("confidence", 50),
                    "typical_move": pattern.get("typical_move", ""),
                    "timeframe": pattern.get("timeframe", ""),
                    "high_conviction_if": pattern.get("high_conviction_if", []),
                    "low_conviction_if": pattern.get("low_conviction_if", []),
                    "trap": pattern.get("trap", ""),
                    "exit_strategy": pattern.get("exit_strategy", ""),
                })

    return matched


# --- Decline analysis ---

def analyze_decline_reason(df, current: float) -> dict:
    """株価が底値圏にいる理由を分析する。"""
    close = df["Close"]
    volume = df["Volume"]
    high = df["High"]
    low = df["Low"]

    if len(close) < 30:
        return {"reason": "unknown", "score": 0}

    ret_30d = (float(close.iloc[-1]) - float(close.iloc[-30])) / float(close.iloc[-30]) * 100
    if ret_30d > -5:
        return {"reason": "not_declining", "score": 0, "note": "下落していない"}

    recent_30 = df.tail(30)
    vol_trend = float(volume.tail(10).mean()) / float(volume.tail(30).mean())
    clv = ((close - low) - (high - close)) / (high - low + 1e-10)
    avg_clv = float(clv.tail(10).mean())
    vol_up = vol_trend > 1.2
    range_small = float(((recent_30["High"] - recent_30["Low"]) / recent_30["Close"]).tail(10).mean()) < 0.03
    clv_high = avg_clv > 0.1
    obv = (np.sign(close.diff()) * volume).cumsum()
    obv_trend = float(obv.iloc[-1] - obv.iloc[-10])

    if vol_up and clv_high and obv_trend > 0:
        return {
            "reason": "whale_accumulation",
            "score": 80,
            "note": "下落中に大口が拾っている兆候（出来高増+CLV高+OBV上昇）。意図的な安値形成の可能性。チャンス",
        }
    elif vol_up and not clv_high:
        return {
            "reason": "algo_selling",
            "score": 50,
            "note": "出来高増だがCLV低い。アルゴの機械的な売りの可能性。大口が入れば反転",
        }
    elif not vol_up and range_small:
        return {
            "reason": "market_wide",
            "score": 40,
            "note": "出来高減+値幅小。市場全体の影響または無関心。銘柄固有の悪材料ではない可能性",
        }
    else:
        return {
            "reason": "fundamental",
            "score": 10,
            "note": "出来高を伴った下落。ファンダメンタルの悪化の可能性。要注意",
        }


# --- Main scenario builder ---

def build_scenario(code: str, name: str, current_price: float, structure: dict = None,
                    rich: bool = False, df=None) -> dict:
    """銘柄のシナリオを構築する。

    全キーワードはconfig/impact_keywords.yamlから読み込み。
    テーマ検出はconfig/themes.yamlから読み込み。

    Args:
        rich: True=個別分析用（本文取得あり、遅い）。False=バッチスキャン用（高速）
        df: 株価DataFrame（外部から渡せば内部でのfetch_priceをスキップ）
    """
    config = _load_keywords()

    # IR/ニュース取得（バッチでは本文なし。1ページのみ）
    news = []
    try:
        if rich:
            news = fetch_kabutan_news(code, max_items=15, max_pages=3, fetch_body=True)
        else:
            news = fetch_kabutan_news(code, max_items=10, max_pages=1)
    except Exception:
        pass

    # 適時開示（1回だけ取得して使い回す）
    disclosures = []
    try:
        if rich:
            disclosures = fetch_recent_disclosures(code, max_items=20, max_pages=2)
        else:
            disclosures = fetch_recent_disclosures(code, max_items=10, max_pages=1)
    except Exception:
        pass

    # 開示データから直接判定（再フェッチしない）
    positive_cats = {"upward_revision", "dividend_increase", "buyback", "alliance",
                     "capital_alliance", "contract", "order", "approval", "record_profit", "turnaround"}
    positive = [d for d in disclosures if d.get("category") in positive_cats]
    dilution_items = [d for d in disclosures if d.get("category") == "dilution"]
    dilution = {"has_risk": len(dilution_items) > 0, "details": dilution_items}

    # テキスト統合
    all_titles = [n.get("title", "") for n in news] + [d.get("title", "") for d in disclosures]
    bodies = [n.get("body", "") for n in news if n.get("body")] if rich else []
    all_text = " ".join(all_titles + bodies)

    # --- コンテキスト付きキーワード分析 ---
    high_count, medium_count, negative_count = count_impacts(all_text, config)

    # --- セクター別パターンマッチング ---
    matched_patterns = match_sector_patterns(all_text, name)

    # パターンの効果は時価総額で減衰
    mcap = 0
    if structure:
        whale = structure.get("whale", {})

    cap_factor = 1.0
    if mcap > 100e9:
        cap_factor = 0.1
    elif mcap > 10e9:
        cap_factor = 0.5

    if matched_patterns:
        effective = [p for p in matched_patterns if p["confidence"] * cap_factor >= 50]
        high_count += len([p for p in effective if p["confidence"] >= 80])
        medium_count += len([p for p in effective if p["confidence"] < 80])
        if cap_factor < 0.5 and matched_patterns:
            for p in matched_patterns:
                p["note"] = "大型株のため株価への影響は限定的"

    # --- テーマ検出 ---
    themes_detected = detect_themes(all_text, name, code)
    if themes_detected:
        medium_count += 1  # テーマにマッチ → インパクト加点

    # --- バイオ パイプライン検出 ---
    pipeline = detect_bio_pipeline(all_text, name, config)

    # パイプライン情報があればインパクト加点
    if pipeline.get("has_pipeline_info"):
        if pipeline.get("trial_positive"):
            high_count += 1
        if pipeline.get("trial_negative"):
            negative_count += 1

    # --- インパクトスコア ---
    scoring = config.get("scoring", {})
    hw = scoring.get("high_impact_weight", 25)
    mw = scoring.get("medium_impact_weight", 10)
    nw = scoring.get("negative_impact_weight", 20)
    impact_score = min(scoring.get("max_score", 100), high_count * hw + medium_count * mw - negative_count * nw)
    impact_score = max(scoring.get("min_score", 0), impact_score)

    # --- 特色判定 ---
    has_story = (
        (high_count > 0 or medium_count >= 2 or len(positive) > 0
         or len(themes_detected) > 0 or pipeline.get("has_pipeline_info"))
        and negative_count < 2
    )

    # トリガー抽出
    triggers = []
    for p in positive[:3]:
        triggers.append(p.get("title", "")[:50])
    # テーマのカタリストもトリガーに
    for t in themes_detected[:2]:
        for cat in t.get("catalysts", [])[:2]:
            triggers.append(f"[テーマ] {cat}")

    # リスク
    risks = []
    if dilution.get("has_risk"):
        risks.append("ワラント/増資リスク検出")
    neg_kws = config.get("negative_impact", [])
    for kw in neg_kws:
        if kw in all_text:
            risks.append(f"直近IRに「{kw}」")
            break
    if pipeline.get("trial_negative"):
        risks.append(f"試験ネガティブ: {', '.join(pipeline['trial_negative'][:3])}")

    # 下落理由分析
    decline = {"reason": "unknown", "score": 0, "note": ""}
    try:
        if df is not None and not df.empty:
            decline = analyze_decline_reason(df, current_price)
        else:
            from src.data.price import fetch_price
            _df = fetch_price(code, period_days=180)
            if _df is not None and not _df.empty:
                decline = analyze_decline_reason(_df, current_price)
    except Exception:
        pass

    if decline.get("reason") == "fundamental" and negative_count > 0:
        has_story = False

    # --- シナリオ構築 ---
    scenario_parts = []

    # YAMLのscenario_labelsからマッチング
    labels = config.get("scenario_labels", [])
    for title in all_titles[:8]:
        for entry in labels:
            kw_list = entry.get("keywords", [])
            label = entry.get("label", "")
            if any(kw in title for kw in kw_list):
                scenario_parts.append(f"{label}（{title[:40]}）")
                break

    # テーマ検出結果（成熟度付き）
    maturity_labels = {
        "confirmed": "実需あり",
        "emerging": "初期段階・ウォッチ",
        "hype": "期待先行・慎重に",
    }
    for t in themes_detected[:3]:
        kw_str = ", ".join(t["matched_keywords"][:3])
        mat = t.get("maturity", "hype")
        mat_label = maturity_labels.get(mat, "不明")
        scenario_parts.append(f"テーマ: {t['theme_name']}（{mat_label}）（キーワード: {kw_str}）")
        # 成熟度の根拠
        detail = t.get("maturity_detail", {})
        if detail.get("confirmed_hits"):
            scenario_parts.append(f"  本物シグナル: {', '.join(detail['confirmed_hits'][:3])}")
        if detail.get("emerging_hits"):
            scenario_parts.append(f"  初期シグナル: {', '.join(detail['emerging_hits'][:3])}")
        if mat == "hype" and detail.get("hype_hits"):
            scenario_parts.append(f"  ⚠ 期待先行: {', '.join(detail['hype_hits'][:3])}")
        # カタリスト発火チェック
        for cat in t.get("catalysts", [])[:2]:
            if cat in all_text:
                scenario_parts.append(f"  カタリスト発火中: {cat}")

    # パイプライン情報
    if pipeline.get("has_pipeline_info"):
        parts = []
        if pipeline.get("highest_phase"):
            parts.append(f"フェーズ: {pipeline['highest_phase']}")
        if pipeline.get("diseases"):
            parts.append(f"対象: {', '.join(pipeline['diseases'][:3])}")
        if pipeline.get("trial_positive"):
            parts.append(f"ポジティブ: {', '.join(pipeline['trial_positive'][:2])}")
        if pipeline.get("trial_negative"):
            parts.append(f"⚠ ネガティブ: {', '.join(pipeline['trial_negative'][:2])}")
        if parts:
            scenario_parts.append(f"パイプライン: {' / '.join(parts)}")

    # セクターパターン
    for mp in matched_patterns[:2]:
        scenario_parts.append(f"セクターパターン: {mp['pattern']}（確信度{mp['confidence']}%、典型{mp.get('typical_move', '')}）")
        if mp.get("high_conviction_if"):
            scenario_parts.append(f"  確度UP条件: {' / '.join(mp['high_conviction_if'][:3])}")
        if mp.get("low_conviction_if"):
            scenario_parts.append(f"  ⚠ 確度DOWN条件: {' / '.join(mp['low_conviction_if'][:3])}")
        if mp.get("trap"):
            scenario_parts.append(f"  罠: {mp['trap'][:80]}")
        if mp.get("exit_strategy"):
            exit_line = str(mp['exit_strategy']).strip().split('\n')[0]
            scenario_parts.append(f"  出口: {exit_line}")

    # ゴール判定（全セクター共通）
    goal_near_kws = config.get("goal_near_keywords", [])
    hype_kws = config.get("hype_keywords", [])
    has_hype = any(kw in all_text for kw in hype_kws)
    goal_near = any(kw in all_text for kw in goal_near_kws)

    if has_hype and matched_patterns:
        if goal_near:
            scenario_parts.append("出口判断: ゴール（商用化/上市等）のタイムラインが見える → 恩株化して保有継続も合理的")
        else:
            scenario_parts.append("出口判断: ゴール不透明 → 期待値で上がった時点で全売り（複利優先。不透明な株に資金を寝かせない）")

    scenario = "\n".join(scenario_parts) if scenario_parts else ""

    # Ollamaで補強（LLMが使えてキーワードで何も出なかった場合のみ）
    if is_available() and all_titles and not scenario_parts:
        titles_text = "\n".join(all_titles[:8])
        whale_info = ""
        if structure:
            w = structure.get("whale", {})
            if w.get("detected"):
                whale_info = f"\n大口推定: {w['shares']:,}株保有、コスト¥{w['cost']:,}、{w['position']}"

        prompt = f"""以下は{name}({code})のIR/ニュースタイトルです。

{titles_text}
{whale_info}
現在株価: ¥{current_price:,.0f}

以下の形式で簡潔に回答してください（日本語、各項目1-2文）:

1. 企業の特色と強み
2. 直近IRで最もインパクトがある事実
3. 業界固有の「ほぼ確実」パターンがあるか
4. 株価が低い理由の推定
5. トリガー（何が起きたら株価が動くか）
6. シナリオ（トリガー成功時の展開）
7. リスク

ルール:
- IRに書かれている事実のみを使う。推測禁止
- 特色が見当たらない場合は「特色なし。投資対象として弱い」と明記
- 業界の一般的な統計は事実として使ってよい"""

        result = generate(prompt, temperature=0.2)
        if result:
            scenario = result

    # LLMなしの場合の簡易シナリオ
    if not scenario:
        high_kws = config.get("high_impact", [])
        medium_kws = config.get("medium_impact", [])
        if high_count > 0:
            scenario = f"直近IRに高インパクト情報あり（{', '.join(kw for kw in high_kws if kw in all_text)}）。"
        elif medium_count > 0:
            scenario = f"業績好調の兆候あり（{', '.join(kw for kw in medium_kws if kw in all_text)}）。"
        else:
            scenario = "直近IRに顕著なインパクトなし。"

    # IR要約
    ir_summary = []
    for n in news[:8]:
        ir_summary.append(f"{n.get('date', '')}: {n.get('title', '')}")

    return {
        "has_story": has_story,
        "impact_score": impact_score,
        "scenario": scenario,
        "triggers": triggers,
        "risks": risks,
        "ir_summary": ir_summary,
        "positive_count": high_count + medium_count,
        "negative_count": negative_count,
        "decline_reason": decline.get("reason", "unknown"),
        "decline_note": decline.get("note", ""),
        "decline_score": decline.get("score", 0),
        "matched_patterns": matched_patterns,
        "themes_detected": [t["theme_name"] for t in themes_detected],
        "pipeline": pipeline if pipeline.get("has_pipeline_info") else {},
        # deep_analyzeで再利用するための生データ
        "_news": news,
        "_disclosures": disclosures,
        "_positive_catalysts": positive,
        "_dilution": dilution,
    }
