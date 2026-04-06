"""自動スクリーニングモジュール

全銘柄を自動分析し、有望銘柄をスコア順にランキングする。
目標価格は全て定量データ（需給、時価総額、浮動株、出来高、過去値動き）から算出。
専門家の意見やアナリスト予想は一切使わない。
"""

import pandas as pd
import numpy as np
from src.data.price import fetch_price, get_stock_info
from src.analysis.supply import calc_supply_score, calc_price_position
from src.analysis.manipulation.detector import detect_phase
from src.analysis.safety import calc_downside_floor, calc_asymmetry_score, is_pure_manipulation
from src.analysis.resistance import calc_ceiling_score, detect_volume_vacuum
from src.data.margin import fetch_margin_data, calc_margin_pressure
from src.strategy.conviction import calc_conviction
from src.analysis.timing import calc_timing_score
from src.analysis.stage_change import detect_financial_stage_change, format_stage_summary
from src.analysis.event_proximity import find_upcoming_events, calc_event_proximity_score
from src.analysis.backtest import backtest_stock, find_winning_patterns, estimate_realistic_target
from src.analysis.pipeline_value import calc_staged_targets_bio, calc_staged_targets_generic
from src.strategy.multi_trade import generate_multi_trade_plan
from src.strategy.screen_helpers import (
    calc_float_scarcity, find_price_targets, estimate_timeframe,
    calc_entry_exit, build_reason,
)
from src.ml.predictor import predict_win_probability


def check_market_environment() -> dict:
    """市場全体の環境を判定する。バックテスト検証済み。

    crash除外で赤字年ゼロ（10年中10年黒字）。
    market上昇時のみで勝率67%。

    Returns:
        {
            "condition": "crash" | "down" | "flat" | "up" | "surge",
            "tradeable": bool（今トレードすべきか）,
            "description": str,
        }
    """
    try:
        import yfinance as yf
        mkt = yf.download("2516.T", period="90d", progress=False)
        if mkt.empty:
            mkt = yf.download("^N225", period="90d", progress=False)
        if mkt.empty:
            return {"condition": "unknown", "tradeable": True, "description": "市場データ取得不可"}

        close = mkt['Close']
        if hasattr(close, 'columns'):
            close = close.iloc[:, 0]
        close = close.dropna()
        if len(close) < 21:
            return {"condition": "unknown", "tradeable": True, "description": "市場データ不足"}

        c_last = float(close.iloc[-1])
        ret_20d = float((c_last / float(close.iloc[-21]) - 1) * 100) if len(close) >= 21 and float(close.iloc[-21]) > 0 else 0
        ret_60d = float((c_last / float(close.iloc[-61]) - 1) * 100) if len(close) >= 61 and float(close.iloc[-61]) > 0 else 0

        # 1日の急落チェック
        ret_1d = float((c_last / float(close.iloc[-2]) - 1) * 100) if len(close) >= 2 else 0

        # MA位置
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else ma20
        above_ma20 = c_last > ma20
        above_ma60 = c_last > ma60

        # じわ下げ判定（MA両方下+60日マイナス=不作環境）
        gradual_decline = not above_ma20 and not above_ma60 and ret_60d < -5

        if ret_1d < -5:
            return {"condition": "shock", "tradeable": False,
                    "description": f"ショック発生（1日{ret_1d:+.1f}%）。様子見推奨",
                    "gradual_decline": False}
        if ret_20d < -10:
            return {"condition": "crash", "tradeable": True,
                    "description": f"市場暴落中（20日{ret_20d:+.1f}%）。crash戦略適用",
                    "gradual_decline": gradual_decline}
        if gradual_decline:
            return {"condition": "gradual_decline", "tradeable": True,
                    "description": f"じわ下げ環境（MA20下+MA60下+60日{ret_60d:+.1f}%）。不作リスク。S+条件のみ推奨",
                    "gradual_decline": True}
        elif ret_20d < -3:
            return {"condition": "down", "tradeable": True,
                    "description": f"市場下落（20日{ret_20d:+.1f}%）。厳選モード",
                    "gradual_decline": False}
        elif above_ma20 and above_ma60 and ret_60d > 0:
            return {"condition": "healthy", "tradeable": True,
                    "description": f"市場健全（MA上+60日{ret_60d:+.1f}%）。通常運用OK",
                    "gradual_decline": False}
        elif ret_20d > 10:
            return {"condition": "surge", "tradeable": True,
                    "description": f"市場急騰（20日{ret_20d:+.1f}%）",
                    "gradual_decline": False}
        elif ret_20d > 3:
            return {"condition": "up", "tradeable": True,
                    "description": f"市場上昇（20日{ret_20d:+.1f}%）",
                    "gradual_decline": False}
        else:
            return {"condition": "flat", "tradeable": True,
                    "description": f"市場横ばい（20日{ret_20d:+.1f}%、60日{ret_60d:+.1f}%）",
                    "gradual_decline": False}
    except Exception:
        return {"condition": "unknown", "tradeable": True, "description": "市場環境判定エラー"}


def screen_stocks(
    codes: list[str],
    period_days: int = 365,
    min_score: float = 40,
    progress_callback=None,
) -> list[dict]:
    """7段階フィルタ。全市場（プライム+スタンダード+グロース）500円以下対象。

    Stage 0: 市場環境判定 — crash/shock/季節
    Stage 1: 環境（全市場→~200）— 500円以下・流動性
    Stage 2: 構造（~200→~60）— しこり・真空・Phase・上値余地
    Stage 3: 需給（~60→~50）— 出来高トレンド・供給スコア
    Stage 4: 信用（~50→~45）— 致命的な信用倍率だけ除外
    Stage 5: 潜伏スコア（~45→10）— S+92%/T1 77%（全市場検証済み）
    → 上位10件をStage 6（情報分析: ファンダ+IR同時評価）に送る

    9月: bot15+gf30+rsi_turn(75%)またはhvol4+gf30+bot15(59.5%)のみ
    """
    import logging
    processed = set()
    total = len(codes)

    # ============================================================
    # Stage 0: 市場環境判定（バックテスト検証済み）
    # crash除外で赤字年ゼロ。market上昇時のみで勝率67%
    # ============================================================
    market_env = check_market_environment()
    logging.info(f"Stage 0 市場: {market_env['condition']} - {market_env['description']}")

    # crash時はむしろチャンス（勝率88%）。除外せず戦略を切り替える
    # 横ばいは厳選モード（T1のみ）
    # 季節フィルタ
    # 3月: gf30+bot15+RSI反転限定で70%。条件付きで稼働
    # 月別戦略（網羅探索結果に基づく）
    from datetime import date as _date
    _month = _date.today().month
    market_env["month"] = _month
    _month_info = {
        3: "🟢3月（ゴールデン）期末ドレッシング底。T1:97%",
        4: "🟢4月 panic:90%, gf+bot:86%",
        12: "🟢12月 節税売り底。gf_crash:96%",
        9: "🔴9月 panic(77%)のみ。他は見送り",
        10: "🔴10月 gf_crash(64%)のみ。厳選",
        7: "🟡7月 gf_crash(73%)のみ",
    }.get(_month)
    if _month_info:
        market_env["description"] += f"　{_month_info}"

    # セクターマップ構築（セクター別勝率の適用に必要）
    try:
        from src.data.stocklist import fetch_stocklist
        _sl = fetch_stocklist()
        _sector_map = dict(zip(_sl["code"].astype(str) + ".T", _sl["sector"]))
    except Exception:
        _sector_map = {}

    # ============================================================
    # Stage 1: 環境フィルタ（全銘柄。超高速）
    # 価格・流動性・データ量。触れない銘柄を除外
    # ============================================================
    stage1 = []
    for i, code in enumerate(codes):
        if progress_callback:
            progress_callback(i, total, f"Stage 1 環境: {code} ({i+1}/{total})")

        if code in processed:
            continue
        processed.add(code)

        try:
            df = fetch_price(code, period_days=period_days)
            if df.empty or len(df) < 60:
                continue

            current = float(df["Close"].iloc[-1])

            if current > 500:
                continue

            # データ異常値チェック（分割未調整や取得エラー防止）
            # 直近20日の中央値と比較し、5倍以上乖離してたら異常
            if len(df) >= 20:
                median_20d = float(df["Close"].tail(20).median())
                if median_20d > 0 and (current / median_20d > 5 or current / median_20d < 0.2):
                    continue  # 異常値。スキップ

            avg_volume_20d = float(df["Volume"].tail(20).mean())
            avg_turnover = avg_volume_20d * current
            if avg_volume_20d < 1000:
                continue
            if avg_turnover < 1_000_000:
                continue

            stage1.append({"code": code, "df": df, "current": current,
                            "avg_volume": avg_volume_20d, "avg_turnover": avg_turnover,
                            "sector": _sector_map.get(code, "")})
        except Exception:
            continue

    logging.info(f"Stage 1 環境: {total}→{len(stage1)}")

    # ============================================================
    # Stage 2: 構造フィルタ（「上がれる構造か」）
    # しこり・真空・Phase・上値余地。dfのみの計算。高速
    # ============================================================
    stage2 = []
    s2_total = len(stage1)
    for s2_i, item in enumerate(stage1):
        code = item["code"]
        df = item["df"]
        current = item["current"]
        if progress_callback and s2_i % 20 == 0:
            progress_callback(s2_i, s2_total, f"Stage 2 構造: {code} ({s2_i+1}/{s2_total})")

        try:
            info = {}
            supply = calc_supply_score(df)
            phase = detect_phase(df)
            trade = calc_entry_exit(df, supply, phase)
            price_levels = find_price_targets(df)

            price_position = supply.get("price_position", 50)

            # フェーズE（売り崩し後）は即除外
            if phase.get("phase") == "E":
                continue

            # 損切り-15%固定
            trade["stop_loss"] = round(trade["entry"] * 0.85)
            trade["risk_pct"] = 15
            trade["risk_reward"] = trade["reward_pct"] / 15 if trade["reward_pct"] > 0 else 0

            # 上値の重さ・真空地帯
            _ceil = calc_ceiling_score(df)
            _ceiling_score = _ceil.get("ceiling_score", 50)
            _vac = detect_volume_vacuum(df)

            # しこりが最悪（直近高値圏に出来高密集）→ 除外
            if _ceiling_score >= 70:
                continue

            # 安値切り上げ（底固め進行中か）
            _higher_lows = False
            if len(df) >= 60:
                _lows = df["Low"].tail(60)
                _q1_low = float(_lows.iloc[:20].min())
                _q2_low = float(_lows.iloc[20:40].min())
                _q3_low = float(_lows.iloc[40:].min())
                _higher_lows = _q1_low <= _q2_low <= _q3_low

            # 構造的に上がれる余地があるか
            has_upside = (
                trade["reward_pct"] >= 30  # 最低30%の上値余地
                or _vac.get("has_vacuum", False)  # 真空地帯がある
                or (price_position <= 15 and current < 500)  # 超低位の深底値
            )
            if not has_upside:
                continue

            # ヒストリカルレンジ
            recent_6m = df.tail(min(120, len(df)))
            historical_range = float(recent_6m["Close"].max()) / max(float(recent_6m["Close"].min()), 1)
            historical_high = price_levels.get("historical_high", current)

            # 勝ちパターンフラグ
            is_best_pattern = (price_position < 15 and historical_range >= 3)
            is_good_pattern = (price_position < 25 and historical_range >= 2.5)

            floor = calc_downside_floor(df, {})
            asymmetry = calc_asymmetry_score(trade["reward_pct"], floor.get("max_downside_pct", 20))
            _overhead_pct = _ceil.get("overhead_supply", {}).get("total_overhead_pct", 0)
            _has_vacuum = _vac.get("has_vacuum", False)
            _vacuum_width = _vac.get("vacuum_width_pct", 0)

            # 出来高トレンド
            _volume_trend = 1.0
            if len(df) >= 40:
                _vol_recent = float(df["Volume"].tail(20).mean())
                _vol_prev = float(df["Volume"].iloc[-40:-20].mean())
                _volume_trend = _vol_recent / _vol_prev if _vol_prev > 0 else 1.0

            stage2.append({
                "code": code,
                "df": df,
                "current_price": current,
                "name": "",
                "is_best_pattern": is_best_pattern,
                "is_good_pattern": is_good_pattern,
                "historical_range": round(historical_range, 1),
                "ret_3d": round((current - float(df["Close"].iloc[-4])) / float(df["Close"].iloc[-4]) * 100, 1) if len(df) >= 4 else 0,
                "supply_score": supply.get("total", 0),
                "float_scarcity": 0,
                "market_cap": 0,
                "phase": phase.get("phase", "NONE"),
                "phase_confidence": phase.get("confidence", 0),
                "phase_desc": phase.get("description", ""),
                "entry": trade["entry"],
                "target": trade["target"],
                "stop_loss": trade["stop_loss"],
                "reward_pct": trade["reward_pct"],
                "risk_pct": trade["risk_pct"],
                "risk_reward": trade["risk_reward"],
                "multiplier": trade.get("multiplier", 0),
                "timing": trade["timing"],
                "is_bottom": supply.get("is_bottom", False),
                "volume_anomaly": supply.get("volume_anomaly", 0),
                "squeeze": supply.get("squeeze", 0),
                "safety_score": 50,
                "floor_price": floor.get("floor_price", 0),
                "max_downside_pct": floor.get("max_downside_pct", 0),
                "asymmetry": asymmetry,
                "risk_factors": [],
                "ceiling_score": _ceiling_score,
                "margin_ratio": 0,
                "margin_score": 50,
                "margin_reason": "",
                "overhead_pct": _overhead_pct,
                "margin_buy_change": 0,
                "price_position": supply.get("price_position", 50),
                "divergence": supply.get("divergence", 0),
                "accumulation": supply.get("accumulation", 0),
                "ml_win_prob": None,
                "has_vacuum": _has_vacuum,
                "vacuum_desc": _vac.get("description", ""),
                "vacuum_width_pct": _vacuum_width,
                "volume_trend": round(_volume_trend, 2),
                "higher_lows": _higher_lows,
                "stage_score": 0,
                "stage_changes": [],
                "stage_risks": [],
                "market_gap": "none",
                "dilution_risk_count": 0,
                "stage_summary": "",
                "sector": item.get("sector", ""),
            })
        except Exception:
            continue

    logging.info(f"Stage 2 構造: {len(stage1)}→{len(stage2)}")
    if progress_callback:
        progress_callback(0, 1, f"Stage 2完了: {len(stage1)}→{len(stage2)}")

    # ============================================================
    # Stage 3: 需給フィルタ（「需給が味方しているか」）
    # 確定的にダメなものだけ切る。上位50件に絞る
    # ============================================================
    if progress_callback:
        progress_callback(0, 1, f"Stage 3 需給: {len(stage2)}件をフィルタ中...")
    stage3 = []
    for r in stage2:
        if r.get("volume_trend", 1.0) < 0.5:
            continue
        if r.get("max_downside_pct", 30) > 50:
            continue
        if r.get("supply_score", 0) < 20:
            continue
        if r.get("reward_pct", 0) < 15:
            continue
        stage3.append(r)

    stage3.sort(key=lambda x: x.get("supply_score", 0), reverse=True)
    stage3 = stage3[:50]

    logging.info(f"Stage 3 需給: {len(stage2)}→{len(stage3)}")
    if progress_callback:
        progress_callback(0, 1, f"Stage 3完了: →{len(stage3)}件. 信用チェック中...")

    # ============================================================
    # Stage 4: 信用致命判定（50件だけに適用。5-10倍=勝率10%を除外）
    # ============================================================
    from src.analysis.funda_score import calc_margin_score

    stage4 = []
    for si, r in enumerate(stage3):
        code = r["code"]
        if progress_callback and si % 10 == 0:
            progress_callback(si, len(stage3), f"Stage 4 信用: {code} ({si+1}/{len(stage3)})")
        try:
            margin = fetch_margin_data(code)
            ms = calc_margin_score(margin.get("margin_ratio", 0))
            r["margin_ratio"] = ms["margin_ratio"]
            r["margin_score"] = ms["margin_score"]
            r["margin_reason"] = ms["margin_reason"]
            r["margin_buy_change"] = margin.get("margin_buy_change", 0)

            if ms["is_fatal"]:
                continue
        except Exception:
            pass
        stage4.append(r)

    logging.info(f"Stage 4 信用: {len(stage3)}→{len(stage4)}")
    if progress_callback:
        progress_callback(0, 1, f"Stage 4完了: →{len(stage4)}件. 潜伏スコア計算中...")

    # ============================================================
    # Stage 5: 動意スコアリング（「動き始めてるか」+「残り上値余地」）
    # ============================================================
    s5_total = len(stage4)
    for s5_i, r in enumerate(stage4):
        if progress_callback and s5_i % 10 == 0:
            progress_callback(s5_i, s5_total, f"Stage 5 潜伏: {r['code']} ({s5_i+1}/{s5_total})")
        df = r.get("df")
        try:
            # タイミング
            timing_result = calc_timing_score(df)
            r["timing_score"] = timing_result["timing_score"]
            r["urgency"] = timing_result["urgency"]
            r["timing_signals"] = timing_result["signals"]
            r["timing_desc"] = timing_result["description"]

            # イベント接近
            events = find_upcoming_events("", "")
            event_prox = calc_event_proximity_score(events)
            r["event_proximity_score"] = event_prox["score"]
            r["event_description"] = event_prox.get("description", "")
            r["upcoming_events"] = events[:3]

            # gap_frequency（窓あけ頻度。IR銘柄の間接検出。lift+13%）
            if df is not None and len(df) >= 20:
                _gaps = (df['Open'].tail(20) / df['Close'].shift().tail(20) - 1).abs()
                r["gap_frequency"] = float((_gaps > 0.02).mean())
            else:
                r["gap_frequency"] = 0

            # bounce_from_low（直近安値からの反発率）
            if df is not None and len(df) >= 20:
                _recent_low = float(df['Close'].tail(20).min())
                r["bounce_from_low"] = (float(df['Close'].iloc[-1]) - _recent_low) / _recent_low * 100 if _recent_low > 0 else 0
            else:
                r["bounce_from_low"] = 0

            # ret5d（直近5日リターン。80%コンボの核: ret5dn8=-8%以下で勝率81%）
            if df is not None and len(df) >= 6:
                r["ret_5d"] = (float(df['Close'].iloc[-1]) / float(df['Close'].iloc[-6]) - 1) * 100
            else:
                r["ret_5d"] = 0

            # RSI反転（下げ切り確認。80%コンボ+RSI反転+季節除外=81%）
            r["rsi_turning"] = False
            r["rsi_value"] = 50
            if df is not None and len(df) >= 16:
                _delta = df['Close'].diff()
                _gain = _delta.clip(lower=0).rolling(14).mean()
                _loss = (-_delta.clip(upper=0)).rolling(14).mean()
                _rs = _gain / (_loss + 1e-10)
                _rsi = 100 - (100 / (1 + _rs))
                r["rsi_value"] = float(_rsi.iloc[-1])
                r["rsi_turning"] = float(_rsi.iloc[-1]) > float(_rsi.iloc[-2])

            # === 90%コンボ用の新指標（全市場検証済み） ===

            # ret20d（直近20日リターン。ret20dn15で勝率68%→コンボで90%）
            if df is not None and len(df) >= 21:
                r["ret_20d"] = (float(df['Close'].iloc[-1]) / float(df['Close'].iloc[-21]) - 1) * 100
            else:
                r["ret_20d"] = 0

            # down_days（連続下落日数。3日+で92.9%コンボの核）
            r["down_days"] = 0
            if df is not None and len(df) >= 10:
                _c = df['Close']
                _dd = 0
                for _di in range(1, min(11, len(_c))):
                    if float(_c.iloc[-_di]) < float(_c.iloc[-_di-1]):
                        _dd += 1
                    else:
                        break
                r["down_days"] = _dd

            # vp_divergence（出来高-価格乖離。売り枯れ検出。89.4%コンボ）
            r["vp_divergence"] = 0
            if df is not None and len(df) >= 20:
                _pc = (float(df['Close'].iloc[-1]) - float(df['Close'].iloc[-20])) / float(df['Close'].iloc[-20])
                _vm = float(df['Volume'].iloc[-20:-10].mean())
                _vc = (float(df['Volume'].tail(10).mean()) - _vm) / max(_vm, 1)
                if _pc < 0:
                    r["vp_divergence"] = max(0, -_pc * 10 - _vc * 5)

            # ma20_dist（MA20乖離率。-10%以下で88%コンボ）
            r["ma20_dist"] = 0
            if df is not None and len(df) >= 20:
                _ma20 = float(df['Close'].tail(20).mean())
                r["ma20_dist"] = (float(df['Close'].iloc[-1]) - _ma20) / _ma20 * 100 if _ma20 > 0 else 0

            # hvol（60日ヒストリカルレンジ%。hvol6=60%+で最強指標）
            r["hvol_pct"] = 0
            if df is not None and len(df) >= 60:
                _r60 = df['Close'].tail(60)
                _rmin = float(_r60.min())
                r["hvol_pct"] = (float(_r60.max()) - _rmin) / _rmin * 100 if _rmin > 0 else 0

            # === 網羅探索で発見した新指標（2026-04-06） ===

            # reversal（反転足: 前日-3%以上→当日陽線。lift+12.7%）
            r["reversal"] = False
            r["strong_reversal"] = False
            if df is not None and len(df) >= 3:
                _prev_bear = float(df['Close'].iloc[-2]) < float(df['Close'].iloc[-3]) * 0.97
                _today_bull = float(df['Close'].iloc[-1]) > float(df['Close'].iloc[-2])
                r["reversal"] = _prev_bear and _today_bull
                _prev_bear5 = float(df['Close'].iloc[-2]) < float(df['Close'].iloc[-3]) * 0.95
                r["strong_reversal"] = _prev_bear5 and _today_bull

            # gap_down（窓開け下落。lift+18.2%/+26.9%）
            r["gap_down"] = False
            if df is not None and len(df) >= 2:
                _o = float(df['Open'].iloc[-1])
                _prev_c = float(df['Close'].iloc[-2])
                r["gap_down"] = _o < _prev_c * 0.97

            # gf_crash（IR銘柄+急落。単体lift+28.5%、n=272）
            r["gf_crash"] = r.get("gap_frequency", 0) >= 0.3 and r.get("ret_20d", 0) <= -15

            # capitulation（セリングクライマックス: 出来高3倍+急落）
            r["capitulation"] = False
            if df is not None and len(df) >= 60:
                _vol_max5 = float(df['Volume'].tail(5).max())
                _vol_avg60 = float(df['Volume'].tail(60).mean())
                r["capitulation"] = _vol_max5 > _vol_avg60 * 3 and r.get("ret_5d", 0) <= -5

            # decelerate（下落減速 = 底打ち兆候）
            r["decelerate"] = False
            if df is not None and len(df) >= 15:
                _ret_w1 = (float(df['Close'].iloc[-6]) - float(df['Close'].iloc[-11])) / float(df['Close'].iloc[-11]) * 100
                _ret_w2 = r.get("ret_5d", 0)
                r["decelerate"] = _ret_w1 < -3 and _ret_w2 > _ret_w1

            # 理由テキスト
            r["reason"] = build_reason(
                {"is_bottom": r["is_bottom"], "volume_anomaly": r["volume_anomaly"],
                 "squeeze": r["squeeze"], "divergence": r["divergence"]},
                {"phase": r["phase"]},
                r, {},
            )

            # 確度（Stage 1時点。Stage 2で再計算）
            r["conviction"] = calc_conviction(r)
            r["conviction_grade"] = r["conviction"]["grade"]
            r["conviction_count"] = r["conviction"]["conviction_count"]
        except Exception:
            r["timing_score"] = 0
            r["urgency"] = "watching"
            r["timing_signals"] = []
            r["timing_desc"] = ""

    # === 潜伏スコア（バックテスト10年検証済み）===
    # 検証結果:
    #   bot15+phC = 72%勝率、損切21%、EV+4.8%
    #   bot15+phC+va<1 = 79%勝率（120銘柄）、68%（499銘柄）
    #   bot15+危険セクター除外+crash除外 = 70%
    #   大口検出(CLV/OBV) = 逆効果(-14.8%)→廃止
    #   出来高爆発ペナルティ = 逆効果→廃止
    #   Phase C = 損切率-9%減、最強の安全フェーズ

    def calc_stealth_score(r):
        """潜伏スコア。市場環境に応じて戦略を切り替える。"""
        score = 0
        mkt = market_env.get("condition", "flat")

        # === crash時: 全部底だから底値フィルタ不要。nosq+lowで88% ===
        if mkt == "crash":
            # crash時は底値位置を重視しない（全部底だから）
            # 代わりにnosq+low条件（低位+ボラあり）だけで十分
            if r.get("has_vacuum"):
                score += 25
            reward = r.get("reward_pct", 0)
            if reward >= 50:
                score += 20
            elif reward >= 30:
                score += 10
            va = r.get("volume_anomaly", 1)
            if va >= 2:
                score += 15  # crash後の出来高急増=反発開始
            return score

        # === 通常〜横ばい市場: 検証済みフィルタ ===

        # --- 底値位置（検証済み: bot15で勝率68%）---
        pp = r.get("price_position", 50)
        if pp < 10:
            score += 40
        elif pp < 15:
            score += 30
        elif pp < 25:
            score += 15
        elif pp < 35:
            score += 5

        # --- Phase（10年検証: phCは3年では72%だが10年では44%。過学習だった）---
        phase = r.get("phase", "NONE")
        if phase == "C":
            score += 10   # 加点するが控えめ（10年だと不安定）
        elif phase == "A":
            score += 5
        elif phase == "D":
            score -= 15

        # --- 上値余地 ---
        reward = r.get("reward_pct", 0)
        if reward >= 80:
            score += 20
        elif reward >= 50:
            score += 10
        elif reward >= 30:
            score += 5

        # --- 真空地帯（検証済み: lift +6%）---
        if r.get("has_vacuum"):
            score += 15

        # --- 直近急落 + RSI反転（80%コンボ+RSI反転+季節除外=81%）---
        ret5 = r.get("ret_5d", 0)
        rsi_turn = r.get("rsi_turning", False)
        rsi_val = r.get("rsi_value", 50)

        if ret5 <= -8:
            score += 35   # 急落。80%コンボの核
            if rsi_turn:
                score += 20  # RSI反転で下げ切り確認（+12% lift）
            if 25 <= rsi_val < 30 and rsi_turn:
                score += 10  # RSI25-30で反転が最強（84%）
        elif ret5 <= -5:
            score += 20
            if rsi_turn:
                score += 10
        elif ret5 <= -3:
            score += 10

        # --- 出来高枯れ（検証済み: va<1でlift +11%）---
        va = r.get("volume_anomaly", 1)
        if va < 0.5:
            score += 15
        elif va < 1.0:
            score += 10

        # --- gap_frequency（10年検証: low+gf+bot15=77%。最強の安定コンボ）---
        gf = r.get("gap_frequency", 0)
        if gf >= 0.3:
            score += 30   # IR銘柄。最重要指標
        elif gf >= 0.15:
            score += 12

        # --- bounce_from_low（10年検証: low+bounce+bot15=75%）---
        bounce = r.get("bounce_from_low", 0)
        if bounce >= 10:
            score += 15   # 底打ちして反発中

        # --- 90%コンボ指標（全市場200銘柄検証済み）---
        ret20 = r.get("ret_20d", 0)
        down_days = r.get("down_days", 0)
        hvol_pct = r.get("hvol_pct", 0)
        vp_div = r.get("vp_divergence", 0)
        ma20_dist = r.get("ma20_dist", 0)

        # 20日急落（lift+20%）
        if ret20 <= -15:
            score += 30
        elif ret20 <= -10:
            score += 15

        # 連続下落（down3+で92.9%コンボの核）
        if down_days >= 5:
            score += 25
        elif down_days >= 3:
            score += 15

        # Volume-Price Divergence（売り枯れ。89.4%コンボ）
        if vp_div >= 2:
            score += 20
        elif vp_div >= 1:
            score += 10

        # MA20乖離（-10%以下で88%コンボ）
        if ma20_dist <= -10:
            score += 20
        elif ma20_dist <= -5:
            score += 10

        # 高ヒストリカルレンジ（hvol6で+24% lift。最強の単独指標）
        if hvol_pct >= 60:
            score += 25
        elif hvol_pct >= 40:
            score += 12

        # --- 網羅探索の新指標 ---

        # reversal（反転足。lift+12.7%。hvol+reversalで83%）
        if r.get("reversal"):
            score += 20
            if r.get("strong_reversal"):
                score += 10  # 前日-5%→当日陽線

        # gap_down（窓開け下落。lift+18.2%）
        if r.get("gap_down"):
            score += 15

        # gf_crash（IR銘柄+急落。lift+28.5%、最強複合指標）
        if r.get("gf_crash"):
            score += 35

        # capitulation（セリクラ。出来高3倍+急落）
        if r.get("capitulation"):
            score += 20

        # decelerate（下落減速。底打ち兆候）
        if r.get("decelerate") and ret5 < -3:
            score += 10

        # --- daily_vol（10年検証: 6%+でlift+11%。ボラ高いほど勝つ）---
        daily_vol = 0
        df = r.get("df")
        if df is not None and len(df) >= 20:
            daily_vol = float(df['Close'].tail(20).pct_change().std() * 100)
        if daily_vol >= 6:
            score += 15
        elif daily_vol >= 4:
            score += 8
        elif daily_vol < 2:
            score -= 10  # 極低ボラはlift-9%

        # --- セクター（網羅探索結果。食料品23%/銀行30%/卸売32%は致命的）---
        sector = r.get("sector", "")
        if sector in ("食料品", "銀行業"):
            score -= 50  # base 23-30%。ほぼ勝てない
        elif sector in ("卸売業", "小売業"):
            score -= 20  # base 32-37%
        elif sector in ("精密機器", "金属製品"):
            score += 15  # base 75-81%。セクター自体が強い
        elif sector in ("建設業", "機械"):
            score += 8   # base 57-59%
        elif sector in ("Financial Services", "Consumer Defensive"):
            score -= 20

        # --- 横ばい市場: 厳選 ---
        if mkt == "flat":
            if not (pp < 15 and (phase == "C" or gf >= 0.3)):
                score -= 10

        # --- 月別戦略（網羅探索: 月×コンボの全組み合わせ検証済み）---
        _month = market_env.get("month", 0)

        if _month == 3:  # ゴールデンマンス。T1:97%, gf_crash:92%
            score += 25  # 3月ボーナス（期末ドレッシングの底拾い）
        elif _month == 12:  # 節税売りの底。gf_crash:96%, rsi_turn:94%
            score += 20  # 12月ボーナス
        elif _month == 4:  # panic:90%, gf30+bot15:86%
            score += 10
        elif _month in (9, 10):  # 9月base45%+panic77%のみ、10月42%+gf_crash64%のみ
            # 9月: panicのみ通す
            if _month == 9:
                is_panic = r.get("down_days", 0) >= 3 and hvol_pct >= 40
                sept_pass = (
                    is_panic
                    or (pp < 15 and gf >= 0.3 and r.get("rsi_turning"))
                    or (daily_vol >= 4 and gf >= 0.3 and pp < 15)
                )
                if not sept_pass:
                    score -= 100
            else:  # 10月
                if not r.get("gf_crash"):
                    score -= 30
        elif _month == 7:  # base41%。gf_crash:73%のみ
            if not (r.get("gf_crash") or (gf >= 0.3 and pp < 15)):
                score -= 15

        return score

    for r in stage4:
        r["motion_score"] = calc_stealth_score(r)

        # Tier判定（資金配分の参考。市場環境で戦略が変わる）
        pp = r.get("price_position", 50)
        phase = r.get("phase", "NONE")
        va = r.get("volume_anomaly", 1)
        mkt = market_env.get("condition", "flat")

        gf = r.get("gap_frequency", 0)
        bounce = r.get("bounce_from_low", 0)
        ret5 = r.get("ret_5d", 0)
        is_low500 = r.get("current_price", 9999) < 500

        rsi_turn = r.get("rsi_turning", False)

        # daily_vol計算（Tier判定用）
        daily_vol = 0
        _tier_df = r.get("df")
        if _tier_df is not None and len(_tier_df) >= 20:
            daily_vol = float(_tier_df['Close'].tail(20).pct_change().std() * 100)

        # 90%コンボ用の変数
        ret20 = r.get("ret_20d", 0)
        down_days = r.get("down_days", 0)
        hvol_pct = r.get("hvol_pct", 0)
        vp_div = r.get("vp_divergence", 0)
        ma20_dist = r.get("ma20_dist", 0)

        # 網羅探索で発見した追加変数
        reversal = r.get("reversal", False)
        strong_rev = r.get("strong_reversal", False)
        gap_down = r.get("gap_down", False)
        gf_crash = r.get("gf_crash", False)
        capitulation = r.get("capitulation", False)
        decelerate = r.get("decelerate", False)

        # ============================================================
        # Tier判定（84指標×300銘柄×10年の網羅探索に基づく）
        # 勝率は全年度ベース。🟢=崩壊年0、🟡=崩壊年1
        # ============================================================

        if mkt == "crash":
            r["tier"] = "CRASH"
            r["tier_desc"] = "暴落反発（88%）"

        # === SS: パニック底（86%、2020除外検証済、直近3年96%）===
        elif hvol_pct >= 60 and ret20 <= -15 and down_days >= 3:
            r["tier"] = "SS"
            r["tier_desc"] = "パニック底（86%、n=132）"

        # === S+: 🟢安定82%コンボ（n=200超、全年度崩壊なし）===
        elif hvol_pct >= 70 and pp < 20:  # hvol7+hvol_bot: 82.2% n=321 🟢
            r["tier"] = "S+"
            r["tier_desc"] = "高ボラ+底値圏（82%、n=321🟢）"
        elif hvol_pct >= 50 and ma20_dist <= -10 and r.get("squeeze", 0) < 40:  # hvol5+ma乖離+nosq: 82% n=255 🟢
            r["tier"] = "S+"
            r["tier_desc"] = "ボラ+MA乖離+非収縮（82%、n=255🟢）"
        elif gf_crash and hvol_pct >= 60:  # gf_crash+hvol6: 81.6% n=179 🟢
            r["tier"] = "S+"
            r["tier_desc"] = "IR銘柄急落+高ボラ（82%、n=179🟢）"
        elif hvol_pct >= 50 and ma20_dist <= -10 and pp < 20:  # hvol5+ma乖離+bot: 81.7% n=219 🟢
            r["tier"] = "S+"
            r["tier_desc"] = "ボラ+MA乖離+底値（82%、n=219🟢）"

        # === S: 高勝率コンボ（75-85%）===
        elif hvol_pct >= 80 and reversal:  # hvol8+reversal: 83.3% n=18
            r["tier"] = "S"
            r["tier_desc"] = "超高ボラ+反転足（83%）"
        elif hvol_pct >= 60 and reversal:  # hvol6+reversal: 78.6% n=42
            r["tier"] = "S"
            r["tier_desc"] = "高ボラ+反転足（79%）"
        elif gf_crash:  # gf_crash単体: 74.6% n=272
            r["tier"] = "S"
            r["tier_desc"] = "IR銘柄急落（75%、n=272）"
        elif ret20 <= -20 and hvol_pct >= 50:  # ret20dn20+hvol5: ~82% n=247
            r["tier"] = "S"
            r["tier_desc"] = "20日急落+ボラ（81%）"
        elif is_low500 and ret5 <= -8 and daily_vol >= 3 and pp < 20 and rsi_turn:
            r["tier"] = "S"
            r["tier_desc"] = "急落+RSI反転（80%、直近3年）"
        elif hvol_pct >= 60 and ret20 <= -15:
            r["tier"] = "S"
            r["tier_desc"] = "高ボラ+20日急落（70%）"
        elif gap_down and gf >= 0.2 and r.get("squeeze", 0) < 40:  # nosq+gf20+gap_down: 84.2%
            r["tier"] = "S"
            r["tier_desc"] = "IR銘柄+窓開け下落（84%）"

        # === T1: 安定高勝率（70-80%）===
        elif pp < 15 and gf >= 0.3:
            r["tier"] = "T1"
            r["tier_desc"] = "bot15+IR銘柄（77%）"
        elif pp < 15 and ret5 <= -5:
            r["tier"] = "T1b"
            r["tier_desc"] = "bot15+押し目（72%）"
        elif pp < 25 and gf >= 0.3:
            r["tier"] = "T1c"
            r["tier_desc"] = "bot25+IR銘柄（71%）"

        # === T2-T3 ===
        elif pp < 15:
            r["tier"] = "T2"
            r["tier_desc"] = "bot15（68%）"
        else:
            r["tier"] = "T3"
            r["tier_desc"] = "nosq+low（60%）"

    stage4.sort(key=lambda x: x.get("motion_score", 0), reverse=True)

    # Stage 6送り件数: 高Tier多ければ多めに送る（IR/ファンダで絞るため）
    # S+/S/CRASH/T1/T1b/T1c = 高Tier。T2/T3は低Tier
    high_tier_count = sum(1 for r in stage4 if r.get("tier") in ("S+", "S", "CRASH", "T1", "T1b", "T1c"))
    if high_tier_count >= 15:
        max_stage6 = 25  # 高Tier多数。IR/ファンダで絞る余地あり
    elif high_tier_count >= 8:
        max_stage6 = 20
    else:
        max_stage6 = 15  # 低Tier中心なら絞って速度優先

    stage5 = stage4[:max_stage6]

    for r in stage5:
        r.pop("df", None)
        r["market_env"] = market_env  # 市場環境を結果に含める

    logging.info(f"Stage 5 潜伏: {len(stage4)}→{len(stage5)} (高Tier {high_tier_count}件)")
    if progress_callback:
        progress_callback(0, 1, f"Stage 5完了: →{len(stage5)}件（高Tier{high_tier_count}）. 情報分析へ...")

    return stage5
