"""信用残データ取得モジュール

信用倍率、信用買残/売残、貸借倍率を取得する。
データソース: 株探 (kabutan.jp)
"""

import requests
from bs4 import BeautifulSoup
import re


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def _parse_number(text: str) -> float:
    """日本語数値表記をfloatに変換する。"""
    if not text:
        return 0
    text = text.strip().replace(",", "").replace("株", "").replace("倍", "")
    text = text.replace("―", "0").replace("-", "0").replace("–", "0")
    try:
        return float(text)
    except ValueError:
        return 0


def fetch_margin_data(code: str) -> dict:
    """銘柄の信用残データを取得する。

    Returns:
        {
            "margin_buy": 信用買残（株数）,
            "margin_sell": 信用売残（株数）,
            "margin_ratio": 信用倍率（買残/売残）,
            "margin_buy_change": 信用買残の前週比,
            "margin_sell_change": 信用売残の前週比,
            "margin_buy_avg_cost": 信用買いの推定平均コスト,
            "is_heavy": 上値が重いかの判定,
            "heaviness_reason": 重い理由,
        }
    """
    result = {
        "margin_buy": 0,
        "margin_sell": 0,
        "margin_ratio": 0,
        "margin_buy_change": 0,
        "margin_sell_change": 0,
        "margin_buy_avg_cost": 0,
        "is_heavy": False,
        "heaviness_reason": "",
        "margin_trend": "",  # increasing / decreasing / stable
        "weeks_data": [],    # 直近数週の推移
    }

    try:
        # 信用残ページ（推移付き）
        url = f"https://kabutan.jp/stock/kabuka?code={code}&ashi=shin"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 信用残テーブルを探す
        tables = soup.find_all("table")
        for table in tables:
            text = table.get_text()
            if "信用買残" in text or "信用売残" in text:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    if len(cells) >= 2:
                        label = cells[0].get_text(strip=True)
                        value = cells[1].get_text(strip=True)

                        if "信用買残" in label and "比" not in label:
                            result["margin_buy"] = _parse_number(value)
                            if len(cells) >= 3:
                                result["margin_buy_change"] = _parse_number(cells[2].get_text(strip=True))
                        elif "信用売残" in label and "比" not in label:
                            result["margin_sell"] = _parse_number(value)
                            if len(cells) >= 3:
                                result["margin_sell_change"] = _parse_number(cells[2].get_text(strip=True))
                        elif "信用倍率" in label or "貸借倍率" in label:
                            result["margin_ratio"] = _parse_number(value)
                break

        # 倍率が取れなかった場合、手動計算
        if result["margin_ratio"] == 0 and result["margin_sell"] > 0:
            result["margin_ratio"] = round(result["margin_buy"] / result["margin_sell"], 2)

    except Exception:
        pass

    # 信用残トレンド判定
    buy_change = result["margin_buy_change"]
    if buy_change < 0:
        result["margin_trend"] = "decreasing"  # 買残減少 = 需給改善
    elif buy_change > 0:
        result["margin_trend"] = "increasing"  # 買残増加 = 上値重く
    else:
        result["margin_trend"] = "stable"

    # 上値の重さ判定
    reasons = []
    ratio = result["margin_ratio"]
    if ratio > 5:
        reasons.append(f"信用倍率{ratio:.1f}倍（極めて重い。上値に大量の売り圧力）")
    elif ratio > 3:
        reasons.append(f"信用倍率{ratio:.1f}倍（重い）")
    elif 0 < ratio < 1:
        reasons.append(f"信用倍率{ratio:.1f}倍（売り長。踏み上げの可能性）")

    if buy_change > 0:
        reasons.append("信用買残が増加中（将来の売り圧力）")
    elif buy_change < 0:
        reasons.append("信用買残が減少中（需給改善）")

    result["is_heavy"] = ratio > 3
    result["heaviness_reason"] = " / ".join(reasons)

    return result


def calc_margin_pressure(margin_data: dict, current_price: float, volume_avg: float) -> dict:
    """信用残から上値圧力を数値化する。

    Returns:
        {
            "pressure_score": 圧力スコア (0-100, 高いほど上値重い),
            "days_to_unwind": 信用買残の解消に必要な日数,
            "squeeze_potential": 踏み上げポテンシャル (売残が多い場合),
        }
    """
    margin_buy = margin_data.get("margin_buy", 0)
    margin_sell = margin_data.get("margin_sell", 0)
    ratio = margin_data.get("margin_ratio", 0)

    # 信用買残の解消日数 = 買残 / 1日平均出来高
    if volume_avg > 0:
        days_to_unwind = margin_buy / volume_avg
    else:
        days_to_unwind = 0

    # 圧力スコア
    pressure = 0
    if ratio > 5:
        pressure += 40
    elif ratio > 3:
        pressure += 25
    elif ratio > 2:
        pressure += 10

    if days_to_unwind > 20:
        pressure += 30
    elif days_to_unwind > 10:
        pressure += 15

    if margin_data.get("margin_buy_change", 0) > 0:
        pressure += 15

    pressure = min(100, pressure)

    # 踏み上げポテンシャル（売残が多い場合）
    squeeze = 0
    if ratio > 0 and ratio < 1:
        # 売残 > 買残 = 踏み上げの可能性
        squeeze = min(100, (1 / ratio - 1) * 50)

    return {
        "pressure_score": round(pressure),
        "days_to_unwind": round(days_to_unwind, 1),
        "squeeze_potential": round(squeeze),
    }


def analyze_margin_trend(df, margin_data: dict) -> dict:
    """信用残の変化トレンドから需給の改善/悪化を判定する。

    Returns:
        {
            "trend": "improving" | "worsening" | "neutral",
            "description": 説明,
            "margin_call_zone": 追証推定ライン,
            "is_converting": 信用→現物への乗り換え中か,
        }
    """
    close = df["Close"]
    current = float(close.iloc[-1])

    margin_buy = margin_data.get("margin_buy", 0)
    margin_buy_change = margin_data.get("margin_buy_change", 0)
    margin_sell = margin_data.get("margin_sell", 0)

    # トレンド判定
    trend = "neutral"
    desc_parts = []

    if margin_buy_change < 0:
        # 信用買残が減少
        recent_return = float(close.pct_change(5).iloc[-1]) if len(close) > 5 else 0
        if recent_return > -0.03:
            # 株価横ばい〜微増で信用買残減少 → 現物への乗り換え
            trend = "improving"
            desc_parts.append("信用買残減少+株価安定（現物乗り換え=需給改善）")
        else:
            # 株価下落で信用買残減少 → 投げ売り（追証の可能性）
            desc_parts.append("信用買残減少+株価下落（投げ売り=底打ち接近の可能性）")
    elif margin_buy_change > 0:
        trend = "worsening"
        desc_parts.append("信用買残増加中（将来の売り圧力）")

    if margin_sell > margin_buy and margin_sell > 0:
        trend = "improving"
        desc_parts.append("売り長（踏み上げの燃料）")

    # 追証推定ライン
    # 信用買いの平均コスト ≒ 直近数週間の出来高加重平均価格
    # 追証 ≒ 平均コストの-30%（証拠金維持率30%割れ）
    if len(close) >= 20:
        volume = df["Volume"]
        recent_close = close.tail(20)
        recent_vol = volume.tail(20)
        total_vol = float(recent_vol.sum())
        if total_vol > 0:
            vwap = float((recent_close * recent_vol).sum() / total_vol)
            margin_call_zone = round(vwap * 0.70)  # 平均コストの-30%
        else:
            margin_call_zone = round(current * 0.70)
    else:
        margin_call_zone = round(current * 0.70)

    # 現在値が追証ゾーンに近い → 投げ売りが起きやすい
    if current < margin_call_zone * 1.1:
        desc_parts.append(f"追証ライン¥{margin_call_zone:,}に接近（投げ売り注意/底打ちチャンス）")

    # 信用→現物への乗り換え判定
    is_converting = margin_buy_change < 0 and float(close.pct_change(5).iloc[-1] if len(close) > 5 else 0) > -0.03

    return {
        "trend": trend,
        "description": " / ".join(desc_parts) if desc_parts else "信用残に特異な動きなし",
        "margin_call_zone": margin_call_zone,
        "is_converting": is_converting,
    }
