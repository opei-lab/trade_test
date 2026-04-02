"""ステージ変化検出モジュール

IRや財務データから「事実としてステージが変わった」のに
市場が反応していない銘柄を検出する。

同時にワラント・希薄化等のネガティブ変化も検出する。
"""

import yfinance as yf
from src.data.price import to_yf_ticker


def detect_financial_stage_change(code: str) -> dict:
    """財務データからステージ変化を検出する。

    yfinanceから取得可能な財務データを使い、
    「数値として確定しているステージ変化」を抽出する。

    Returns:
        {
            "changes": [検出されたステージ変化のリスト],
            "risks": [検出されたリスクのリスト],
            "stage_score": ステージ変化スコア (-100〜+100),
            "market_gap": 市場の織り込み度合い,
        }
    """
    ticker = yf.Ticker(to_yf_ticker(code))
    info = ticker.info

    changes = []
    risks = []
    stage_score = 0

    # === ポジティブなステージ変化 ===

    # 1. 売上急成長
    revenue_growth = info.get("revenueGrowth", 0)
    if revenue_growth and revenue_growth > 0.5:  # 50%以上の成長
        changes.append({
            "type": "revenue_surge",
            "description": f"売上成長率{revenue_growth*100:.0f}%（急成長）",
            "impact": "high",
            "value": revenue_growth,
        })
        stage_score += 30
    elif revenue_growth and revenue_growth > 0.2:
        changes.append({
            "type": "revenue_growth",
            "description": f"売上成長率{revenue_growth*100:.0f}%（好調）",
            "impact": "medium",
            "value": revenue_growth,
        })
        stage_score += 15

    # 2. 利益率の改善 / 黒字転換
    profit_margin = info.get("profitMargins", 0)
    prev_margin = info.get("operatingMargins", 0)  # 近似として営業利益率を使用
    if profit_margin and profit_margin > 0 and prev_margin is not None:
        if prev_margin < 0:
            changes.append({
                "type": "turnaround",
                "description": f"黒字転換（利益率{profit_margin*100:.1f}%）",
                "impact": "high",
                "value": profit_margin,
            })
            stage_score += 35
        elif profit_margin > 0.15:
            changes.append({
                "type": "high_margin",
                "description": f"高利益率{profit_margin*100:.1f}%",
                "impact": "medium",
                "value": profit_margin,
            })
            stage_score += 10

    # 3. 利益の急成長
    earnings_growth = info.get("earningsGrowth", 0)
    if earnings_growth and earnings_growth > 1.0:  # 100%以上
        changes.append({
            "type": "earnings_surge",
            "description": f"利益成長率{earnings_growth*100:.0f}%（爆発的成長）",
            "impact": "high",
            "value": earnings_growth,
        })
        stage_score += 25
    elif earnings_growth and earnings_growth > 0.3:
        changes.append({
            "type": "earnings_growth",
            "description": f"利益成長率{earnings_growth*100:.0f}%",
            "impact": "medium",
            "value": earnings_growth,
        })
        stage_score += 10

    # 4. 市場の織り込み度合い（PER/PEGで判定）
    forward_pe = info.get("forwardPE", 0)
    trailing_pe = info.get("trailingPE", 0)
    peg = info.get("pegRatio", 0)

    if forward_pe and trailing_pe and forward_pe > 0:
        if forward_pe < trailing_pe * 0.6:
            # Forward PEが大幅に低い = 将来の成長を市場が織り込んでいない
            changes.append({
                "type": "underpriced_growth",
                "description": f"Forward PE({forward_pe:.0f}) << Trailing PE({trailing_pe:.0f})、成長未織り込み",
                "impact": "high",
                "value": trailing_pe / forward_pe,
            })
            stage_score += 20

    if peg and 0 < peg < 0.5:
        changes.append({
            "type": "low_peg",
            "description": f"PEG Ratio {peg:.2f}（成長に対して割安）",
            "impact": "high",
            "value": peg,
        })
        stage_score += 15

    # 5. フリーキャッシュフローの改善
    fcf = info.get("freeCashflow", 0)
    market_cap = info.get("marketCap", 0)
    if fcf and market_cap and fcf > 0 and market_cap > 0:
        fcf_yield = fcf / market_cap
        if fcf_yield > 0.1:  # FCF利回り10%超
            changes.append({
                "type": "high_fcf",
                "description": f"FCF利回り{fcf_yield*100:.1f}%（キャッシュリッチ）",
                "impact": "medium",
                "value": fcf_yield,
            })
            stage_score += 10

    # === ネガティブなステージ変化（リスク） ===

    # 1. 希薄化リスク（発行済株式数の変化）
    shares = info.get("sharesOutstanding", 0)
    float_shares = info.get("floatShares", 0)
    if shares and float_shares:
        # 浮動株比率が異常に高い = 過去に大量発行している可能性
        if float_shares / shares > 0.9:
            risks.append({
                "type": "high_float",
                "description": "浮動株比率90%超（大量発行の形跡）",
                "severity": "medium",
            })
            stage_score -= 10

    # 2. 負債リスク
    debt_equity = info.get("debtToEquity", 0)
    if debt_equity and debt_equity > 200:
        risks.append({
            "type": "high_debt",
            "description": f"D/E Ratio {debt_equity:.0f}%（過剰負債）",
            "severity": "high",
        })
        stage_score -= 20
    elif debt_equity and debt_equity > 100:
        risks.append({
            "type": "moderate_debt",
            "description": f"D/E Ratio {debt_equity:.0f}%",
            "severity": "medium",
        })
        stage_score -= 5

    # 3. 継続的な赤字 + キャッシュ減少
    if profit_margin and profit_margin < -0.2:  # 赤字率20%超
        if fcf and fcf < 0:
            risks.append({
                "type": "cash_burn",
                "description": f"赤字(利益率{profit_margin*100:.0f}%) + FCF赤字（資金燃焼中）",
                "severity": "high",
            })
            stage_score -= 25

    # 4. 株価が52週高値付近（高値掴みリスク）
    week52_high = info.get("fiftyTwoWeekHigh", 0)
    current = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0)
    if week52_high and current and week52_high > 0:
        pct_from_high = (week52_high - current) / week52_high * 100
        if pct_from_high < 5:
            risks.append({
                "type": "near_52w_high",
                "description": "52週高値付近（高値掴みリスク）",
                "severity": "medium",
            })
            stage_score -= 10

    # === 市場ギャップの総合判定 ===
    market_gap = "none"
    if stage_score >= 40:
        market_gap = "large"  # 大きなギャップ = 最もチャンス
    elif stage_score >= 20:
        market_gap = "moderate"
    elif stage_score > 0:
        market_gap = "small"
    elif stage_score < -20:
        market_gap = "negative"  # ネガティブギャップ = 危険

    return {
        "changes": changes,
        "risks": risks,
        "stage_score": max(-100, min(100, stage_score)),
        "market_gap": market_gap,
    }


def format_stage_summary(result: dict) -> str:
    """ステージ変化を1行サマリーにする。"""
    parts = []

    for c in result["changes"]:
        if c["impact"] == "high":
            parts.append(c["description"])

    for r in result["risks"]:
        if r["severity"] == "high":
            parts.append(f"[警戒] {r['description']}")

    if not parts:
        return "特筆すべきステージ変化なし"

    return " / ".join(parts)
