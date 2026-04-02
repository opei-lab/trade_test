"""Stage 3: シナリオ構築モジュール

IR/ニュースの事実からインパクトを評価し、
銘柄ごとのストーリーを構築する。
特色がない銘柄を除外し、トリガーが明確な銘柄だけを残す。

Ollamaが動いている場合はLLMでシナリオ生成。
動いていない場合はキーワードベースで簡易判定。
"""

from src.llm.client import is_available, generate
from src.llm.news_analyzer import fetch_kabutan_news
from src.data.tdnet import fetch_recent_disclosures, detect_positive_catalysts, detect_dilution_risk
import numpy as np


def match_sector_patterns(text: str, name: str = "") -> list:
    """IRテキストからセクター別の勝ち確パターンにマッチするか判定。"""
    import yaml
    from pathlib import Path

    config_path = Path(__file__).parent.parent / "config" / "sector_patterns.yaml"
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
        # このセクターに該当するか
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
                })

    return matched


def analyze_decline_reason(df, current: float) -> dict:
    """株価が底値圏にいる理由を分析する。

    a. ファンダ悪化 → 本当に弱い（除外すべき）
    b. 市場全体の下落 → 銘柄固有ではない（戻る可能性）
    c. アルゴの売り叩き → 一時的（チャンス）
    d. 大口の意図的な下げ → 仕込みのため（最高のチャンス）
    """
    close = df["Close"]
    volume = df["Volume"]
    high = df["High"]
    low = df["Low"]

    # 直近30日の下落パターン
    if len(close) < 30:
        return {"reason": "unknown", "score": 0}

    ret_30d = (float(close.iloc[-1]) - float(close.iloc[-30])) / float(close.iloc[-30]) * 100
    if ret_30d > -5:
        return {"reason": "not_declining", "score": 0, "note": "下落していない"}

    # パターン判定
    recent_30 = df.tail(30)
    vol_trend = float(volume.tail(10).mean()) / float(volume.tail(30).mean())

    # CLV（引け際の売買方向）
    clv = ((close - low) - (high - close)) / (high - low + 1e-10)
    avg_clv = float(clv.tail(10).mean())

    # 大口判定
    # 出来高増+値幅小+CLV高 = 大口が下で拾っている
    vol_up = vol_trend > 1.2
    range_small = float(((recent_30["High"] - recent_30["Low"]) / recent_30["Close"]).tail(10).mean()) < 0.03
    clv_high = avg_clv > 0.1

    # OBV（出来高ベース）
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


def build_scenario(code: str, name: str, current_price: float, structure: dict = None) -> dict:
    """銘柄のシナリオを構築する。

    Returns:
        {
            "has_story": ストーリーがあるか（Falseなら除外候補）,
            "impact_score": インパクトスコア (0-100),
            "scenario": シナリオ文面,
            "triggers": トリガーイベント,
            "risks": リスク,
            "ir_summary": IR要約,
        }
    """
    # IR/ニュース取得
    news = []
    try:
        news = fetch_kabutan_news(code, max_items=10)
    except Exception:
        pass

    disclosures = []
    try:
        disclosures = fetch_recent_disclosures(code, max_items=15)
    except Exception:
        pass

    positive = []
    try:
        positive = detect_positive_catalysts(code)
    except Exception:
        pass

    dilution = {}
    try:
        dilution = detect_dilution_risk(code)
    except Exception:
        pass

    # キーワード分析
    all_titles = [n.get("title", "") for n in news] + [d.get("title", "") for d in disclosures]
    all_text = " ".join(all_titles)

    # インパクトキーワード
    high_impact = ["黒字", "上方修正", "最高益", "承認", "提携", "契約", "受注", "導出", "買収", "増配", "自社株買"]
    medium_impact = ["増収", "増益", "好調", "拡大", "成長", "新規", "開始", "発表"]
    negative_impact = ["下方修正", "赤字", "減損", "ワラント", "増資", "希薄化", "廃止", "減配"]

    high_count = sum(1 for kw in high_impact if kw in all_text)
    medium_count = sum(1 for kw in medium_impact if kw in all_text)
    negative_count = sum(1 for kw in negative_impact if kw in all_text)

    # セクター別勝ち確パターンのマッチング
    matched_patterns = match_sector_patterns(all_text, name)

    # パターンの効果は時価総額で減衰する
    # 小型（100億未満）: フルインパクト
    # 中型（100-1000億）: 半減
    # 大型（1000億超）: ほぼ無意味（株数が多すぎて動かない）
    mcap = 0
    if structure:
        whale = structure.get("whale", {})
        # infoがないので簡易推定: 現在値 × 推定発行株数
        # 正確な値はStage 2のinfo取得時に更新される

    cap_factor = 1.0  # デフォルト
    if mcap > 100e9:  # 1000億超
        cap_factor = 0.1  # ほぼ効かない
    elif mcap > 10e9:  # 100億超
        cap_factor = 0.5

    if matched_patterns:
        effective = [p for p in matched_patterns if p["confidence"] * cap_factor >= 50]
        high_count += len([p for p in effective if p["confidence"] >= 80])
        medium_count += len([p for p in effective if p["confidence"] < 80])

        # 大型株のパターンマッチは警告
        if cap_factor < 0.5 and matched_patterns:
            for p in matched_patterns:
                p["note"] = "大型株のため株価への影響は限定的"

    # インパクトスコア
    impact_score = min(100, high_count * 25 + medium_count * 10 - negative_count * 20)
    impact_score = max(0, impact_score)

    # 特色判定
    has_story = (high_count > 0 or medium_count >= 2 or len(positive) > 0) and negative_count < 2

    # トリガー抽出
    triggers = []
    for p in positive[:3]:
        triggers.append(p.get("title", "")[:50])

    # リスク
    risks = []
    if dilution.get("has_risk"):
        risks.append("ワラント/増資リスク検出")
    for kw in negative_impact:
        if kw in all_text:
            risks.append(f"直近IRに「{kw}」")
            break

    # 下落理由分析
    decline = {"reason": "unknown", "score": 0, "note": ""}
    try:
        from src.data.price import fetch_price
        _df = fetch_price(code, period_days=180)
        if _df is not None and not _df.empty:
            decline = analyze_decline_reason(_df, current_price)
    except Exception:
        pass

    # ファンダ悪化による下落は除外候補
    if decline.get("reason") == "fundamental" and negative_count > 0:
        has_story = False  # ストーリーなし → 除外

    # Ollamaでシナリオ生成
    scenario = ""
    if is_available() and all_titles:
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
   （例: 早期承認制度に選ばれた→承認率95%、ブロックバスター候補→導出ほぼ確実、等）
4. 株価が低い理由の推定（ファンダ悪化/市場全体/アルゴ売り/大口の意図的な下げ）
5. トリガー（何が起きたら株価が動くか。時期が分かれば記載）
6. シナリオ（トリガー成功時の展開。具体的な株価水準は不要）
7. リスク（ワラント、試験失敗、競合等）

ルール:
- IRに書かれている事実のみを使う。推測禁止
- 特色が見当たらない場合は「特色なし。投資対象として弱い」と明記
- 業界の一般的な統計（承認率等）は事実として使ってよい"""

        result = generate(prompt, temperature=0.2)
        if result:
            scenario = result

    # LLMなしの場合の簡易シナリオ
    if not scenario:
        if high_count > 0:
            scenario = f"直近IRに高インパクト情報あり（{', '.join(kw for kw in high_impact if kw in all_text)}）。"
        elif medium_count > 0:
            scenario = f"業績好調の兆候あり（{', '.join(kw for kw in medium_impact if kw in all_text)}）。"
        else:
            scenario = "直近IRに顕著なインパクトなし。"

    # IR要約
    ir_summary = []
    for n in news[:5]:
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
    }
