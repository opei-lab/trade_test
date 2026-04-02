"""ポートフォリオ管理画面

複数銘柄を並行管理し、「いつ何を買い、いつ売って、次に何に乗り換えるか」
のタイムラインを表示する。資金を常に「次に跳ねる銘柄」に入れておく戦略。

考え方:
- 短期売買はアルゴに負ける
- イベントの半年前に底値で仕込む → アルゴの対象外
- 跳ねたら売って次の銘柄に回す → 複利で資金を回転させる
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta
import plotly.graph_objects as go

from src.ui.components import COLORS, render_header

st.set_page_config(page_title="ポートフォリオ", page_icon="📋", layout="wide")
render_header()
st.title("ポートフォリオ管理")
st.markdown("複数銘柄の並行運用タイムライン")

# --- セッションステートでポートフォリオ管理 ---
if "portfolio" not in st.session_state:
    st.session_state.portfolio = []

with st.sidebar:
    st.markdown("### 銘柄を追加")
    code = st.text_input("銘柄コード", placeholder="4572")
    name = st.text_input("銘柄名", placeholder="カルナバイオ")
    buy_price = st.number_input("買い目安（円）", min_value=0, value=0)
    buy_date = st.date_input("仕込み時期", value=date.today())
    event_name = st.text_input("跳ねるイベント", placeholder="ASCO学会発表")
    event_date = st.date_input("イベント時期", value=date.today() + timedelta(days=90))
    sell_price = st.number_input("売り目標（円）", min_value=0, value=0)
    investment = st.number_input("投資額（万円）", min_value=0, value=100)

    if st.button("追加", type="primary", use_container_width=True):
        if code and buy_price > 0:
            st.session_state.portfolio.append({
                "code": code,
                "name": name or code,
                "buy_price": buy_price,
                "buy_date": buy_date.isoformat(),
                "event_name": event_name,
                "event_date": event_date.isoformat(),
                "sell_price": sell_price,
                "investment": investment,
                "status": "waiting",  # waiting / holding / sold
            })
            st.success(f"{name or code} を追加")

    st.markdown("---")
    if st.button("サンプルデータ", use_container_width=True):
        st.session_state.portfolio = [
            {
                "code": "4572", "name": "カルナバイオ",
                "buy_price": 350, "buy_date": "2026-04-01",
                "event_name": "ASCO学会", "event_date": "2026-06-05",
                "sell_price": 700, "investment": 100, "status": "holding",
            },
            {
                "code": "6526", "name": "ソシオネクスト",
                "buy_price": 1800, "buy_date": "2026-04-15",
                "event_name": "TSMC決算波及", "event_date": "2026-07-15",
                "sell_price": 3500, "investment": 150, "status": "waiting",
            },
            {
                "code": "3133", "name": "海帆",
                "buy_price": 300, "buy_date": "2026-05-01",
                "event_name": "AI電力テーマ再燃", "event_date": "2026-08-01",
                "sell_price": 600, "investment": 80, "status": "waiting",
            },
        ]
        st.rerun()

portfolio = st.session_state.portfolio

if not portfolio:
    st.markdown("""
    <div style="background:#1A1F2E;padding:40px;border-radius:16px;text-align:center;margin-top:40px">
        <div style="font-size:3em;margin-bottom:16px">📋</div>
        <div style="font-size:1.3em;font-weight:600;margin-bottom:8px">銘柄を追加してタイムラインを作成</div>
        <div style="color:#90A4AE;line-height:1.8">
            サイドバーから銘柄・仕込み時期・イベント・売り目標を入力<br>
            または「サンプルデータ」で体験
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# --- タイムライン表示 ---
st.markdown("### タイムライン")

fig = go.Figure()
today = date.today()

for i, p in enumerate(portfolio):
    buy_d = date.fromisoformat(p["buy_date"])
    event_d = date.fromisoformat(p["event_date"])
    sell_d = event_d + timedelta(days=7)  # イベント後1週間で利確想定

    color = COLORS["buy"] if p["status"] == "holding" else COLORS["caution"] if p["status"] == "waiting" else COLORS["neutral"]

    # 仕込み期間
    fig.add_trace(go.Scatter(
        x=[buy_d, event_d],
        y=[i, i],
        mode="lines",
        line=dict(color=color, width=8),
        name=f"{p['name']}（保有期間）",
        showlegend=False,
        hovertext=f"{p['name']}: ¥{p['buy_price']:,}で仕込み → {p['event_name']}",
    ))

    # イベント
    fig.add_trace(go.Scatter(
        x=[event_d],
        y=[i],
        mode="markers+text",
        marker=dict(size=14, color=COLORS["sell"], symbol="star"),
        text=[p["event_name"]],
        textposition="top center",
        textfont=dict(size=10, color=COLORS["text_primary"]),
        showlegend=False,
    ))

    # 銘柄名ラベル
    fig.add_annotation(
        x=buy_d,
        y=i,
        text=f"<b>{p['name']}</b> ¥{p['buy_price']:,}→¥{p['sell_price']:,}",
        showarrow=False,
        xanchor="right",
        xshift=-10,
        font=dict(size=11, color=COLORS["text_primary"]),
    )

# 今日の線
fig.add_vline(x=today.isoformat(), line_dash="dot", line_color=COLORS["caution"],
              annotation_text="今日", annotation_position="top")

fig.update_layout(
    height=max(300, len(portfolio) * 80),
    yaxis=dict(visible=False),
    xaxis=dict(title=""),
    template="plotly_dark",
    paper_bgcolor="#0E1117",
    plot_bgcolor="#0E1117",
    margin=dict(l=200),
)
st.plotly_chart(fig, use_container_width=True)

# --- サマリーカード ---
st.markdown("### 運用サマリー")

total_investment = sum(p["investment"] for p in portfolio)
expected_returns = []
for p in portfolio:
    if p["buy_price"] > 0:
        ret = (p["sell_price"] - p["buy_price"]) / p["buy_price"]
        expected_returns.append(ret * p["investment"])

total_expected_profit = sum(expected_returns)
total_expected_return_pct = (total_expected_profit / total_investment * 100) if total_investment > 0 else 0

col1, col2, col3, col4 = st.columns(4)
col1.metric("投資総額", f"¥{total_investment}万")
col2.metric("期待利益", f"¥{total_expected_profit:,.0f}万", f"+{total_expected_return_pct:.0f}%")
col3.metric("銘柄数", len(portfolio))
next_event = min(portfolio, key=lambda p: p["event_date"])
days_to_next = (date.fromisoformat(next_event["event_date"]) - today).days
col4.metric("次のイベント", f"{next_event['event_name']}", f"あと{days_to_next}日")

# --- 個別カード ---
st.markdown("### 個別銘柄")
for i, p in enumerate(portfolio):
    buy_d = date.fromisoformat(p["buy_date"])
    event_d = date.fromisoformat(p["event_date"])
    days_to_event = (event_d - today).days
    hold_days = (event_d - buy_d).days
    ret_pct = (p["sell_price"] - p["buy_price"]) / p["buy_price"] * 100 if p["buy_price"] > 0 else 0
    profit = p["investment"] * ret_pct / 100

    status_label = {"waiting": "仕込み待ち", "holding": "保有中", "sold": "売却済み"}
    status_color = {"waiting": COLORS["caution"], "holding": COLORS["buy"], "sold": COLORS["neutral"]}

    st.markdown(f"""
    <div style="background:#1A1F2E;padding:16px;border-radius:10px;margin-bottom:8px;border-left:4px solid {status_color[p['status']]}">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap">
            <div>
                <span style="font-size:1.2em;font-weight:700">{p['name']}</span>
                <span style="color:{COLORS['text_secondary']};margin-left:6px">{p['code']}</span>
                <span style="background:{status_color[p['status']]};color:#fff;padding:2px 8px;border-radius:8px;font-size:0.8em;margin-left:8px">{status_label[p['status']]}</span>
            </div>
            <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:4px">
                <div style="text-align:center">
                    <div style="color:{COLORS['text_secondary']};font-size:0.75em">買い</div>
                    <div style="font-weight:600">¥{p['buy_price']:,}</div>
                </div>
                <div style="text-align:center">
                    <div style="color:{COLORS['buy']};font-size:0.75em">売り目標</div>
                    <div style="font-weight:600;color:{COLORS['buy']}">¥{p['sell_price']:,}</div>
                </div>
                <div style="text-align:center">
                    <div style="color:{COLORS['text_secondary']};font-size:0.75em">リターン</div>
                    <div style="font-weight:600;color:{COLORS['buy']}">+{ret_pct:.0f}%</div>
                </div>
                <div style="text-align:center">
                    <div style="color:{COLORS['text_secondary']};font-size:0.75em">利益</div>
                    <div style="font-weight:600">¥{profit:,.0f}万</div>
                </div>
                <div style="text-align:center">
                    <div style="color:{COLORS['caution']};font-size:0.75em">イベント</div>
                    <div style="font-weight:600">{p['event_name']}</div>
                    <div style="color:{COLORS['text_secondary']};font-size:0.75em">あと{days_to_event}日</div>
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# --- 資金回転シミュレーション ---
st.markdown("### 資金回転シミュレーション")
st.markdown("売却益を次の銘柄に回した場合の複利効果")

sorted_portfolio = sorted(portfolio, key=lambda p: p["event_date"])
running_capital = total_investment
capital_history = [{"event": "開始", "date": today.isoformat(), "capital": running_capital}]

for p in sorted_portfolio:
    if p["buy_price"] > 0:
        ret = (p["sell_price"] - p["buy_price"]) / p["buy_price"]
        # この銘柄に割り当てた分のリターンを全体に適用
        allocation_ratio = p["investment"] / total_investment if total_investment > 0 else 0
        running_capital *= (1 + ret * allocation_ratio)
        capital_history.append({
            "event": f"{p['name']}売却",
            "date": p["event_date"],
            "capital": round(running_capital),
        })

if len(capital_history) > 1:
    df_capital = pd.DataFrame(capital_history)
    final = running_capital
    total_ret = (final - total_investment) / total_investment * 100

    col1, col2 = st.columns(2)
    col1.metric("最終資産", f"¥{final:,.0f}万", f"+{total_ret:.0f}%")
    col2.metric("倍率", f"{final/total_investment:.1f}倍")

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(
        x=[c["date"] for c in capital_history],
        y=[c["capital"] for c in capital_history],
        mode="lines+markers+text",
        text=[c["event"] for c in capital_history],
        textposition="top center",
        line=dict(color=COLORS["buy"], width=3),
        marker=dict(size=10),
    ))
    fig2.update_layout(
        height=300,
        yaxis_title="資産（万円）",
        template="plotly_dark",
        paper_bgcolor="#0E1117",
        plot_bgcolor="#0E1117",
    )
    st.plotly_chart(fig2, use_container_width=True)
