"""おすすめ銘柄画面

考えずに投資できることを目標にしたUI。
表示: 何を → いくらで買い → いつ売り → なぜ
"""

import streamlit as st
import pandas as pd
from datetime import date

from src.data.stocklist import fetch_stocklist, get_growth_stocks, get_stocks_by_sector
from src.data.price import fetch_price
from src.strategy.screener import screen_stocks
from src.strategy.deep_analysis import run_deep_analysis
from src.strategy.report import generate_report
from src.strategy.trade_plan import generate_detailed_trade_plan, format_trade_plan_full
from src.strategy.portfolio_router import classify_strategy, plan_relay_route, format_portfolio_plan
from src.strategy.cache import save_screen_results, load_screen_cache, get_cache_info, clear_cache
from src.data.watchlist import update_from_screening, get_watchlist_summary
from src.ui.components import COLORS, PHASE_CONFIG, render_header

st.set_page_config(page_title="おすすめ銘柄", page_icon="🔍", layout="wide")
render_header()

# --- 設定（サイドバー、通常は触らなくていい） ---
scan_mode = "おまかせ"
max_price = 1000
max_stocks = 2
capital = 100

with st.sidebar:
    with st.expander("詳細設定", expanded=False):
        scan_mode = st.radio("スキャン対象", ["おまかせ", "全市場", "業種指定", "銘柄指定"])
        if scan_mode == "業種指定":
            sector = st.selectbox("業種", ["医薬品", "情報・通信業", "電気機器", "機械", "化学", "サービス業", "小売業"])
        elif scan_mode == "銘柄指定":
            codes_input = st.text_area("銘柄コード", value="4572\n3133\n6526", height=80)
        max_price = st.number_input("株価上限（円）", value=1000, min_value=100, step=100)
        max_stocks = st.slider("集中度（銘柄数）", 1, 5, 2)
        capital = st.number_input("投資資金（万円）", value=100, min_value=10, step=10)

# --- サイドバーにボタン ---
with st.sidebar:
    run = st.button("🔍 おすすめ銘柄を探す", type="primary", use_container_width=True)

# --- メインエリア ---
buy_c = COLORS["buy"]
sell_c = COLORS["sell"]
info_c = COLORS["info"]
sec_c = COLORS["text_secondary"]
warn_c = COLORS["caution"]

# キャッシュ確認
cached = load_screen_cache(scan_mode)
cache_info = get_cache_info()

results = None

# キャッシュがあれば即表示（ボタンを押さなくても）
if cached and not run:
    results = cached
    ts = cache_info.get("timestamp", "")[:16].replace("T", " ") if cache_info else ""
    st.caption(f"本日 {ts} のスキャン結果 ・ ボタンを押すと再スキャン")

if results is None and not run:
    # ランディング
    st.markdown(f"""
    <div style="text-align:center;padding:60px 0">
        <div style="font-size:3em;margin-bottom:12px">🔍</div>
        <div style="font-size:1.3em;font-weight:600">「おすすめ銘柄を探す」で開始</div>
        <div style="color:{sec_c};margin-top:8px">確度が高い銘柄だけを厳選して提示します</div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# --- スキャン実行 ---
if results is None:
    # Stage 1
    with st.spinner("銘柄リストを取得中..."):
        if scan_mode == "おまかせ":
            stock_df = get_growth_stocks()
        elif scan_mode == "業種指定":
            stock_df = get_stocks_by_sector(sector)
        elif scan_mode == "銘柄指定":
            codes = [c.strip() for c in codes_input.strip().split("\n") if c.strip()]
            stock_df = pd.DataFrame({"code": codes, "name": "", "market": "", "sector": ""})
        else:
            stock_df = fetch_stocklist()

    codes_list = stock_df["code"].tolist()
    name_map = dict(zip(stock_df["code"].astype(str), stock_df["name"]))

    progress = st.progress(0, text="Stage 1: 高速スキャン中...")
    candidates = screen_stocks(
        codes_list, min_score=0,
        progress_callback=lambda c, t, code: progress.progress((c+1)/t, text=f"Stage 1: {code} ({c+1}/{t})"),
    )
    for r in candidates:
        jpx = name_map.get(r["code"], "")
        if jpx and jpx.strip():
            r["name"] = jpx

    if not candidates:
        progress.empty()
        st.info("条件に合う銘柄が見つかりませんでした")
        st.stop()

    progress.progress(1.0, text=f"Stage 2: {len(candidates)}銘柄を深層分析中...")
    results = run_deep_analysis(
        candidates,
        progress_callback=lambda c, t, code: progress.progress((c+1)/t, text=f"Stage 2: {code} ({c+1}/{t})"),
    )
    progress.empty()
    save_screen_results(scan_mode, results)
    update_from_screening(results)  # ウォッチリスト自動更新

if not results:
    st.info("高確度の銘柄が見つかりませんでした。条件が揃うまで待つのも戦略です。")
    st.stop()

# --- 株価フィルタ ---
for r in results:
    if not r.get("name"):
        r["name"] = r["code"]

filtered = [r for r in results if r.get("current_price", 9999) <= max_price]
if not filtered:
    filtered = results  # フィルタで全滅したら全表示

# --- 銘柄カード ---
st.markdown(f"### {len(filtered)}銘柄を厳選")

for r in filtered:
    conv = r.get("conviction", {}) if isinstance(r.get("conviction"), dict) else {}
    grade = conv.get("grade", "?")
    conv_score = conv.get("conviction_score", 0)
    stars_n = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}.get(grade, 1)
    stars = "★" * stars_n + "☆" * (5 - stars_n)
    grade_color = {"S": "#FFD700", "A": buy_c, "B": warn_c, "C": sec_c}.get(grade, sec_c)

    current = r.get("current_price", 0)
    entry = r.get("entry", 0)
    target = r.get("target", 0)
    stop = r.get("stop_loss", 0)
    rr = r.get("risk_reward", 0)
    reward = r.get("reward_pct", 0)
    max_dd = r.get("max_downside_pct", 99)

    # なぜ上がるか
    passed = conv.get("passed", [])
    why_list = [p.get("name", "") for p in passed if p.get("weight", 0) >= 4][:4]
    why = " / ".join(why_list) if why_list else "—"

    # 下方リスク
    downside = f"下方-{max_dd:.0f}%（限定的）" if max_dd < 20 else f"下方-{max_dd:.0f}%"

    # 期間
    exp = r.get("expectation", {}) if isinstance(r.get("expectation"), dict) else {}
    period = exp.get("period_source", "期間未定")
    target_days = exp.get("target_days")

    # パターン判定
    is_best = r.get("is_best_pattern", False)
    is_good = r.get("is_good_pattern", False)

    # タイミング
    timing = r.get("timing", "WAIT")
    timing_label = {"NOW": "今が買い時", "NEAR": "もうすぐ", "WAIT": "待ち"}.get(timing, "—")
    timing_bg = {"NOW": buy_c, "NEAR": warn_c, "WAIT": sec_c}.get(timing, sec_c)

    # 期間の短縮表示
    period_short = ""
    if target_days:
        period_short = f"{target_days}日"
    elif period and period != "期間未定":
        period_short = period[:20]

    st.markdown(f"""
    <div class="stock-card" style="border-left-color:{timing_bg}">
        <div class="card-header">
            <div>
                <span style="font-size:1.3em;font-weight:700">{r['name']}</span>
                <span style="color:{sec_c};margin-left:6px;font-size:0.85em">{r['code']}</span>
                <span style="color:{grade_color};margin-left:10px;font-size:1em;letter-spacing:1px">{stars}</span>
            </div>
            <div style="display:flex;gap:6px;align-items:center">
                {'<span class=\"badge\" style=\"background:#FFD700;color:#000\">勝ちパターン</span>' if is_best else '<span class=\"badge\" style=\"background:#2A4A3A;color:' + buy_c + '\">有望</span>' if is_good else ''}
                <span class="badge" style="background:{timing_bg}">{timing_label}</span>
                {'<span class=\"badge\" style=\"background:#2A2F3E;color:' + sec_c + '\">' + period_short + '</span>' if period_short else ''}
            </div>
        </div>

        <div class="card-prices">
            <div class="card-price-item">
                <div class="card-price-label" style="color:{sec_c}">現在値</div>
                <div class="card-price-value">¥{current:,.0f}</div>
            </div>
            <div class="card-price-item">
                <div class="card-price-label" style="color:{info_c}">買い目安</div>
                <div class="card-price-value" style="color:{info_c}">¥{entry:,}</div>
            </div>
            <div class="card-price-item">
                <div class="card-price-label" style="color:{buy_c}">売り目標</div>
                <div class="card-price-value" style="color:{buy_c}">¥{target:,}</div>
                <div style="color:{buy_c};font-size:0.75em">+{reward:.0f}%</div>
            </div>
            <div class="card-price-item">
                <div class="card-price-label" style="color:{sell_c}">損切り</div>
                <div class="card-price-value" style="color:{sell_c}">¥{stop:,}</div>
            </div>
        </div>

        <div class="card-reason">
            <div style="color:{warn_c};font-weight:600;font-size:0.9em">{why}</div>
            <div style="color:{sec_c};font-size:0.8em;margin-top:6px">{downside} &nbsp;|&nbsp; RR比 {rr:.1f} &nbsp;|&nbsp; 確度 {grade}({conv_score}pt){' &nbsp;|&nbsp; ML予測 ' + str(round(r.get('ml_win_prob', 0) * 100)) + '%' if r.get('ml_win_prob') is not None else ''}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    with st.expander(f"📈 {r.get('name', '')} を深堀り"):
        # analysisと同等のリッチな情報を展開表示
        st.markdown(generate_report(r))

        # チャートも表示
        try:
            import plotly.graph_objects as go
            _df = fetch_price(r["code"], period_days=365) if "fetch_price" in dir() else None
            if _df is not None and not _df.empty:
                _fig = go.Figure(go.Candlestick(
                    x=_df.index, open=_df["Open"], high=_df["High"],
                    low=_df["Low"], close=_df["Close"],
                    increasing_line_color=buy_c, decreasing_line_color=sell_c,
                ))
                _fig.add_hline(y=entry, line_dash="dot", line_color=info_c, annotation_text="買い")
                _fig.add_hline(y=target, line_dash="dot", line_color=buy_c, annotation_text="売り")
                _fig.add_hline(y=stop, line_dash="dot", line_color=sell_c, annotation_text="損切")
                _fig.update_layout(
                    height=350, xaxis_rangeslider_visible=False,
                    template="plotly_dark", paper_bgcolor="#0E1117", plot_bgcolor="#0E1117",
                    margin=dict(l=40, r=20, t=10, b=20),
                )
                st.plotly_chart(_fig, use_container_width=True)
        except Exception:
            pass

# --- 渡り鳥プラン ---
st.markdown("---")

all_for_plan = filtered
swing_picks = []
long_picks = []
for r in all_for_plan:
    strat = classify_strategy(r)
    r["strategy"] = strat
    if strat["strategy"] == "swing":
        swing_picks.append(r)
    elif strat["strategy"] == "long_term":
        long_picks.append(r)

swing_route = plan_relay_route(swing_picks[:max_stocks], capital=capital)
st.markdown(format_portfolio_plan(long_picks, swing_route, capital=capital))

with st.expander("詳細トレードプラン（エントリー条件/損切り判断/シナリオ）"):
    trade_plan = generate_detailed_trade_plan(all_for_plan, total_capital=capital)
    st.markdown(format_trade_plan_full(trade_plan))

# --- データテーブル ---
with st.expander("データ一覧"):
    if filtered:
        df_t = pd.DataFrame([{
            "銘柄": f"{r.get('name','')}({r['code']})",
            "現在値": r.get("current_price", 0),
            "買い": r.get("entry", 0),
            "売り": r.get("target", 0),
            "損切": r.get("stop_loss", 0),
            "+%": r.get("reward_pct", 0),
            "RR": r.get("risk_reward", 0),
            "確度": r.get("conviction_grade", "?"),
        } for r in filtered])
        st.dataframe(df_t, use_container_width=True, hide_index=True)

# --- ウォッチリスト ---
watchlist = get_watchlist_summary()
if watchlist:
    st.markdown("---")
    st.markdown("### ウォッチリスト")

    for w in watchlist:
        status_icon = {"action": "🔴", "attention": "🟡", "watching": "⚪", "holding": "💰"}.get(w["status"], "⚪")
        status_label = {"action": "買い検討", "attention": "注視中", "watching": "監視中", "holding": "保有判断"}.get(w["status"], "")
        dev_severity = w.get("deviation_severity", "ok")
        border_color = sell_c if dev_severity == "critical" else warn_c if dev_severity == "warning" else "#2A2F3E"

        # 出口情報
        exit_event = w.get("exit_event", "")
        target_date = w.get("target_date", "")
        exit_info = f"{exit_event}（{target_date[:10]}）" if exit_event and target_date else exit_event or "未設定"

        # なぜ
        why = " / ".join(w.get("why", [])[:3]) if w.get("why") else "—"

        # 乖離警告
        dev_text = ""
        devs = w.get("deviations", [])
        if devs:
            dev_text = f"<div style='color:{sell_c};font-size:0.8em;margin-top:4px'>⚠ {' / '.join(devs)}</div>"

        # アラート
        note = w.get("latest_note", "")
        alert_text = ""
        if note and "ALERT" in note:
            alert_text = f"<div style='color:{buy_c};font-size:0.85em;font-weight:600;margin-top:4px'>{note}</div>"

        st.markdown(f"""
        <div class="stock-card" style="border-left-color:{border_color};padding:14px 16px">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:6px">
                <div>
                    <span style="font-size:1.1em;font-weight:700">{status_icon} {w['name']}</span>
                    <span style="color:{sec_c};margin-left:4px">{w['code']}</span>
                    <span class="badge" style="background:#2A2F3E;color:{sec_c};margin-left:6px">{status_label}</span>
                </div>
                <div style="display:flex;gap:14px;font-size:0.85em">
                    <div style="text-align:center">
                        <div style="color:{sec_c};font-size:0.7em">現在値</div>
                        <div>¥{w['latest_price']:,.0f}</div>
                    </div>
                    <div style="text-align:center">
                        <div style="color:{buy_c};font-size:0.7em">売り目標</div>
                        <div style="color:{buy_c}">¥{w['target']:,}</div>
                    </div>
                    <div style="text-align:center">
                        <div style="color:{sell_c};font-size:0.7em">損切り</div>
                        <div style="color:{sell_c}">¥{w['stop_loss']:,}</div>
                    </div>
                </div>
            </div>
            <div style="margin-top:8px;font-size:0.85em">
                <span style="color:{warn_c}">なぜ: {why}</span>
                <span style="color:{sec_c};margin-left:12px">出口: {exit_info}</span>
            </div>
            {alert_text}
            {dev_text}
        </div>
        """, unsafe_allow_html=True)
