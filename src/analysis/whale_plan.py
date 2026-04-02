"""大口の売買計画逆算モジュール

チャートは誰かの売買の結果。
出来高の異常パターンから大口の「仕込み量」「取得コスト」
「目標売値」「残り玉」を逆算する。
"""

import pandas as pd
import numpy as np


def estimate_accumulation(df: pd.DataFrame, baseline_window: int = 60) -> dict:
    """大口の仕込み量を推定する。

    通常の出来高（ベースライン）と実際の出来高の差分を累積し、
    「誰かが通常より多く買った量」を推定する。

    Returns:
        {
            "accumulated_shares": 推定仕込み量（株数）,
            "accumulation_period": 仕込み期間（日数）,
            "accumulation_start": 仕込み開始推定日,
            "avg_cost": 推定平均取得単価（VWAP）,
            "total_cost": 推定仕込み金額,
            "is_accumulating": 現在も仕込み中か,
            "pct_of_float": 浮動株に対する仕込み比率,
        }
    """
    if len(df) < baseline_window + 20:
        return {"accumulated_shares": 0, "description": "データ不足"}

    volume = df["Volume"]
    close = df["Close"]

    # ベースライン出来高（過去の安定期間の平均）
    baseline = volume.rolling(baseline_window).mean()

    # 異常出来高の検出
    # ベースラインを超えた分を「誰かが追加で売買した量」と推定
    # ベースラインは過去の「静かな期間」の出来高
    # 全期間の中央値をベースラインとする（平均だと急騰期間に引っ張られる）
    baseline_median = volume.rolling(baseline_window).median()
    excess = (volume - baseline_median).clip(lower=0)

    # 有意な異常出来高がある期間を探す
    # 「出来高がベースラインの1.5倍を超えた日」が直近にあるか
    significant = volume > baseline_median * 1.5

    # 直近90日以内に有意な出来高がある期間を探す
    lookback = min(90, len(df) - baseline_window)
    recent_significant = significant.tail(lookback)

    if recent_significant.sum() < 3:
        # 有意な日が3日未満 → 大口の活動なし
        # ただし長期的な累積をチェック
        total_excess = float(excess.tail(lookback).sum())
        if total_excess <= 0:
            return {"accumulated_shares": 0, "description": "異常出来高なし"}

    # 仕込み開始を推定: 直近で出来高パターンが変化した時点
    accumulation_start_idx = max(baseline_window, len(df) - lookback)

    # 仕込み期間のデータ
    accum_period = df.iloc[accumulation_start_idx:]
    accum_excess = excess.iloc[accumulation_start_idx:]
    raw_excess = int(accum_excess.sum())
    period_days = len(accum_period)

    if raw_excess <= 0:
        return {"accumulated_shares": 0, "description": "異常出来高なし"}

    # 補正: 異常出来高の全てが大口の片方向ではない
    # - 売買は双方向 → 異常出来高の半分が買い、半分が売り
    # - さらにその中で大口の比率は20-40%程度（残りは個人・デイトレ）
    # 保守的に異常出来高の15%を大口の純買いと推定
    accumulated = int(raw_excess * 0.15)

    if accumulated <= 0:
        return {"accumulated_shares": 0, "description": "異常出来高なし"}

    # 仕込み期間のVWAP（出来高加重平均価格）= 大口の推定取得コスト
    accum_close = close.iloc[accumulation_start_idx:]
    accum_vol = volume.iloc[accumulation_start_idx:]
    total_vol = float(accum_vol.sum())
    if total_vol > 0:
        vwap = float((accum_close * accum_vol).sum() / total_vol)
    else:
        vwap = float(close.iloc[-1])

    total_cost = accumulated * vwap

    # 現在も仕込み中か
    recent_excess = float(excess.tail(5).mean())
    is_accumulating = recent_excess > 0

    # 仕込み開始日
    start_date = df.index[accumulation_start_idx]
    start_str = str(start_date.date()) if hasattr(start_date, "date") else str(start_date)

    return {
        "accumulated_shares": accumulated,
        "accumulation_period": period_days,
        "accumulation_start": start_str,
        "avg_cost": round(vwap),
        "total_cost": round(total_cost),
        "is_accumulating": is_accumulating,
        "pct_of_float": 0,  # 浮動株数が分かれば計算
    }


def estimate_target_zone(accumulation: dict, current_price: float) -> dict:
    """大口の目標売値圏を推定する。

    大口の利確目標:
    - 仕込みコストの2-3倍が一般的
    - ただし全株を売り切るには十分な出来高が必要
    - 高すぎると売り切れない（流動性の制約）

    Returns:
        {
            "target_low": 目標下限（2倍）,
            "target_high": 目標上限（3倍）,
            "estimated_profit": 推定利益,
            "description": 説明,
        }
    """
    avg_cost = accumulation.get("avg_cost", 0)
    accumulated = accumulation.get("accumulated_shares", 0)

    if avg_cost <= 0:
        return {"target_low": 0, "target_high": 0, "description": "データ不足"}

    target_low = round(avg_cost * 2)
    target_high = round(avg_cost * 3)
    est_profit_low = accumulated * (target_low - avg_cost)
    est_profit_high = accumulated * (target_high - avg_cost)

    # 現在値との比較
    if current_price < avg_cost:
        position = "含み損"
        note = f"現在値¥{current_price:,.0f}は大口の推定取得単価¥{avg_cost:,}を下回っている。大口は含み損状態で、ここから売りに出る可能性は低い（むしろ買い増しする可能性）"
    elif current_price < target_low:
        position = "含み益（利確前）"
        note = f"現在値¥{current_price:,.0f}は取得単価¥{avg_cost:,}の{current_price/avg_cost:.1f}倍。まだ目標圏¥{target_low:,}-¥{target_high:,}に未到達。大口は売りに出ていない可能性が高い"
    else:
        position = "利確圏"
        note = f"現在値¥{current_price:,.0f}は取得単価¥{avg_cost:,}の{current_price/avg_cost:.1f}倍で目標圏内。大口が売り始める可能性がある"

    return {
        "target_low": target_low,
        "target_high": target_high,
        "avg_cost": avg_cost,
        "estimated_profit_low": round(est_profit_low),
        "estimated_profit_high": round(est_profit_high),
        "position": position,
        "description": note,
    }


def estimate_remaining_position(df: pd.DataFrame, accumulation: dict) -> dict:
    """大口の残り玉を推定する。

    仕込み推定量 - 売り推定量 = 残り保有量
    残りが多い = まだ株価を支える（下支え）
    残りがゼロに近い = 売り抜け完了（暴落リスク）

    Returns:
        {
            "estimated_remaining": 推定残り株数,
            "sold_estimate": 推定売却済み株数,
            "holding_ratio": 残り比率（100%=全部持っている）,
            "phase": "accumulating" | "holding" | "distributing" | "exited",
            "description": 説明,
        }
    """
    accumulated = accumulation.get("accumulated_shares", 0)
    if accumulated <= 0:
        return {"estimated_remaining": 0, "phase": "none", "description": "仕込みなし"}

    avg_cost = accumulation.get("avg_cost", 0)
    close = df["Close"]
    volume = df["Volume"]

    # 仕込みコストより上で出来高が増えた期間 = 売り（利確）している可能性
    if avg_cost > 0:
        above_cost = df[close > avg_cost * 1.3]  # コストの1.3倍以上で
        if not above_cost.empty:
            # この期間の出来高の一部が売りと推定
            baseline = volume.rolling(60).mean()
            above_excess = (above_cost["Volume"] - baseline.loc[above_cost.index]).clip(lower=0)
            sold_estimate = int(above_excess.sum() * 0.5)  # 異常出来高の半分が売りと推定
        else:
            sold_estimate = 0
    else:
        sold_estimate = 0

    remaining = max(0, accumulated - sold_estimate)
    holding_ratio = (remaining / accumulated * 100) if accumulated > 0 else 0

    # フェーズ判定
    is_accumulating = accumulation.get("is_accumulating", False)
    if is_accumulating:
        phase = "accumulating"
        desc = f"現在も仕込み中。推定{accumulated:,}株中{remaining:,}株を保有（{holding_ratio:.0f}%）。まだ売りに出ていない"
    elif holding_ratio > 80:
        phase = "holding"
        desc = f"仕込み完了、ホールド中。推定{remaining:,}株保有（{holding_ratio:.0f}%）。株価を上げるフェーズ"
    elif holding_ratio > 30:
        phase = "distributing"
        desc = f"一部利確中。推定{sold_estimate:,}株売却済み、{remaining:,}株残り（{holding_ratio:.0f}%）。まだ株価を維持する必要あり"
    else:
        phase = "exited"
        desc = f"ほぼ売り抜け完了。推定残り{remaining:,}株（{holding_ratio:.0f}%）。株価の下支えがなくなる可能性"

    return {
        "estimated_remaining": remaining,
        "sold_estimate": sold_estimate,
        "holding_ratio": round(holding_ratio, 1),
        "phase": phase,
        "description": desc,
    }


def reconstruct_whale_plan(df: pd.DataFrame, info: dict = None) -> dict:
    """大口の売買計画を逆算する。

    全ての推定を統合して「大口は何をしようとしているか」を提示する。
    """
    current = float(df["Close"].iloc[-1])

    # 仕込み量推定
    accum = estimate_accumulation(df)
    accumulated = accum.get("accumulated_shares", 0)

    if accumulated <= 0:
        return {
            "detected": False,
            "description": "大口の顕著な売買パターンは検出されていない",
            "accumulation": accum,
        }

    # 浮動株に対する比率
    if info:
        float_shares = info.get("float_shares", 0)
        if float_shares > 0:
            accum["pct_of_float"] = round(accumulated / float_shares * 100, 1)

    # 目標売値圏
    target = estimate_target_zone(accum, current)

    # 残り玉
    remaining = estimate_remaining_position(df, accum)

    # 総合判定
    phase = remaining["phase"]
    plan_summary = ""
    if phase == "accumulating":
        plan_summary = f"大口が¥{accum['avg_cost']:,}付近で約{accumulated:,}株を仕込み中。目標は¥{target['target_low']:,}-¥{target['target_high']:,}と推定。現在値は取得コスト付近のため、大口が下支えしている状態。この銘柄は大口の計画途中にある"
    elif phase == "holding":
        plan_summary = f"大口の仕込みが完了（推定{accumulated:,}株、取得¥{accum['avg_cost']:,}）。次は株価を引き上げるフェーズ。目標¥{target['target_low']:,}-¥{target['target_high']:,}。大口が売りに出るまでは下支えされる"
    elif phase == "distributing":
        plan_summary = f"大口が利確中（{remaining['holding_ratio']:.0f}%残り）。まだ残りがあるため急落はしにくいが、売りが進むと上値は重くなる"
    elif phase == "exited":
        plan_summary = f"大口がほぼ売り抜け完了。下支えがなくなっている。注意"

    return {
        "detected": True,
        "description": plan_summary,
        "accumulation": accum,
        "target_zone": target,
        "remaining": remaining,
        "current_vs_cost": round(current / accum["avg_cost"], 2) if accum["avg_cost"] > 0 else 0,
    }
