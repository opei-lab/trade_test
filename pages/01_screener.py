"""おすすめ銘柄画面

3つのセクション:
1. ウォッチ中の銘柄（アクティブ。売買プラン付き）
2. プランから外れた銘柄（了解で消す）
3. 新しい候補（スクリーニング結果。ウォッチに追加）
"""

import streamlit as st
import pandas as pd
from datetime import date

from src.data.stocklist import fetch_stocklist, get_growth_stocks, get_stocks_by_sector
from src.data.price import fetch_price
from src.strategy.screener import screen_stocks
from src.strategy.deep_analysis import run_deep_analysis
from src.strategy.report import generate_report
from src.strategy.portfolio_router import classify_strategy, plan_relay_route, format_portfolio_plan
from src.strategy.cache import save_screen_results, load_screen_cache, get_cache_info
from src.data.watchlist import get_watchlist_summary, add_from_screening, remove_from_watchlist, update_from_screening
from src.scheduler.background import get_scan_status
from src.ui.components import COLORS, render_header

st.set_page_config(page_title="おすすめ銘柄", page_icon="🔍", layout="wide")
render_header()

buy_c = COLORS["buy"]
sell_c = COLORS["sell"]
info_c = COLORS["info"]
sec_c = COLORS["text_secondary"]
warn_c = COLORS["caution"]

# --- サイドバー ---
with st.sidebar:
    st.markdown("---")
    with st.expander("詳細設定", expanded=False):
        scan_mode = st.radio("スキャン対象", ["おまかせ", "全市場", "業種指定", "銘柄指定"])
        if scan_mode == "業種指定":
            sector = st.selectbox("業種", ["医薬品", "情報・通信業", "電気機器", "機械", "化学", "サービス業", "小売業"])
        elif scan_mode == "銘柄指定":
            codes_input = st.text_area("銘柄コード", value="4572\n3133\n6526", height=80)
        max_price = st.number_input("株価上限（円）", value=1000, min_value=100, step=100)
        capital = st.number_input("投資資金（万円）", value=100, min_value=10, step=10)

    run = st.button("🔍 スキャン実行", type="primary", use_container_width=True)

# --- バックグラウンドスキャン状態 ---
scan_status = get_scan_status()
if scan_status["running"]:
    st.info(f"スキャン中: {scan_status['progress']}")


# ============================
# セクション1: ウォッチ中の銘柄
# ============================
watchlist = get_watchlist_summary()
active = [w for w in watchlist if w["status"] in ("action", "attention", "watching")]
deviated = [w for w in watchlist if w.get("deviation_severity") in ("critical", "warning")]

if active:
    st.markdown("## ウォッチ銘柄")

    for w in active:
        status_icon = {"action": "🔴", "attention": "🟡", "watching": "⚪"}.get(w["status"], "⚪")
        status_label = {"action": "買い検討", "attention": "注視中", "watching": "監視中"}.get(w["status"], "")

        why = " / ".join(w.get("why", [])[:3]) if w.get("why") else "—"
        exit_event = w.get("exit_event", "")
        target_date = w.get("target_date", "")
        exit_info = f"{exit_event}" if exit_event else "未設定"
        if target_date:
            exit_info += f"（{target_date[:10]}）"

        st.markdown(f"""
        <div class="stock-card" style="border-left-color:{buy_c if w['status'] == 'action' else warn_c if w['status'] == 'attention' else sec_c}">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:10px">
                <div>
                    <span style="font-size:1.2em;font-weight:700">{status_icon} {w['name']}</span>
                    <span style="color:{sec_c};margin-left:4px">{w['code']}</span>
                    <span class="badge" style="background:#2A2F3E;color:{sec_c};margin-left:6px">{status_label}</span>
                </div>
                <div style="display:flex;gap:16px;font-size:0.9em">
                    <div style="text-align:center">
                        <div style="color:{sec_c};font-size:0.7em">現在値</div>
                        <div style="font-weight:600">¥{w['latest_price']:,.0f}</div>
                    </div>
                    <div style="text-align:center">
                        <div style="color:{buy_c};font-size:0.7em">売り目標</div>
                        <div style="font-weight:600;color:{buy_c}">¥{w['target']:,}</div>
                    </div>
                    <div style="text-align:center">
                        <div style="color:{sell_c};font-size:0.7em">損切り</div>
                        <div style="font-weight:600;color:{sell_c}">¥{w['stop_loss']:,}</div>
                    </div>
                </div>
            </div>
            <div style="font-size:0.85em">
                <span style="color:{warn_c}">なぜ: {why}</span><br>
                <span style="color:{sec_c}">出口: {exit_info}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # 詳細分析（展開）
        with st.expander(f"📈 {w['name']} 詳細分析"):
            try:
                _df = fetch_price(w["code"], period_days=365)
                if _df is not None and not _df.empty:
                    import plotly.graph_objects as go
                    _fig = go.Figure(go.Candlestick(
                        x=_df.index, open=_df["Open"], high=_df["High"],
                        low=_df["Low"], close=_df["Close"],
                        increasing_line_color=buy_c, decreasing_line_color=sell_c,
                    ))
                    if w.get("target"):
                        _fig.add_hline(y=w["target"], line_dash="dot", line_color=buy_c, annotation_text="売り目標")
                    if w.get("stop_loss"):
                        _fig.add_hline(y=w["stop_loss"], line_dash="dot", line_color=sell_c, annotation_text="損切り")
                    _fig.update_layout(
                        height=300, xaxis_rangeslider_visible=False,
                        template="plotly_dark", paper_bgcolor="#0E1117", plot_bgcolor="#0E1117",
                        margin=dict(l=40, r=20, t=10, b=20),
                    )
                    st.plotly_chart(_fig, use_container_width=True)
            except Exception:
                pass


# ============================
# セクション2: プランから外れた銘柄
# ============================
if deviated:
    st.markdown("---")
    st.markdown("## ⚠ プランから外れた銘柄")

    for w in deviated:
        devs = w.get("deviations", [])
        dev_text = " / ".join(devs) if devs else "不明"
        severity_color = sell_c if w["deviation_severity"] == "critical" else warn_c

        col1, col2 = st.columns([5, 1])
        with col1:
            st.markdown(f"""
            <div style="background:#1A1F2E;padding:10px 14px;border-radius:8px;border-left:3px solid {severity_color}">
                <span style="font-weight:600">{w['name']}</span>
                <span style="color:{sec_c};margin-left:4px">{w['code']}</span>
                <span style="color:{sec_c};margin-left:8px">¥{w['latest_price']:,.0f}</span>
                <div style="color:{severity_color};font-size:0.85em;margin-top:4px">{dev_text}</div>
            </div>
            """, unsafe_allow_html=True)
        with col2:
            if st.button("了解", key=f"dismiss_{w['code']}"):
                remove_from_watchlist(w["code"], reason=dev_text)
                st.rerun()


# ============================
# セクション3: 新しい候補
# ============================
cached = load_screen_cache("おまかせ" if "scan_mode" not in dir() else scan_mode)
cache_info = get_cache_info()

# 手動スキャン実行
if run:
    from src.strategy.cache import clear_cache
    clear_cache()
    import threading
    from src.scheduler.background import _run_scan
    threading.Thread(target=_run_scan, daemon=True).start()
    st.info("スキャンを開始しました。画面は自由に操作できます。数分後に自動更新されます。")
    import time
    time.sleep(3)
    st.rerun()

if cached:
    # ウォッチ済みの銘柄を除外
    watch_codes = set(w["code"] for w in watchlist)
    new_candidates = [r for r in cached if r.get("code") not in watch_codes]

    if new_candidates:
        st.markdown("---")
        ts = cache_info.get("timestamp", "")[:16].replace("T", " ") if cache_info else ""
        st.markdown(f"## 新しい候補")
        st.caption(f"{ts} のスキャン結果")

        for r in new_candidates:
            conv = r.get("conviction", {}) if isinstance(r.get("conviction"), dict) else {}
            grade = conv.get("grade", "?")
            stars_n = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}.get(grade, 1)
            stars = "★" * stars_n + "☆" * (5 - stars_n)
            grade_color = {"S": "#FFD700", "A": buy_c, "B": warn_c}.get(grade, sec_c)

            current = r.get("current_price", 0)
            entry = r.get("entry", 0)
            target = r.get("target", 0)
            stop = r.get("stop_loss", 0)
            reward = r.get("reward_pct", 0)
            rr = r.get("risk_reward", 0)
            is_best = r.get("is_best_pattern", False)

            passed = conv.get("passed", [])
            why = " / ".join([p.get("name", "") for p in passed if p.get("weight", 0) >= 4][:3]) or "—"

            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"""
                <div class="stock-card">
                    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:8px">
                        <div>
                            <span style="font-size:1.1em;font-weight:700">{r.get('name', r['code'])}</span>
                            <span style="color:{sec_c};margin-left:4px">{r['code']}</span>
                            <span style="color:{grade_color};margin-left:8px">{stars}</span>
                            {'<span class=\"badge\" style=\"background:#FFD700;color:#000;margin-left:6px\">勝ちパターン</span>' if is_best else ''}
                        </div>
                        <div style="display:flex;gap:14px;font-size:0.85em">
                            <div><span style="color:{sec_c};font-size:0.7em">現在値</span><br>¥{current:,.0f}</div>
                            <div><span style="color:{info_c};font-size:0.7em">買い</span><br><span style="color:{info_c}">¥{entry:,}</span></div>
                            <div><span style="color:{buy_c};font-size:0.7em">売り</span><br><span style="color:{buy_c}">¥{target:,} +{reward:.0f}%</span></div>
                            <div><span style="color:{sell_c};font-size:0.7em">損切</span><br><span style="color:{sell_c}">¥{stop:,}</span></div>
                        </div>
                    </div>
                    <div style="color:{warn_c};font-size:0.85em">{why}</div>
                </div>
                """, unsafe_allow_html=True)
            with col2:
                if st.button("ウォッチ", key=f"watch_{r['code']}"):
                    add_from_screening(r)
                    st.rerun()

            with st.expander(f"詳細レポート"):
                st.markdown(generate_report(r))

# ============================
# 渡り鳥タイムライン
# ============================
if active and len(active) >= 2:
    st.markdown("---")
    st.markdown("## 渡り鳥タイムライン")

    import plotly.graph_objects as go
    from datetime import timedelta

    today = date.today()
    fig = go.Figure()

    for i, w in enumerate(active):
        start = today
        exit_date_str = w.get("target_date", "")
        if exit_date_str:
            try:
                end = date.fromisoformat(exit_date_str[:10])
            except Exception:
                end = today + timedelta(days=60)
        else:
            end = today + timedelta(days=60)

        color = buy_c if w["status"] == "action" else warn_c if w["status"] == "attention" else sec_c

        # 保有期間バー
        fig.add_trace(go.Scatter(
            x=[start, end], y=[i, i], mode="lines",
            line=dict(color=color, width=12), showlegend=False,
            hovertext=f"{w['name']}: ¥{w.get('latest_price', 0):,.0f}→¥{w.get('target', 0):,}",
        ))

        # イベントマーカー
        if exit_date_str:
            fig.add_trace(go.Scatter(
                x=[end], y=[i], mode="markers+text",
                marker=dict(size=12, color=sell_c, symbol="star"),
                text=[w.get("exit_event", "")[:10]], textposition="top center",
                textfont=dict(size=9, color=COLORS["text_primary"]),
                showlegend=False,
            ))

        # 銘柄名
        fig.add_annotation(
            x=start, y=i, text=f"<b>{w['name']}</b> ¥{w.get('latest_price', 0):,.0f}→¥{w.get('target', 0):,}",
            showarrow=False, xanchor="right", xshift=-10,
            font=dict(size=10, color=COLORS["text_primary"]),
        )

    # 今日の線
    fig.add_vline(x=today.isoformat(), line_dash="dot", line_color=warn_c, annotation_text="今日")

    fig.update_layout(
        height=max(150, len(active) * 60 + 50),
        yaxis=dict(visible=False),
        xaxis=dict(title=""),
        template="plotly_dark",
        paper_bgcolor="#0E1117", plot_bgcolor="#0E1117",
        margin=dict(l=180, r=20, t=10, b=30),
    )
    st.plotly_chart(fig, use_container_width=True)

elif not active and not cached and not scan_status["running"]:
    st.markdown(f"""
    <div style="text-align:center;padding:40px 0">
        <div style="font-size:2em;margin-bottom:8px">🔍</div>
        <div style="color:{sec_c}">サイドバーの「スキャン実行」でスクリーニングを開始</div>
    </div>
    """, unsafe_allow_html=True)
