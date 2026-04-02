"""業種別バリュエーション分析モジュール

業種ごとに異なる指標・閾値で割安/割高を判定する。
バイオはパイプライン市場規模、SaaSはPSR、製造業はPBRなど。
"""

import yaml
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "sectors.yaml"


def load_sector_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def match_sector(sector: str, industry: str) -> tuple[str, dict]:
    """銘柄のsector/industryから適切な業種設定を返す。"""
    config = load_sector_config()
    combined = f"{sector} {industry}".lower()

    for key, cfg in config.items():
        if key == "default":
            continue
        match_keywords = cfg.get("match", [])
        for kw in match_keywords:
            if kw.lower() in combined:
                return key, cfg

    return "default", config["default"]


def assess_valuation(info: dict) -> dict:
    """銘柄情報から業種別バリュエーション判定を行う。

    Args:
        info: get_stock_info()の返り値

    Returns:
        {
            "sector_type": 業種分類キー,
            "primary_metric": 主要指標名,
            "assessment": "cheap" | "fair" | "expensive" | "unknown",
            "metric_value": 指標の実測値,
            "benchmark": 基準値の説明,
            "notes": 業種固有の注意点,
            "blockbuster_potential": ブロックバスター判定(healthcare only),
        }
    """
    sector = info.get("sector", "")
    industry = info.get("industry", "")
    sector_type, cfg = match_sector(sector, industry)

    result = {
        "sector_type": sector_type,
        "primary_metric": cfg.get("primary_metric", "per"),
        "assessment": "unknown",
        "metric_value": None,
        "benchmark": "",
        "notes": cfg.get("notes", ""),
        "blockbuster_potential": None,
    }

    approach = cfg.get("valuation_approach", "per_standard")

    if approach == "target_market_size":
        # バイオ/ヘルスケア: パイプライン市場規模 vs 時価総額
        market_cap = info.get("market_cap", 0)
        result["metric_value"] = market_cap
        result["benchmark"] = "パイプラインの対象市場規模と比較して判定"
        # 時価総額が小さいほど上昇余地が大きい
        if market_cap > 0 and market_cap < 50e9:  # 500億未満
            result["assessment"] = "cheap"
        elif market_cap < 200e9:
            result["assessment"] = "fair"
        else:
            result["assessment"] = "expensive"

    elif approach == "psr_vs_growth_rate":
        # SaaS/IT: PSRベース
        market_cap = info.get("market_cap", 0)
        revenue = info.get("totalRevenue", 0) or info.get("revenue", 0)
        if revenue and revenue > 0:
            psr = market_cap / revenue
            result["metric_value"] = round(psr, 1)
            cheap = cfg.get("cheap_psr", 3)
            expensive = cfg.get("expensive_psr", 15)
            if psr < cheap:
                result["assessment"] = "cheap"
            elif psr > expensive:
                result["assessment"] = "expensive"
            else:
                result["assessment"] = "fair"
            result["benchmark"] = f"PSR: 割安<{cheap}, 割高>{expensive}"

    elif approach in ("pbr_and_roe", "pbr_roe_correlation"):
        # 製造業/金融: PBRベース
        pbr = info.get("priceToBook", 0)
        if pbr and pbr > 0:
            result["metric_value"] = round(pbr, 2)
            cheap = cfg.get("cheap_pbr", 0.8)
            expensive = cfg.get("expensive_pbr", 2.5)
            if pbr < cheap:
                result["assessment"] = "cheap"
            elif pbr > expensive:
                result["assessment"] = "expensive"
            else:
                result["assessment"] = "fair"
            result["benchmark"] = f"PBR: 割安<{cheap}, 割高>{expensive}"

    elif approach == "per_standard" or approach == "cycle_normalized_per":
        # PERベース
        per = info.get("trailingPE", 0) or info.get("forwardPE", 0)
        if per and per > 0:
            result["metric_value"] = round(per, 1)
            cheap = cfg.get("cheap_per", 10)
            expensive = cfg.get("expensive_per", 25)
            if per < cheap:
                result["assessment"] = "cheap"
            elif per > expensive:
                result["assessment"] = "expensive"
            else:
                result["assessment"] = "fair"
            result["benchmark"] = f"PER: 割安<{cheap}, 割高>{expensive}"

    elif approach == "dividend_yield":
        # 配当利回りベース
        div_yield = info.get("dividendYield", 0)
        if div_yield and div_yield > 0:
            div_pct = div_yield * 100
            result["metric_value"] = round(div_pct, 2)
            high = cfg.get("high_yield", 4.0)
            low = cfg.get("low_yield", 1.5)
            if div_pct > high:
                result["assessment"] = "cheap"
            elif div_pct < low:
                result["assessment"] = "expensive"
            else:
                result["assessment"] = "fair"
            result["benchmark"] = f"配当利回り: 高利回り>{high}%, 低利回り<{low}%"

    return result


def calc_market_size_gap(market_cap: float, target_market_size: float, capture_rate: float = 0.05) -> dict:
    """市場規模と時価総額のギャップからポテンシャル倍率を算出する。

    Args:
        market_cap: 現在の時価総額（円）
        target_market_size: 対象市場規模（円）
        capture_rate: 想定市場シェア（デフォルト5%）

    Returns:
        {
            "potential_revenue": 想定売上,
            "potential_multiplier": ポテンシャル倍率,
            "is_blockbuster": ブロックバスター判定,
        }
    """
    if market_cap <= 0 or target_market_size <= 0:
        return {"potential_revenue": 0, "potential_multiplier": 1, "is_blockbuster": False}

    potential_revenue = target_market_size * capture_rate
    # 売上の10倍 = 大まかな理論時価総額（PSR10x相当）
    potential_market_cap = potential_revenue * 10
    multiplier = potential_market_cap / market_cap

    return {
        "potential_revenue": potential_revenue,
        "potential_multiplier": round(max(1, multiplier), 1),
        "is_blockbuster": potential_revenue >= 100e9,  # 年商1000億円超
    }
