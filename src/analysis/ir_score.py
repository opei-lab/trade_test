"""IR/ニュース独立評価モジュール

IR/適時開示の質を0-100の独立スコアで評価する。
「まぁまぁ」は低く、「尖ってる」は高く。

評価軸:
- インパクトの大きさ（上方修正5% vs 50%は別物）
- 鮮度（直近のIRほど価値が高い）
- カタリストの近さ（3ヶ月後 vs 2年後）
- ネガティブの深刻度（5%増資 vs 50%増資）
"""

from datetime import date, datetime
import re


def calc_ir_score(news: list[dict], disclosures: list[dict],
                  scenario: dict = None) -> dict:
    """IR/ニュースの独立スコア（0-100）を算出する。

    Args:
        news: fetch_kabutan_newsの結果
        disclosures: fetch_recent_disclosuresの結果
        scenario: build_scenarioの結果（あれば補強に使う）

    Returns:
        {
            "ir_score": 0-100（尖ってると高い）,
            "ir_grade": "S"/"A"/"B"/"C"/"D",
            "ir_reasons": [理由リスト],
            "ir_negative": [リスクリスト],
            "freshness": 0-100（直近IRの鮮度）,
            "catalyst_proximity": 0-100（カタリストの近さ）,
        }
    """
    score = 0
    reasons = []
    negatives = []

    all_titles = [n.get("title", "") for n in news] + [d.get("title", "") for d in disclosures]
    all_text = " ".join(all_titles)

    # === 1. 高インパクトIR（株価への影響度に比例した加点）===
    # IRは単独で株価を数倍にする力がある。ファンダと同列ではなく独立した爆発力

    # 上方修正（倍率で加点が大きく変わる）
    for title in all_titles:
        if "上方修正" in title:
            pct_match = re.search(r'(\d+)[%％]', title)
            if pct_match:
                pct = int(pct_match.group(1))
                if pct >= 50:
                    score += 70  # 50%以上 = ゲームチェンジャー。株価2倍もあり
                    reasons.append(f"上方修正{pct}%（大幅。株価直結）")
                elif pct >= 20:
                    score += 45
                    reasons.append(f"上方修正{pct}%")
                else:
                    score += 25
                    reasons.append(f"上方修正{pct}%（小幅）")
            else:
                score += 35
                reasons.append("上方修正")
            break

    # 導出/ライセンス（バイオ最強。株価5-20倍の可能性）
    if any(kw in all_text for kw in ["導出", "ライセンス契約", "ライセンスアウト"]):
        score += 80
        reasons.append("導出/ライセンス契約（バイオ最強。株価数倍の可能性）")

    # 承認/認可（薬事承認。リスク大幅低減）
    if any(kw in all_text for kw in ["承認取得", "承認を取得", "認可"]):
        score += 65
        reasons.append("承認取得（リスク大幅低減）")

    # 黒字転換（ステージ変化。市場の見方が変わる）
    if any("黒字" in t and "赤字" not in t for t in all_titles):
        score += 55
        reasons.append("黒字転換（ステージ変化）")

    # 最高益
    if any("最高益" in t for t in all_titles):
        score += 45
        reasons.append("最高益更新")

    # 大型提携/資本提携
    if any(kw in all_text for kw in ["資本提携", "資本業務提携"]):
        score += 45
        reasons.append("資本提携")
    elif any("提携" in t for t in all_titles):
        score += 20
        reasons.append("業務提携")

    # 自社株買い（需給改善）
    if any(kw in all_text for kw in ["自社株買", "自己株式取得"]):
        score += 30
        reasons.append("自社株買い（需給改善）")

    # 大型受注/契約
    if any(kw in all_text for kw in ["大型受注", "大口受注"]):
        score += 25
        reasons.append("大型受注")
    elif any("受注" in t or "契約" in t for t in all_titles):
        score += 10
        reasons.append("受注/契約")

    # 増収増益（確定した業績改善）
    if any("増収増益" in t for t in all_titles):
        score += 15
        reasons.append("増収増益")
    elif any("増収" in t and "減益" not in t for t in all_titles):
        score += 8
        reasons.append("増収")

    # 増益（増収増益でなくても増益単体）
    if any("増益" in t and "減収" not in t and "増収増益" not in t for t in all_titles):
        score += 12
        reasons.append("増益")

    # 増配
    if any("増配" in t for t in all_titles):
        score += 12
        reasons.append("増配")

    # 採用/導入（大企業に採用された）
    if any("採用" in t for t in all_titles):
        score += 10
        reasons.append("サービス/製品採用")

    # 好調/堅調
    if any(kw in all_text for kw in ["好調", "堅調", "順調"]):
        score += 5
        reasons.append("業績好調")

    # === 2. ネガティブIR（深刻度で減点幅が変わる）===

    # ワラント/増資
    dilution_titles = [t for t in all_titles if any(kw in t for kw in ["ワラント", "新株予約権", "公募増資", "売出"])]
    if dilution_titles:
        # 複数回の希薄化は致命的
        if len(dilution_titles) >= 2:
            score -= 30
            negatives.append(f"希薄化IR {len(dilution_titles)}件（致命的）")
        else:
            score -= 15
            negatives.append("希薄化リスク（ワラント/増資）")

    # 下方修正
    if any("下方修正" in t for t in all_titles):
        score -= 20
        negatives.append("下方修正")

    # 減損/特損
    if any(kw in all_text for kw in ["減損", "特別損失"]):
        score -= 10
        negatives.append("減損/特別損失")

    # 赤字拡大
    if "赤字拡大" in all_text or "赤字幅拡大" in all_text:
        score -= 15
        negatives.append("赤字拡大")

    # === 3. 鮮度（直近のIRほど価値が高い）===
    freshness = _calc_freshness(news + disclosures)

    # 鮮度ボーナス: 直近1週間に高インパクトIRがあれば加点
    if freshness >= 80 and score > 0:
        score += 10
        reasons.append("直近IR（鮮度高）")

    # === 4. scenarioからの補強 ===
    if scenario:
        # テーマ検出
        themes = scenario.get("themes_detected", [])
        if themes:
            score += min(15, len(themes) * 5)
            reasons.append(f"テーマ: {', '.join(themes[:2])}")

        # パイプライン
        pipeline = scenario.get("pipeline", {})
        if pipeline.get("has_pipeline_info"):
            phase = pipeline.get("highest_phase", "")
            if pipeline.get("trial_positive"):
                score += 20
                reasons.append(f"試験ポジティブ（{phase}）")
            elif phase in ("Phase 3", "Phase 2"):
                score += 10
                reasons.append(f"パイプライン進行中（{phase}）")

        # セクターパターン
        patterns = scenario.get("matched_patterns", [])
        for p in patterns[:1]:
            if p.get("confidence", 0) >= 80:
                score += 15
                reasons.append(f"セクターパターン: {p.get('pattern', '')}（確信度{p['confidence']}%）")

    # === 5. カタリスト接近度 ===
    catalyst_prox = 50  # デフォルト
    if scenario:
        # IRに具体的な日付やイベント言及があるか
        for title in all_titles[:5]:
            if any(kw in title for kw in ["ASCO", "ASH", "AACR", "FDA", "PMDA", "決算"]):
                catalyst_prox = 80
                break
            if any(kw in title for kw in ["量産開始", "商用化", "上市", "発売"]):
                catalyst_prox = 90
                break

    # === スコア確定 ===
    score = max(0, min(100, score))

    # グレード判定（インパクトの大きさで分類）
    if score >= 70:
        grade = "S"  # ゲームチェンジャー（導出、大幅上方修正等）
    elif score >= 45:
        grade = "A"  # 強い（承認、黒字転換、最高益等）
    elif score >= 25:
        grade = "B"  # 中程度（提携、受注、増益等）
    elif score >= 10:
        grade = "C"  # 弱い（好調、採用等）
    else:
        grade = "D"  # 特になし

    return {
        "ir_score": score,
        "ir_grade": grade,
        "ir_reasons": reasons,
        "ir_negative": negatives,
        "freshness": freshness,
        "catalyst_proximity": catalyst_prox,
    }


def _calc_freshness(items: list[dict]) -> int:
    """IRの鮮度を0-100で算出する。直近ほど高い。"""
    if not items:
        return 0

    today = date.today()
    best_freshness = 0

    for item in items[:10]:
        date_str = item.get("date", "")
        if not date_str:
            continue

        try:
            # "26/03/27 12:38" のような形式
            clean = date_str.split("\xa0")[0].strip()
            if "/" in clean and len(clean) <= 8:
                parts = clean.split("/")
                if len(parts) == 3:
                    y = int(parts[0]) + 2000
                    m = int(parts[1])
                    d = int(parts[2])
                    ir_date = date(y, m, d)
                    days_ago = (today - ir_date).days

                    if days_ago <= 3:
                        freshness = 100
                    elif days_ago <= 7:
                        freshness = 80
                    elif days_ago <= 14:
                        freshness = 60
                    elif days_ago <= 30:
                        freshness = 40
                    else:
                        freshness = 20

                    best_freshness = max(best_freshness, freshness)
        except Exception:
            continue

    return best_freshness
