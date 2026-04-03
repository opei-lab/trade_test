"""個別銘柄 深堀り分析

1銘柄に特化したリッチ分析。
スキャンでは速度上やれない深い分析をここに集約。
IR本文、テーマ成熟度、パイプライン、セクターパターン、出口戦略まで。
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.data.price import fetch_price, get_stock_info
from src.analysis.supply import (
    calc_supply_score, calc_volume_anomaly, calc_volatility_squeeze,
    calc_price_position, calc_accumulation_signal,
)
from src.analysis.manipulation.detector import detect_phase
from src.analysis.resistance import detect_overhead_supply, detect_volume_vacuum
from src.analysis.whale_detection import detect_whale_accumulation
from src.analysis.whale_plan import reconstruct_whale_plan
from src.analysis.timing import calc_timing_score
from src.analysis.stage_change import detect_financial_stage_change, format_stage_summary
from src.analysis.event_proximity import find_upcoming_events, calc_event_proximity_score
from src.analysis.scenario import build_scenario
from src.analysis.market_structure import analyze_full_structure, format_structure_report
from src.strategy.screener import find_price_targets, calc_entry_exit
from src.strategy.report import generate_report
from src.strategy.conviction import calc_conviction
from src.strategy.multi_trade import generate_multi_trade_plan, format_trade_plan
from src.ui.components import COLORS, PHASE_CONFIG, render_header, render_phase_card

st.set_page_config(page_title="個別分析", page_icon="📈", layout="wide")
render_header()

buy_c = COLORS["buy"]
sell_c = COLORS["sell"]
sec_c = COLORS["text_secondary"]
warn_c = COLORS["caution"]
info_c = COLORS["info"]

# --- サイドバー ---
with st.sidebar:
    st.markdown("""
    **個別分析:** 1銘柄リッチ分析
    IR本文・テーマ成熟度・パイプライン
    セクターパターン・出口戦略まで
    """)
    st.markdown("---")
    code = st.text_input("銘柄コード", value="4572", placeholder="例: 4572")
    period = st.selectbox("期間", [365, 730, 1095], format_func=lambda x: {365: "1年", 730: "2年", 1095: "3年"}[x])
    analyze = st.button("分析", type="primary", use_container_width=True)

if not analyze and not code:
    st.markdown("### 銘柄コードを入力して分析")
    st.caption("1銘柄に特化したリッチ分析。IR本文、テーマ、パイプライン、出口戦略まで。")
    st.stop()

if not analyze:
    st.stop()

# ============================================================
# データ取得
# ============================================================
with st.spinner("データ取得中..."):
    df = fetch_price(code.strip(), period_days=period)

if df.empty:
    st.error("データ取得失敗")
    st.stop()

current = float(df["Close"].iloc[-1])

# --- 全分析を実行 ---
with st.spinner("分析中..."):
    info = {}
    try:
        info = get_stock_info(code)
    except Exception:
        pass

    name = info.get("name") or code
    supply = calc_supply_score(df)
    phase = detect_phase(df)
    trade = calc_entry_exit(df, supply, phase, info)
    levels = find_price_targets(df)
    vacuum = detect_volume_vacuum(df)
    whale = detect_whale_accumulation(df, info)
    whale_plan = reconstruct_whale_plan(df, info)
    timing = calc_timing_score(df)

    stage = {}
    try:
        stage = detect_financial_stage_change(code)
    except Exception:
        pass

    sector = info.get("sector", "")
    industry = info.get("industry", "")
    events = find_upcoming_events(sector, industry)
    event_prox = calc_event_proximity_score(events)

    # 市場構造分析（1銘柄なので全部やる）
    structure = {}
    structure_report = ""
    try:
        structure = analyze_full_structure(df, info)
        structure_report = format_structure_report(structure, name, current)
    except Exception:
        pass

    # シナリオ構築（IR本文取得付き。1銘柄なのでリッチに）
    scenario = build_scenario(code, name, current, structure, rich=True, df=df)

    # build_scenarioが取得済みのデータを再利用（再フェッチしない）
    disclosures = scenario.get("_disclosures", [])
    news_with_body = scenario.get("_news", [])

# ============================================================
# ヘッダー
# ============================================================
st.markdown(f"# {name}")
col_h1, col_h2, col_h3, col_h4, col_h5 = st.columns(5)
col_h1.metric("現在値", f"¥{current:,.0f}")
mcap = info.get("market_cap", 0)
col_h2.metric("時価総額", f"¥{mcap/1e8:,.0f}億" if mcap > 0 else "—")
col_h3.metric("需給スコア", f"{supply.get('total', 0):.0f}/100")
col_h4.metric("インパクト", f"{scenario.get('impact_score', 0)}")
col_h5.metric("ストーリー", "あり" if scenario.get("has_story") else "なし")

# ============================================================
# 結論（最初に表示）
# ============================================================
st.markdown("---")
st.markdown("### 結論")

entry = trade["entry"]
target = trade["target"]
stop = trade["stop_loss"]
reward = trade["reward_pct"]
rr = trade.get("risk_reward", 0)

timing_label = {"NOW": "今が買い時", "NEAR": "もうすぐ圏内", "WAIT": "待ち"}.get(trade["timing"], "—")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("買い", f"¥{entry:,}")
c2.metric("売り目標", f"¥{target:,}", f"+{reward:.0f}%")
c3.metric("損切り", f"¥{stop:,}")
c4.metric("RR比", f"{rr:.1f}")
c5.metric("タイミング", timing_label)

# ============================================================
# シナリオ（IR分析結果）
# ============================================================
st.markdown("---")
st.markdown("### シナリオ")

scenario_text = scenario.get("scenario", "")
if scenario_text:
    for line in scenario_text.split("\n"):
        if line.strip():
            if line.startswith("⚠") or line.startswith("  ⚠"):
                st.warning(line.strip())
            elif line.startswith("  "):
                st.caption(line)
            else:
                st.markdown(f"- {line}")
else:
    st.info("直近IRに顕著なシナリオなし")

# テーマ
themes = scenario.get("themes_detected", [])
if themes:
    st.markdown(f"**テーマ:** {', '.join(themes)}")

# パイプライン
pipeline = scenario.get("pipeline", {})
if pipeline:
    parts = []
    if pipeline.get("highest_phase"):
        parts.append(f"フェーズ: **{pipeline['highest_phase']}**")
    if pipeline.get("diseases"):
        parts.append(f"対象: {', '.join(pipeline['diseases'][:5])}")
    if pipeline.get("trial_positive"):
        parts.append(f"ポジティブ: {', '.join(pipeline['trial_positive'][:3])}")
    if pipeline.get("trial_negative"):
        parts.append(f"ネガティブ: {', '.join(pipeline['trial_negative'][:3])}")
    if parts:
        st.markdown(f"**パイプライン:** {' / '.join(parts)}")

# トリガーとリスク
triggers = scenario.get("triggers", [])
risks = scenario.get("risks", [])
if triggers or risks:
    tc1, tc2 = st.columns(2)
    with tc1:
        if triggers:
            st.markdown("**トリガー:**")
            for t in triggers[:5]:
                st.markdown(f"- {t}")
    with tc2:
        if risks:
            st.markdown("**リスク:**")
            for r in risks[:5]:
                st.markdown(f"- {r}")

# 下落理由
decline_reason = scenario.get("decline_reason", "unknown")
decline_note = scenario.get("decline_note", "")
if decline_reason not in ("unknown", "not_declining") and decline_note:
    decline_labels = {
        "whale_accumulation": "大口が拾っている",
        "algo_selling": "アルゴの機械的売り",
        "market_wide": "市場全体の影響",
        "fundamental": "ファンダ悪化",
    }
    st.markdown(f"**下落理由:** {decline_labels.get(decline_reason, decline_reason)} — {decline_note}")

# ============================================================
# チャート
# ============================================================
st.markdown("---")
st.markdown("### チャート")

fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04,
    row_heights=[0.5, 0.25, 0.25],
    subplot_titles=("株価", "出来高", "需給指標"),
)

fig.add_trace(go.Candlestick(
    x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
    name="株価", increasing_line_color=buy_c, decreasing_line_color=sell_c,
), row=1, col=1)

for s in levels["supports"][:3]:
    fig.add_hline(y=s, line_dash="dash", line_color=buy_c, opacity=0.3, row=1, col=1)
for r_level in levels["resistances"][:3]:
    fig.add_hline(y=r_level, line_dash="dash", line_color=sell_c, opacity=0.3, row=1, col=1)

fig.add_hline(y=entry, line_dash="dot", line_color=info_c, opacity=0.6, row=1, col=1, annotation_text="買い")
fig.add_hline(y=target, line_dash="dot", line_color=buy_c, opacity=0.6, row=1, col=1, annotation_text="売り")
fig.add_hline(y=stop, line_dash="dot", line_color=sell_c, opacity=0.6, row=1, col=1, annotation_text="損切")

# 大口の推定コスト
if whale_plan.get("detected"):
    whale_cost = whale_plan.get("cost", 0)
    if whale_cost > 0:
        fig.add_hline(y=whale_cost, line_dash="dashdot", line_color="#FFD700", opacity=0.5, row=1, col=1, annotation_text="大口コスト")

bar_colors = [sell_c if c < o else buy_c for c, o in zip(df["Close"], df["Open"])]
fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=bar_colors, name="出来高", opacity=0.6), row=2, col=1)

vol_anom = calc_volume_anomaly(df)
fig.add_trace(go.Scatter(x=df.index, y=vol_anom, name="出来高倍率", line=dict(color=warn_c, width=1.5)), row=3, col=1)
fig.add_hline(y=2.8, line_dash="dash", line_color=sell_c, opacity=0.4, row=3, col=1, annotation_text="急増ライン")

accum = calc_accumulation_signal(df)
fig.add_trace(go.Scatter(x=df.index, y=accum, name="買い集め", line=dict(color=info_c, width=1.5)), row=3, col=1)

fig.update_layout(
    height=700, xaxis_rangeslider_visible=False, showlegend=True,
    template="plotly_dark", paper_bgcolor="#0E1117", plot_bgcolor="#0E1117",
    font=dict(color=COLORS["text_primary"], size=11),
    margin=dict(l=50, r=20, t=30, b=20),
)
st.plotly_chart(fig, use_container_width=True)

# ============================================================
# なぜ上がるか
# ============================================================
st.markdown("---")
st.markdown("### なぜ上がるか")

if whale_plan.get("detected"):
    st.markdown(f"**{whale_plan['description']}**")
    tz = whale_plan.get("target_zone", {})
    if tz.get("description"):
        st.markdown(tz["description"])

stage_summary = format_stage_summary(stage) if stage else ""
if stage_summary and "特筆" not in stage_summary:
    st.markdown(f"**ファンダ変化:** {stage_summary}")

reasons = []
if supply.get("volume_anomaly", 1) < 0.7:
    reasons.append(f"出来高が平常の{supply['volume_anomaly']:.1f}倍に縮小（売り手がいない）")
elif supply.get("volume_anomaly", 1) > 2:
    reasons.append(f"出来高が平常の{supply['volume_anomaly']:.1f}倍に急増（誰かが動き始めた）")
if supply.get("is_bottom"):
    reasons.append("底値圏（売り枯れ+ボラ収縮）")
if vacuum.get("has_vacuum"):
    reasons.append(vacuum["description"])
for sig in whale.get("institutional", {}).get("signals", [])[:3]:
    reasons.append(sig)
if events:
    reasons.append(event_prox.get("description", ""))
for r_text in reasons:
    if r_text:
        st.markdown(f"- {r_text}")

# ============================================================
# 市場構造（需給・しこり・大口・売りライン）
# ============================================================
if structure_report:
    st.markdown("---")
    with st.expander("市場構造レポート", expanded=True):
        st.markdown(structure_report)

# ============================================================
# パターン検出 + アルゴ
# ============================================================
st.markdown("---")
col_p1, col_p2 = st.columns(2)

with col_p1:
    st.markdown("### パターン検出")
    render_phase_card(phase)

with col_p2:
    st.markdown("### アルゴ判定ライン")
    algo = whale.get("algo", {})
    ma_levels = algo.get("moving_avg_levels", {})
    if ma_levels:
        for ma_name, ma_data in ma_levels.items():
            role = "サポート" if ma_data["is_support"] else "レジスタンス"
            dist = ma_data["distance_pct"]
            icon = "🟢" if ma_data["is_support"] else "🔴"
            st.markdown(f"{icon} {ma_name}: ¥{ma_data['price']:,}（{role}、乖離{dist:+.1f}%）")
    else:
        st.caption("アルゴ判定ラインなし")

# ============================================================
# 直近シグナル
# ============================================================
if timing.get("signals"):
    st.markdown("---")
    st.markdown("### 直近の動き")
    for sig in timing["signals"]:
        st.markdown(f"- {sig}")

# ============================================================
# 段階トレードプラン
# ============================================================
trade_plan = generate_multi_trade_plan(
    current_price=current, market_cap=mcap, sector=sector, industry=industry,
)
if trade_plan.get("trades"):
    st.markdown("---")
    st.markdown("### 段階トレードプラン")
    st.markdown(format_trade_plan(trade_plan))

# ============================================================
# IR / ニュース（本文付き）
# ============================================================
st.markdown("---")
st.markdown("### IR / ニュース")

tab_news, tab_disc = st.tabs(["ニュース", "適時開示"])

with tab_news:
    if news_with_body:
        for n in news_with_body[:10]:
            title = n.get("title", "")
            date_str = n.get("date", "")
            body = n.get("body", "")
            st.markdown(f"**{date_str}** {title}")
            if body:
                st.caption(body[:300])
            st.markdown("")
    else:
        st.info("ニュースなし")

with tab_disc:
    if disclosures:
        for d in disclosures[:15]:
            cat = d.get("category", "other")
            cat_icons = {
                "upward_revision": "📈", "approval": "✅", "alliance": "🤝",
                "contract": "📝", "order": "📦", "turnaround": "💰",
                "dilution": "⚠", "downward_revision": "📉", "impairment": "⚠",
                "special_loss": "⚠", "earnings": "📊",
            }
            icon = cat_icons.get(cat, "📄")
            st.markdown(f"{icon} **{d.get('date', '')}** {d.get('title', '')}")
    else:
        st.info("適時開示なし")

# ============================================================
# セクターパターン
# ============================================================
matched_patterns = scenario.get("matched_patterns", [])
if matched_patterns:
    st.markdown("---")
    st.markdown("### セクターパターン")
    for mp in matched_patterns:
        st.markdown(f"**{mp['pattern']}**（{mp['sector']}、確信度{mp['confidence']}%、典型{mp.get('typical_move', '')}）")
        if mp.get("description"):
            st.caption(mp["description"])
        if mp.get("high_conviction_if"):
            st.markdown("確度UP条件:")
            for cond in mp["high_conviction_if"]:
                st.markdown(f"  - {cond}")
        if mp.get("low_conviction_if"):
            st.markdown("確度DOWN条件:")
            for cond in mp["low_conviction_if"]:
                st.markdown(f"  - ⚠ {cond}")
        if mp.get("trap"):
            st.warning(f"罠: {mp['trap'][:120]}")
        if mp.get("exit_strategy"):
            exit_line = str(mp["exit_strategy"]).strip().split("\n")[0]
            st.info(f"出口: {exit_line}")

# ============================================================
# 銘柄情報
# ============================================================
st.markdown("---")
with st.expander("銘柄情報"):
    col1, col2 = st.columns(2)
    col1.markdown(f"**セクター:** {info.get('sector', '—')}")
    col1.markdown(f"**業種:** {info.get('industry', '—')}")
    col1.markdown(f"**発行済株数:** {info.get('shares_outstanding', 0)/1e6:,.1f}M")
    col2.markdown(f"**浮動株:** {info.get('float_shares', 0)/1e6:,.1f}M")
    col2.markdown(f"**52週レンジ:** ¥{info.get('fifty_two_week_low', 0):,.0f} - ¥{info.get('fifty_two_week_high', 0):,.0f}")
    col2.markdown(f"**平均出来高:** {info.get('average_volume', 0):,.0f}")

# ============================================================
# フルレポート
# ============================================================
with st.expander("フルレポート"):
    result = {
        "code": code, "name": name, "current_price": current,
        "supply_score": supply.get("total", 0),
        "price_position": supply.get("price_position", 50),
        "volume_anomaly": supply.get("volume_anomaly", 0),
        "squeeze": supply.get("squeeze", 0),
        "divergence": supply.get("divergence", 0),
        "market_cap": mcap,
        "margin_ratio": 0, "ceiling_score": 0, "overhead_pct": 0,
        "has_vacuum": vacuum.get("has_vacuum", False),
        "vacuum_desc": vacuum.get("description", ""),
        "floor_price": trade.get("stop_loss", 0),
        "max_downside_pct": 15,
        "safety_score": 60,
        "stage_summary": stage_summary,
        "market_gap": stage.get("market_gap", "none"),
        "entry": entry, "target": target, "stop_loss": stop,
        "reward_pct": reward,
        "risk_pct": trade.get("risk_pct", 10),
        "risk_reward": trade.get("risk_reward", 0),
        "multiplier": trade.get("multiplier", 0),
        "timing": trade["timing"],
        "target_basis": trade.get("target_basis", ""),
        "prev_highs": trade.get("prev_highs", []),
        "timeframe": trade.get("timeframe", {}),
        "conviction": {"grade": "?", "conviction_score": 0, "conviction_count": 0,
                       "total_checks": 0, "passed": [], "failed": []},
        "conviction_grade": "?",
        "staged_targets": [], "upcoming_events": events[:3],
        "timing_signals": timing.get("signals", []),
        "whale_summary": whale.get("summary", ""),
        "whale_plan_description": whale_plan.get("description", ""),
        "whale_plan": whale_plan,
        "institutional_signals": whale.get("institutional", {}).get("signals", []),
        "sector_description": "",
        "stage_risks": stage.get("risks", []),
        "risk_factors": [],
        "trade_plan": trade_plan,
        "scenario_text": scenario.get("scenario", ""),
        "has_story": scenario.get("has_story", False),
        "impact_score": scenario.get("impact_score", 0),
        "themes_detected": scenario.get("themes_detected", []),
        "pipeline": scenario.get("pipeline", {}),
        "backtest": {},
        "expectation": {},
    }
    st.markdown(generate_report(result))
