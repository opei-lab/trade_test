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
    with st.expander("スキャン対象", expanded=False):
        scan_mode = st.radio("対象", ["グロース市場", "全市場", "業種指定", "銘柄指定"], label_visibility="collapsed")
        if scan_mode == "業種指定":
            sector = st.selectbox("業種", ["医薬品", "情報・通信業", "電気機器", "機械", "化学", "サービス業", "小売業"])
        elif scan_mode == "銘柄指定":
            codes_input = st.text_area("銘柄コード", value="4572\n3133\n6526", height=80)

    run = st.button("🔍 スキャン実行", type="primary", use_container_width=True)

# スキャン状態
scan_status = get_scan_status()

# session_state
if "scan_results" not in st.session_state:
    st.session_state.scan_results = None

# ============================
# スキャン実行（他より先に処理。完了後にrerunで再描画）
# ============================
if run:
    st.session_state.scan_results = None
    st.markdown("### スキャン中...")

    from src.strategy.cache import save_screen_results as _save
    from src.data.stocklist import get_growth_stocks, fetch_stocklist, get_stocks_by_sector
    from src.strategy.screener import screen_stocks
    from src.strategy.deep_analysis import run_deep_analysis
    from src.data.watchlist import update_from_screening as _update_wl
    _mode = scan_mode if "scan_mode" in dir() else "グロース市場"

    if _mode == "全市場":
        _stocks = fetch_stocklist()
    elif _mode == "業種指定" and "sector" in dir():
        _stocks = get_stocks_by_sector(sector)
    elif _mode == "銘柄指定" and "codes_input" in dir():
        _codes = [c.strip() for c in codes_input.strip().split("\n") if c.strip()]
        import pandas as _pd
        _stocks = _pd.DataFrame({"code": _codes, "name": "", "market": "", "sector": ""})
    else:
        _stocks = get_growth_stocks()

    _codes_list = _stocks["code"].tolist()
    _name_map = dict(zip(_stocks["code"].astype(str), _stocks["name"]))

    _progress = st.progress(0, text="スキャン中...")
    _candidates = screen_stocks(
        _codes_list, min_score=0,
        progress_callback=lambda c, t, code: _progress.progress((c+1)/t, text=f"Stage 1: {code} ({c+1}/{t})"),
    )
    for _r in _candidates:
        _jpx = _name_map.get(_r["code"], "")
        if _jpx and _jpx.strip():
            _r["name"] = _jpx

    if _candidates:
        _progress.progress(1.0, text=f"Stage 2: {len(_candidates)}銘柄を分析中...")
        _results = run_deep_analysis(
            _candidates,
            progress_callback=lambda c, t, code: _progress.progress((c+1)/t, text=f"Stage 2: {code} ({c+1}/{t})"),
        )
        _save(_mode, _results)
        _update_wl(_results)
        st.session_state.scan_results = _results
    else:
        st.session_state.scan_results = []

    st.rerun()  # 再描画

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

        st.markdown(f"**{status_icon} {w['name']}** {w['code']} _{status_label}_")
        wc1, wc2, wc3 = st.columns(3)
        wc1.metric("現在値", f"¥{w['latest_price']:,.0f}")
        wc2.metric("売り目標", f"¥{w['target']:,}")
        wc3.metric("損切り", f"¥{w['stop_loss']:,}")
        st.caption(f"なぜ: {why} | 出口: {exit_info}")

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
            st.warning(f"**{w['name']}** {w['code']} ¥{w['latest_price']:,.0f} — {dev_text}")
        with col2:
            if st.button("了解", key=f"dismiss_{w['code']}"):
                remove_from_watchlist(w["code"], reason=dev_text)
                st.rerun()


# ============================
# セクション3: 新しい候補
# ============================

# session_stateにスキャン結果を保持
cached = st.session_state.scan_results
if cached is None:
    cached = load_screen_cache("グロース市場" if "scan_mode" not in dir() else scan_mode)
cache_info = get_cache_info()

    # (スキャン処理はページ先頭のif runブロックで実行済み)

if cached:
    # ウォッチ済みの銘柄を除外
    watch_codes = set(w["code"] for w in watchlist)
    new_candidates = [r for r in cached if r.get("code") not in watch_codes]

    if new_candidates:
        # 確度順（Stage 2でソート済み）。上位10件を表示、残りは折りたたみ
        top_n = new_candidates[:10]
        others = new_candidates[10:]

        st.markdown("---")
        ts = cache_info.get("timestamp", "")[:16].replace("T", " ") if cache_info else ""
        st.markdown(f"## 新しい候補（上位{len(top_n)}件 / {len(new_candidates)}件）")
        if ts:
            st.caption(f"{ts} のスキャン結果 · 確度順")

        for r in top_n:
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

            # 推奨度（確度スコアをパーセント表示 + 色分け）
            conv_score = conv.get("conviction_score", 0)
            if conv_score >= 70:
                rec_color = "🟢"
                rec_label = "強く推奨"
            elif conv_score >= 50:
                rec_color = "🟡"
                rec_label = "推奨"
            elif conv_score >= 30:
                rec_color = "🟠"
                rec_label = "検討"
            else:
                rec_color = "⚪"
                rec_label = "様子見"

            # アルゴフェーズ
            algo = r.get("algo_phase", "unknown")
            algo_label = {"pre_algo": "🔵静か", "algo_entering": "🟡動き始め", "algo_active": "🔴過熱", "algo_exiting": "⚫撤退中"}.get(algo, "")

            col1, col2 = st.columns([5, 1])
            with col1:
                # 1行目: 銘柄名 + 推奨度 + アルゴ
                badge = " 🏆勝ちパターン" if is_best else ""
                st.markdown(f"**{r.get('name', r['code'])}** {r['code']} — {rec_color} **{rec_label} {conv_score}%** {algo_label}{badge}")

                # 2行目: 価格
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("現在値", f"¥{current:,.0f}")
                c2.metric("買い", f"¥{entry:,}")
                c3.metric("売り", f"¥{target:,}", f"+{reward:.0f}%")
                c4.metric("損切", f"¥{stop:,}")

                # 3行目: 理由
                st.caption(f"根拠: {why}")

                # 4行目: シナリオ
                scenario_text = r.get("scenario_text", "")
                if scenario_text:
                    st.caption(scenario_text[:120])
            with col2:
                if st.button("ウォッチ", key=f"watch_{r['code']}"):
                    add_from_screening(r)
                    st.rerun()

            with st.expander(f"詳細レポート"):
                # 市場構造レポート（需給・しこり・大口・売りライン・損切りライン）
                struct_report = r.get("structure_report", "")
                if struct_report:
                    st.markdown(struct_report)
                    st.markdown("---")
                st.markdown(generate_report(r))

        if others:
            with st.expander(f"その他の候補（{len(others)}件）"):
                for r in others:
                    conv = r.get("conviction", {}) if isinstance(r.get("conviction"), dict) else {}
                    passed = conv.get("passed", [])
                    why_o = " / ".join([p.get("name", "") for p in passed if p.get("weight", 0) >= 4][:3]) or "—"
                    st.markdown(f"**{r.get('name', r['code'])}** {r['code']} — ¥{r.get('current_price', 0):,.0f} → ¥{r.get('target', 0):,}（+{r.get('reward_pct', 0):.0f}%） _{why_o}_")

# ============================
# 渡り鳥プラン
# ============================
if active:
    st.markdown("---")
    st.markdown("## 渡り鳥プラン")

    from datetime import timedelta
    today = date.today()
    sorted_active = sorted(active, key=lambda w: w.get("target_date", "9999"))

    running_capital = 100
    for i, w in enumerate(sorted_active):
        exit_event = w.get("exit_event", "")
        target_date_str = w.get("target_date", "")
        target_val = w.get("target", 0)
        latest = w.get("latest_price", 0)

        if target_date_str:
            try:
                days_left = (date.fromisoformat(target_date_str[:10]) - today).days
                period_str = f"{exit_event}（{days_left}日後）" if exit_event else f"{days_left}日後"
            except Exception:
                period_str = exit_event or "期間未定"
        else:
            period_str = exit_event or "期間未定"

        reward = (target_val - latest) / latest * 100 if latest > 0 else 0
        next_capital = running_capital * (1 + reward / 100)

        st.markdown(f"**{i+1}. {w['name']}** {w['code']} — ¥{latest:,.0f}→¥{target_val:,}（+{reward:.0f}%） | {period_str} | ¥{running_capital:.0f}万→¥{next_capital:.0f}万")
        running_capital = next_capital

    st.markdown(f"**合計: ¥100万→¥{running_capital:.0f}万（{running_capital/100:.1f}倍）**")

elif not active and not cached and not scan_status["running"]:
    st.markdown(f"""
    <div style="text-align:center;padding:40px 0">
        <div style="font-size:2em;margin-bottom:8px">🔍</div>
        <div style="color:{sec_c}">サイドバーの「スキャン実行」でスクリーニングを開始</div>
    </div>
    """, unsafe_allow_html=True)
