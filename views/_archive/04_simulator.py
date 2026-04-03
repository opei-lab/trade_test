"""売買シミュレーション画面

エントリー/イグジット価格のシミュレーションと複利計算。
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from src.ui.components import render_header

st.set_page_config(page_title="シミュレーター", page_icon="💰", layout="wide")
render_header()
st.title("売買シミュレーター")

tab1, tab2 = st.tabs(["複利シミュレーション", "銘柄別シミュレーション"])

# --- Tab 1: 複利シミュレーション ---
with tab1:
    st.markdown("### 複利で資産がどう増えるか")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        initial_capital = st.number_input("初期資金（万円）", value=100, min_value=1)
    with col2:
        win_rate = st.slider("勝率 (%)", 10, 90, 60)
    with col3:
        avg_win = st.slider("平均利益倍率", 1.1, 10.0, 2.0, 0.1)
    with col4:
        avg_loss = st.slider("平均損失率", 0.1, 0.9, 0.3, 0.05)

    col5, col6 = st.columns(2)
    with col5:
        trades_per_year = st.slider("年間トレード回数", 1, 50, 12)
    with col6:
        years = st.slider("シミュレーション年数", 1, 10, 3)

    if st.button("シミュレーション実行", type="primary"):
        # モンテカルロシミュレーション（1000回）
        n_sims = 1000
        total_trades = trades_per_year * years
        all_paths = []

        for _ in range(n_sims):
            capital = initial_capital
            path = [capital]
            for _ in range(total_trades):
                if np.random.random() < win_rate / 100:
                    capital *= avg_win
                else:
                    capital *= (1 - avg_loss)
                path.append(capital)
            all_paths.append(path)

        paths_array = np.array(all_paths)

        # パーセンタイル計算
        p10 = np.percentile(paths_array, 10, axis=0)
        p25 = np.percentile(paths_array, 25, axis=0)
        p50 = np.percentile(paths_array, 50, axis=0)
        p75 = np.percentile(paths_array, 75, axis=0)
        p90 = np.percentile(paths_array, 90, axis=0)

        x = list(range(total_trades + 1))

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x, y=p90, fill=None, mode="lines", line=dict(color="rgba(0,100,255,0.1)"), name="90パーセンタイル"))
        fig.add_trace(go.Scatter(x=x, y=p10, fill="tonexty", mode="lines", line=dict(color="rgba(0,100,255,0.1)"), name="10パーセンタイル"))
        fig.add_trace(go.Scatter(x=x, y=p75, fill=None, mode="lines", line=dict(color="rgba(0,100,255,0.3)"), name="75パーセンタイル"))
        fig.add_trace(go.Scatter(x=x, y=p25, fill="tonexty", mode="lines", line=dict(color="rgba(0,100,255,0.3)"), name="25パーセンタイル"))
        fig.add_trace(go.Scatter(x=x, y=p50, mode="lines", line=dict(color="blue", width=3), name="中央値"))

        fig.update_layout(
            title="資産推移（モンテカルロ・シミュレーション 1000回）",
            xaxis_title="トレード回数",
            yaxis_title="資産（万円）",
            yaxis_type="log",
            height=500,
        )
        st.plotly_chart(fig, use_container_width=True)

        # 結果サマリー
        col1, col2, col3 = st.columns(3)
        col1.metric("中央値", f"¥{p50[-1]:,.0f}万", f"{(p50[-1]/initial_capital - 1)*100:+.0f}%")
        col2.metric("上位25%", f"¥{p75[-1]:,.0f}万", f"{(p75[-1]/initial_capital - 1)*100:+.0f}%")
        col3.metric("下位25%", f"¥{p25[-1]:,.0f}万", f"{(p25[-1]/initial_capital - 1)*100:+.0f}%")

        # 期待値計算
        expected_per_trade = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * (1 - avg_loss))
        st.markdown(f"**1トレードあたりの期待値: {expected_per_trade:.3f}x** "
                    f"({'プラス期待値' if expected_per_trade > 1 else 'マイナス期待値'})")

        if expected_per_trade <= 1:
            st.warning("期待値が1以下です。勝率か利益倍率を上げるか、損失率を下げる必要があります。")


# --- Tab 2: 銘柄別シミュレーション ---
with tab2:
    st.markdown("### 個別銘柄の売買シミュレーション")

    from src.data.price import fetch_price
    from src.analysis.supply import calc_supply_score

    code = st.text_input("銘柄コード", value="4572", key="sim_code")
    if st.button("分析", key="sim_analyze"):
        df = fetch_price(code.strip(), period_days=730)
        if df.empty:
            st.error("データ取得失敗")
            st.stop()

        supply = calc_supply_score(df)
        current = float(df["Close"].iloc[-1])

        # 底値・上値の推定
        close = df["Close"]
        rolling_low = close.rolling(60).min()
        rolling_high = close.rolling(60).max()

        estimated_bottom = float(rolling_low.iloc[-1])
        estimated_top = float(rolling_high.iloc[-1])

        # 過去の反発パターンから目標倍率を推定
        # 過去の底値からの反発倍率を計算
        lows = close.rolling(20).min()
        highs = close.rolling(20).max()
        bounce_ratios = highs / lows
        avg_bounce = float(bounce_ratios.dropna().median())

        st.markdown(f"**現在値:** ¥{current:,.0f}")

        col1, col2, col3 = st.columns(3)
        col1.metric("推定底値", f"¥{estimated_bottom:,.0f}", f"{(estimated_bottom/current - 1)*100:+.1f}%")
        col2.metric("推定上値", f"¥{estimated_top:,.0f}", f"{(estimated_top/current - 1)*100:+.1f}%")
        col3.metric("平均反発倍率", f"{avg_bounce:.2f}x")

        st.markdown("#### シミュレーション")
        entry = st.number_input("エントリー価格", value=int(estimated_bottom), key="entry")
        target = st.number_input("目標価格", value=int(estimated_bottom * avg_bounce), key="target")
        stop_loss = st.number_input("損切り価格", value=int(estimated_bottom * 0.9), key="sl")
        shares = st.number_input("株数", value=100, min_value=1, key="shares")

        profit = (target - entry) * shares
        loss = (entry - stop_loss) * shares
        risk_reward = profit / loss if loss > 0 else 0

        col1, col2, col3 = st.columns(3)
        col1.metric("利益（目標到達時）", f"¥{profit:,.0f}", f"+{(target/entry - 1)*100:.1f}%")
        col2.metric("損失（損切り時）", f"¥{-loss:,.0f}", f"-{(entry - stop_loss)/entry*100:.1f}%")
        col3.metric("リスクリワード比", f"{risk_reward:.2f}")

        if risk_reward < 2:
            st.warning("リスクリワード比が2未満です。エントリーを見送るか条件を見直してください。")
