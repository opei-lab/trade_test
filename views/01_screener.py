"""おすすめ銘柄画面

3つのセクション:
1. ウォッチ中の銘柄（アクティブ。売買プラン付き）
2. プランから外れた銘柄（了解で消す）
3. 新しい候補（スクリーニング結果。ウォッチに追加）
"""

import streamlit as st
import pandas as pd
from datetime import date

from src.data.stocklist import get_growth_stocks
from src.data.price import fetch_price
from src.strategy.screener import screen_stocks
from src.strategy.deep_analysis import run_deep_analysis
from src.strategy.report import generate_report
from src.strategy.portfolio_router import classify_strategy, plan_relay_route, format_portfolio_plan
from src.strategy.cache import save_screen_results, load_screen_cache, get_cache_info
from src.data.watchlist import get_watchlist_summary, update_from_screening
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
    st.markdown("""
    **方針:** グロース市場 · 低価格帯 · 高ボラ厳選
    下値限定 × 上値大の非対称構造をメインに
    IR/需給/テーマ/大口のシグナルを全自動検知
    """)
    st.markdown("---")
    run = st.button("スキャン実行", type="primary", use_container_width=True)

# スキャン状態
scan_status = get_scan_status()

# session_state（キャッシュからの復元付き）
if "scan_results" not in st.session_state:
    st.session_state.scan_results = None

# 再起動時: session_stateが空ならキャッシュから復元
if st.session_state.scan_results is None:
    _cache_info = get_cache_info()
    if _cache_info:
        _cached = load_screen_cache("グロース市場")
        if _cached:
            st.session_state.scan_results = _cached
            st.sidebar.caption(f"前回スキャン: {_cache_info.get('timestamp', '')[:16]} ({_cache_info.get('count', 0)}件)")

# ============================
# スキャン実行（他より先に処理。完了後にrerunで再描画）
# ============================
if run:
    st.session_state.scan_results = None
    st.session_state.cand_page = 0
    st.markdown("### スキャン中...")

    from src.strategy.cache import save_screen_results as _save
    from src.data.stocklist import get_growth_stocks
    from src.strategy.screener import screen_stocks
    from src.strategy.deep_analysis import run_deep_analysis
    from src.data.watchlist import update_from_screening as _update_wl, refresh_watchlist as _refresh_wl

    # テーマ自動更新（みんかぶランキングから最新テーマを取得）
    try:
        from src.data.theme_scraper import update_themes_yaml
        _theme_result = update_themes_yaml()
        st.toast(f"テーマ更新: {_theme_result['auto_count']}件自動取得", icon="🔄")
    except Exception:
        pass

    _stocks = get_growth_stocks()

    _codes_list = _stocks["code"].tolist()
    _name_map = dict(zip(_stocks["code"].astype(str), _stocks["name"]))

    _progress = st.progress(0, text="スキャン中...")
    _candidates = screen_stocks(
        _codes_list, min_score=0,
        progress_callback=lambda c, t, code: _progress.progress((c+1)/t, text=f"Stage 1-4: {code} ({c+1}/{t})"),
    )
    for _r in _candidates:
        _jpx = _name_map.get(_r["code"], "")
        if _jpx and _jpx.strip():
            _r["name"] = _jpx

    if _candidates:
        _progress.progress(1.0, text=f"Stage 5: {len(_candidates)}銘柄を情報分析中...")
        _results = run_deep_analysis(
            _candidates,
            progress_callback=lambda c, t, code: _progress.progress((c+1)/t, text=f"Stage 5: {code} ({c+1}/{t})"),
        )
        # IR急増パターンからの新興テーマ検出
        try:
            from src.data.theme_scraper import detect_emerging_themes
            _all_ir = []
            for _r in _results:
                for _ir in _r.get("ir_summary", []):
                    _all_ir.append(_ir)
            _emerging = detect_emerging_themes(_all_ir, min_count=3)
            if _emerging:
                st.session_state.emerging_themes = _emerging
        except Exception:
            pass

        _save("グロース市場", _results)
        _update_wl(_results)

        # ウォッチ中の全銘柄を独立更新（スキャン対象外も追跡。除外も自動）
        try:
            _wl_result = _refresh_wl()
            if _wl_result["updated"] > 0:
                st.toast(f"ウォッチ {_wl_result['updated']}件更新", icon="👁")
            for _rm in _wl_result.get("auto_removed", []):
                st.toast(f"自動除外: {_rm}", icon="⚠")
        except Exception:
            pass

        st.session_state.scan_results = _results
    else:
        st.session_state.scan_results = []

    st.rerun()  # 再描画

# ============================
# 新興テーマ検出（IR急増パターン）
# ============================
if st.session_state.get("emerging_themes"):
    with st.expander(f"新興テーマ候補（IR急増検出: {len(st.session_state.emerging_themes)}件）", expanded=False):
        for _et in st.session_state.emerging_themes[:10]:
            st.markdown(f"**{_et['keyword']}** — {_et['count']}社が言及")
            for _t in _et["titles"][:3]:
                st.caption(f"  {_t}")

# ============================
# セクション1: ウォッチ中の銘柄（サマリーのみ。詳細は専用ページ）
# ============================
watchlist = get_watchlist_summary()
active = [w for w in watchlist if w["status"] in ("action", "attention", "watching")]
deviated = [w for w in watchlist if w.get("deviation_severity") in ("critical", "warning")]

if active or deviated:
    st.markdown("## ウォッチ銘柄")
    wc1, wc2, wc3 = st.columns(3)
    wc1.metric("ウォッチ中", f"{len(active)}銘柄")
    wc2.metric("買い検討", f"{len([w for w in active if w['status'] == 'action'])}銘柄")
    wc3.metric("要注意", f"{len(deviated)}銘柄")

    # アラートがある銘柄だけ表示
    for w in active:
        note = w.get("latest_note", "")
        if "ALERT" in note:
            st.warning(f"**{w['name']}** {w['code']} — {note}")
    for w in deviated:
        devs = " / ".join(w.get("deviations", []))
        st.error(f"**{w['name']}** {w['code']} — {devs}")

    st.caption("詳細は左メニューの「ウォッチリスト」ページへ")


# ============================
# セクション3: 新しい候補
# ============================

# session_stateにスキャン結果を保持
cached = st.session_state.scan_results
if cached is None:
    cached = load_screen_cache("グロース市場")
cache_info = get_cache_info()

    # (スキャン処理はページ先頭のif runブロックで実行済み)

if cached:
    # ウォッチ済みの銘柄を除外
    watch_codes = set(w["code"] for w in watchlist)
    new_candidates = [r for r in cached if r.get("code") not in watch_codes]

    if new_candidates:
        PER_PAGE = 10

        # ページネーション状態
        if "cand_page" not in st.session_state:
            st.session_state.cand_page = 0
        total_pages = max(1, -(-len(new_candidates) // PER_PAGE))  # ceil division
        page = st.session_state.cand_page = min(st.session_state.cand_page, total_pages - 1)
        page_items = new_candidates[page * PER_PAGE : (page + 1) * PER_PAGE]

        st.markdown("---")
        ts = cache_info.get("timestamp", "")[:16].replace("T", " ") if cache_info else ""
        start_n = page * PER_PAGE + 1
        end_n = min((page + 1) * PER_PAGE, len(new_candidates))
        st.markdown(f"## 新しい候補（{start_n}-{end_n} / {len(new_candidates)}件）")
        if ts:
            st.caption(f"{ts} のスキャン結果 · 確度×利益幅順")

        for r in page_items:
            conv = r.get("conviction", {}) if isinstance(r.get("conviction"), dict) else {}
            grade = conv.get("grade", "?")
            is_best = r.get("is_best_pattern", False)

            current = r.get("current_price", 0)
            entry = r.get("entry", 0)
            target = r.get("target", 0)
            stop = r.get("stop_loss", 0)
            reward = r.get("reward_pct", 0)

            passed = conv.get("passed", [])
            why = " / ".join([p.get("name", "") for p in passed if p.get("weight", 0) >= 4][:3]) or "—"

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

            algo = r.get("algo_phase", "unknown")
            algo_label = {"pre_algo": "🔵静か", "algo_entering": "🟡動き始め", "algo_active": "🔴過熱", "algo_exiting": "⚫撤退中"}.get(algo, "")

            badge = " 🏆勝ちパターン" if is_best else ""
            st.markdown(f"**{r.get('name', r['code'])}** {r['code']} — {rec_color} **{rec_label} {conv_score}%** {algo_label}{badge}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("現在値", f"¥{current:,.0f}")
            c2.metric("買い", f"¥{entry:,}")
            c3.metric("売り", f"¥{target:,}", f"+{reward:.0f}%")
            c4.metric("損切", f"¥{stop:,}")

            st.caption(f"根拠: {why}")

            scenario_text = r.get("scenario_text", "")
            if scenario_text:
                st.caption(scenario_text[:120])

            with st.expander(f"詳細レポート"):
                struct_report = r.get("structure_report", "")
                if struct_report:
                    st.markdown(struct_report)
                    st.markdown("---")
                st.markdown(generate_report(r))

        # ページ切り替え
        if total_pages > 1:
            pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
            with pcol1:
                if st.button("← 前", disabled=(page == 0), use_container_width=True):
                    st.session_state.cand_page -= 1
                    st.rerun()
            with pcol2:
                st.markdown(f"<div style='text-align:center;padding:8px;color:{sec_c}'>{page+1} / {total_pages}</div>", unsafe_allow_html=True)
            with pcol3:
                if st.button("次 →", disabled=(page >= total_pages - 1), use_container_width=True):
                    st.session_state.cand_page += 1
                    st.rerun()

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
    # 上位5件のみ（全件並べると非現実的）
    sorted_active = sorted_active[:5]
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
