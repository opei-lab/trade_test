"""個別銘柄 深堀り分析

screenerで気になった銘柄を徹底的に分析する。
screenerより明らかにリッチ: チャート、大口の動き、イベントカレンダー、
段階トレードプラン、過去パターンの実績を全て表示。
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
from src.strategy.screener import find_price_targets, calc_entry_exit
from src.strategy.report import generate_report
from src.strategy.multi_trade import generate_multi_trade_plan, format_trade_plan
from src.ui.components import COLORS, PHASE_CONFIG, render_header, render_phase_card

st.set_page_config(page_title="個別分析", page_icon="📈", layout="wide")
render_header()

buy_c = COLORS["buy"]
sell_c = COLORS["sell"]
sec_c = COLORS["text_secondary"]
warn_c = COLORS["caution"]

# --- サイドバー ---
with st.sidebar:
    code = st.text_input("銘柄コード", value="4572", placeholder="例: 4572")
    period = st.selectbox("期間", [365, 730, 1095], format_func=lambda x: {365: "1年", 730: "2年", 1095: "3年"}[x])
    analyze = st.button("分析", type="primary", use_container_width=True)

if not analyze and not code:
    st.markdown(f"""
    <div style="text-align:center;padding:60px 0">
        <div style="font-size:3em">📈</div>
        <div style="font-size:1.2em;font-weight:600;margin-top:8px">銘柄コードを入力して分析</div>
        <div style="color:{sec_c};margin-top:4px">screenerで気になった銘柄をここで深堀り</div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

if not analyze:
    st.stop()

# --- データ取得 ---
with st.spinner("データ取得中..."):
    df = fetch_price(code.strip(), period_days=period)

if df.empty:
    st.error("データ取得失敗")
    st.stop()

current = float(df["Close"].iloc[-1])

# 全分析を並行実行
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

# ===== ヘッダー =====
st.markdown(f"# {name}")
col_h1, col_h2, col_h3, col_h4 = st.columns(4)
col_h1.metric("現在値", f"¥{current:,.0f}")
mcap = info.get("market_cap", 0)
col_h2.metric("時価総額", f"¥{mcap/1e8:,.0f}億" if mcap > 0 else "—")
col_h3.metric("需給スコア", f"{supply.get('total', 0):.0f}/100")
col_h4.metric("確度", f"{trade.get('risk_reward', 0):.1f} RR")

# ===== 結論（最初に表示） =====
st.markdown("---")
st.markdown("### 結論")

entry = trade["entry"]
target = trade["target"]
stop = trade["stop_loss"]
reward = trade["reward_pct"]

timing_label = {"NOW": "今が買い時", "NEAR": "もうすぐ圏内", "WAIT": "待ち"}.get(trade["timing"], "—")
timing_color = {"NOW": buy_c, "NEAR": warn_c, "WAIT": sec_c}.get(trade["timing"], sec_c)

st.markdown(f"""
<div class="stock-card" style="border-left-color:{timing_color}">
    <div class="card-prices">
        <div class="card-price-item">
            <div class="card-price-label" style="color:{COLORS['info']}">買い</div>
            <div class="card-price-value" style="color:{COLORS['info']}">¥{entry:,}</div>
        </div>
        <div class="card-price-item">
            <div class="card-price-label" style="color:{buy_c}">売り目標</div>
            <div class="card-price-value" style="color:{buy_c}">¥{target:,}</div>
            <div style="color:{buy_c};font-size:0.8em">+{reward:.0f}%</div>
        </div>
        <div class="card-price-item">
            <div class="card-price-label" style="color:{sell_c}">損切り</div>
            <div class="card-price-value" style="color:{sell_c}">¥{stop:,}</div>
        </div>
        <div class="card-price-item">
            <div class="card-price-label" style="color:{sec_c}">タイミング</div>
            <div><span class="badge" style="background:{timing_color}">{timing_label}</span></div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ===== なぜ上がるか =====
st.markdown("### なぜ上がるか")

# 大口の計画
if whale_plan.get("detected"):
    st.markdown(f"**{whale_plan['description']}**")
    tz = whale_plan.get("target_zone", {})
    if tz.get("description"):
        st.markdown(tz["description"])

# ステージ変化
stage_summary = format_stage_summary(stage) if stage else ""
if stage_summary and "特筆" not in stage_summary:
    st.markdown(f"**ファンダ変化:** {stage_summary}")

# 需給
reasons = []
if supply.get("volume_anomaly", 1) < 0.7:
    reasons.append(f"出来高が平常の{supply['volume_anomaly']:.1f}倍に縮小（売り手がいない）")
elif supply.get("volume_anomaly", 1) > 2:
    reasons.append(f"出来高が平常の{supply['volume_anomaly']:.1f}倍に急増（誰かが動き始めた）")
if supply.get("is_bottom"):
    reasons.append("底値圏（売り枯れ+ボラ収縮）")
if vacuum.get("has_vacuum"):
    reasons.append(vacuum["description"])

# 大口シグナル
for sig in whale.get("institutional", {}).get("signals", [])[:2]:
    reasons.append(sig)

# イベント
if events:
    reasons.append(event_prox.get("description", ""))

for r_text in reasons:
    if r_text:
        st.markdown(f"- {r_text}")

# ===== チャート =====
st.markdown("### チャート")

fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04,
    row_heights=[0.5, 0.25, 0.25],
    subplot_titles=("株価", "出来高", "需給指標"),
)

# ローソク足
fig.add_trace(go.Candlestick(
    x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
    name="株価", increasing_line_color=buy_c, decreasing_line_color=sell_c,
), row=1, col=1)

# サポート/レジスタンス
for s in levels["supports"][:3]:
    fig.add_hline(y=s, line_dash="dash", line_color=buy_c, opacity=0.3, row=1, col=1)
for r_level in levels["resistances"][:3]:
    fig.add_hline(y=r_level, line_dash="dash", line_color=sell_c, opacity=0.3, row=1, col=1)

# エントリー/ターゲット
fig.add_hline(y=entry, line_dash="dot", line_color=COLORS["info"], opacity=0.6, row=1, col=1, annotation_text="買い")
fig.add_hline(y=target, line_dash="dot", line_color=buy_c, opacity=0.6, row=1, col=1, annotation_text="売り")
fig.add_hline(y=stop, line_dash="dot", line_color=sell_c, opacity=0.6, row=1, col=1, annotation_text="損切")

# 出来高
bar_colors = [sell_c if c < o else buy_c for c, o in zip(df["Close"], df["Open"])]
fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=bar_colors, name="出来高", opacity=0.6), row=2, col=1)

# 出来高異常度
vol_anom = calc_volume_anomaly(df)
fig.add_trace(go.Scatter(x=df.index, y=vol_anom, name="出来高倍率", line=dict(color=warn_c, width=1.5)), row=3, col=1)
fig.add_hline(y=2.8, line_dash="dash", line_color=sell_c, opacity=0.4, row=3, col=1, annotation_text="急増ライン")

# 買い集めシグナル
accum = calc_accumulation_signal(df)
fig.add_trace(go.Scatter(x=df.index, y=accum, name="買い集め", line=dict(color=COLORS["info"], width=1.5)), row=3, col=1)

fig.update_layout(
    height=700, xaxis_rangeslider_visible=False, showlegend=True,
    template="plotly_dark", paper_bgcolor="#0E1117", plot_bgcolor="#0E1117",
    font=dict(color=COLORS["text_primary"], size=11),
    margin=dict(l=50, r=20, t=30, b=20),
)
st.plotly_chart(fig, use_container_width=True)

# ===== 仕手パターン =====
st.markdown("### パターン検出")
render_phase_card(phase)

# ===== 直近シグナル =====
if timing.get("signals"):
    st.markdown("### 直近の動き")
    for sig in timing["signals"]:
        st.markdown(f"- {sig}")

# ===== 段階トレードプラン =====
trade_plan = generate_multi_trade_plan(
    current_price=current, market_cap=mcap, sector=sector, industry=industry,
)
if trade_plan.get("trades"):
    st.markdown("### 段階トレードプラン")
    st.markdown(format_trade_plan(trade_plan))

# ===== アルゴの判定ライン =====
algo = whale.get("algo", {})
ma_levels = algo.get("moving_avg_levels", {})
if ma_levels:
    st.markdown("### アルゴの判定ライン")
    for ma_name, ma_data in ma_levels.items():
        role = "サポート" if ma_data["is_support"] else "レジスタンス"
        dist = ma_data["distance_pct"]
        icon = "🟢" if ma_data["is_support"] else "🔴"
        st.markdown(f"{icon} {ma_name}: ¥{ma_data['price']:,}（{role}、乖離{dist:+.1f}%）")

# ===== 銘柄情報 =====
with st.expander("銘柄情報"):
    col1, col2 = st.columns(2)
    col1.markdown(f"**セクター:** {info.get('sector', '—')}")
    col1.markdown(f"**業種:** {info.get('industry', '—')}")
    col1.markdown(f"**発行済株数:** {info.get('shares_outstanding', 0)/1e6:,.1f}M")
    col2.markdown(f"**浮動株:** {info.get('float_shares', 0)/1e6:,.1f}M")
    col2.markdown(f"**52週レンジ:** ¥{info.get('fifty_two_week_low', 0):,.0f} - ¥{info.get('fifty_two_week_high', 0):,.0f}")
    col2.markdown(f"**平均出来高:** {info.get('average_volume', 0):,.0f}")

# ===== フルレポート =====
with st.expander("フルレポート"):
    # レポート用のresult dictを構築
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
        "backtest": {},
        "expectation": {},
    }
    st.markdown(generate_report(result))
