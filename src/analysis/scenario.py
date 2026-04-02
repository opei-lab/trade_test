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
1. この企業の特色（何が強みか）
2. 直近のIRで最もインパクトがある事実
3. 今後のトリガー（何が起きたら株価が動くか）
4. シナリオ（トリガーが成功した場合の展開）
5. リスク

ルール:
- IRに書かれている事実のみを使う
- 推測や「だろう」は禁止
- 特色が見当たらない場合は「特色なし」と回答"""

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
    }
