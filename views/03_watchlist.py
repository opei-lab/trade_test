"""ウォッチリスト画面

ウォッチ中の銘柄を一覧表示。
ステータス管理、プラン乖離チェック、手動追加・削除。
"""

import streamlit as st
from datetime import date

from src.data.watchlist import (
    get_watchlist_summary,
    remove_from_watchlist,
    load_watchlist,
    get_trade_stats,
)
from src.data.price import fetch_price
from src.ui.components import COLORS, render_header

st.set_page_config(page_title="ウォッチリスト", page_icon="👁", layout="wide")
render_header()

with st.sidebar:
    st.markdown("""
    **ウォッチリスト:** 全自動管理
    確度S/A+スコア40以上で自動追加
    損切り割れ等criticalで自動除外
    追跡・乖離チェックも自動
    """)

buy_c = COLORS["buy"]
sell_c = COLORS["sell"]
warn_c = COLORS["caution"]
sec_c = COLORS["text_secondary"]

st.markdown("## ウォッチリスト")

# ページを開いた時に自動更新（1日1回。同日2回目以降はスキップ）
from src.data.watchlist import refresh_watchlist
_today = date.today().isoformat()
if st.session_state.get("wl_last_refresh") != _today:
    with st.spinner("ウォッチ銘柄を更新中..."):
        _wl_result = refresh_watchlist()
        st.session_state.wl_last_refresh = _today
        if _wl_result["updated"] > 0:
            st.toast(f"{_wl_result['updated']}銘柄を更新", icon="✅")
        for _rm in _wl_result.get("auto_removed", []):
            st.toast(f"自動除外: {_rm}", icon="⚠")

watchlist = get_watchlist_summary()
active = [w for w in watchlist if w["status"] in ("action", "attention", "watching")]
deviated = [w for w in watchlist if w.get("deviation_severity") in ("critical", "warning")]
holding = [w for w in watchlist if w["status"] == "holding"]

# サマリー
c1, c2, c3, c4 = st.columns(4)
c1.metric("ウォッチ中", f"{len(active)}銘柄")
c2.metric("買い検討", f"{len([w for w in active if w['status'] == 'action'])}銘柄")
c3.metric("要注意", f"{len(deviated)}銘柄")
c4.metric("保有中", f"{len(holding)}銘柄")

if not active and not deviated:
    st.info("ウォッチリストは空です。スクリーナーで銘柄をスキャンし、「ウォッチ」ボタンで追加してください。")
    st.stop()

# ============================
# プランから外れた銘柄（最優先で表示）
# ============================
if deviated:
    st.markdown("### ⚠ プランから外れた銘柄")
    for w in deviated:
        devs = w.get("deviations", [])
        dev_text = " / ".join(devs) if devs else "不明"
        _dcol1, _dcol2 = st.columns([5, 1])
        with _dcol1:
            st.error(f"**{w['name']}** {w['code']} ¥{w['latest_price']:,.0f} — {dev_text}")
        with _dcol2:
            if st.button("了解・除外", key=f"dev_rm_{w['code']}"):
                remove_from_watchlist(w["code"], reason=f"手動除外: {dev_text[:50]}")
                st.rerun()
    st.markdown("---")

# ============================
# アクティブ銘柄
# ============================
if active:
    # ステータスごとにグループ化
    for status, label, icon in [
        ("action", "買い検討", "🔴"),
        ("attention", "注視中", "🟡"),
        ("watching", "監視中", "⚪"),
    ]:
        group = [w for w in active if w["status"] == status]
        if not group:
            continue

        st.markdown(f"### {icon} {label}（{len(group)}銘柄）")

        for w in group:
            why = " / ".join(w.get("why", [])[:3]) if w.get("why") else "—"
            exit_event = w.get("exit_event", "")
            target_date = w.get("target_date", "")
            exit_info = exit_event if exit_event else "未設定"
            if target_date:
                exit_info += f"（{target_date[:10]}）"

            # 損益計算
            added = w.get("added_price", 0)
            latest = w.get("latest_price", 0)
            pnl = ((latest - added) / added * 100) if added > 0 else 0
            pnl_color = buy_c if pnl >= 0 else sell_c

            _hcol1, _hcol2 = st.columns([5, 1])
            with _hcol1:
                st.markdown(f"**{w['name']}** {w['code']}　確度: **{w['grade']}** ({w['score']:.0f}%)")
            with _hcol2:
                if st.button("除外", key=f"rm_{w['code']}", type="secondary"):
                    remove_from_watchlist(w["code"], reason="手動除外")
                    st.rerun()
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("現在値", f"¥{latest:,.0f}", f"{pnl:+.1f}%")
            mc2.metric("追加時", f"¥{added:,.0f}")
            mc3.metric("売り目標", f"¥{w['target']:,}")
            mc4.metric("損切り", f"¥{w['stop_loss']:,}")

            # なぜウォッチに入れたか（一目でわかるように）
            why_detail = w.get("why_detail", "")
            if why_detail:
                st.markdown(f"**理由:** {why_detail}")
            scenario = w.get("scenario_text", "")
            if scenario:
                st.caption(f"シナリオ: {scenario[:150]}")
            st.caption(f"根拠: {why} | 出口: {exit_info}")

            # 直近のアラート
            note = w.get("latest_note", "")
            if note and "ALERT" in note:
                st.warning(note)

            # チャート（展開）
            with st.expander(f"📈 チャート"):
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
                        if added > 0:
                            _fig.add_hline(y=added, line_dash="dash", line_color=sec_c, annotation_text="追加時")
                        _fig.update_layout(
                            height=300, xaxis_rangeslider_visible=False,
                            template="plotly_dark", paper_bgcolor="#0E1117", plot_bgcolor="#0E1117",
                            margin=dict(l=40, r=20, t=10, b=20),
                        )
                        st.plotly_chart(_fig, use_container_width=True)
                except Exception:
                    pass

            # 凍結ストーリー + MFE/MAE
            wl_data = load_watchlist()
            stock_data = wl_data.get("stocks", {}).get(w["code"], {})
            frozen = stock_data.get("frozen_story", {})
            if frozen.get("scenario_text"):
                with st.expander("📋 凍結ストーリー（追加時のプラン）"):
                    st.markdown(frozen["scenario_text"])
                    if frozen.get("triggers"):
                        st.caption(f"トリガー: {' / '.join(frozen['triggers'][:3])}")
                    if frozen.get("themes"):
                        st.caption(f"テーマ: {' / '.join(frozen['themes'][:3])}")

            # MFE/MAE表示
            mfe = stock_data.get("mfe_price", 0)
            mae = stock_data.get("mae_price", 0)
            added = w.get("added_price", 0)
            if mfe > 0 and mae > 0 and added > 0:
                mfe_pct = (mfe - added) / added * 100
                mae_pct = (mae - added) / added * 100
                st.caption(f"期中最高: ¥{mfe:,.0f}（{mfe_pct:+.1f}%） / 期中最安: ¥{mae:,.0f}（{mae_pct:+.1f}%）")

            # 履歴
            history = stock_data.get("history", [])
            if len(history) >= 2:
                with st.expander(f"📊 追跡履歴（{len(history)}回）"):
                    for h in reversed(history[-10:]):
                        note_str = f" — {h['note']}" if h.get("note") else ""
                        st.caption(f"{h['date']} ¥{h.get('price', 0):,.0f} {h['grade']}({h.get('score', 0):.0f}%){note_str}")

# ============================
# 実績サマリー
# ============================
stats = get_trade_stats()
if stats["count"] > 0:
    st.markdown("---")
    st.markdown("## 実績")
    st.caption("勝ち = 手数料込み+5%以上の実質黒字。同値撤退・微益は引き分け")

    sc1, sc2, sc3, sc4, sc5 = st.columns(5)
    sc1.metric("トレード", stats["count"])
    sc2.metric("勝率", f"{stats['win_rate']:.0f}%")
    sc3.metric("平均利益", f"{stats['avg_win_pct']:+.1f}%")
    sc4.metric("平均損失", f"{stats['avg_loss_pct']:+.1f}%")
    sc5.metric("総損益", f"{stats['total_pnl']:+.1f}%")

    ev = stats["expected_value"]
    if ev > 5:
        st.success(f"期待値: **{ev:+.1f}%/トレード** — 稼げている")
    elif ev > 0:
        st.warning(f"期待値: **{ev:+.1f}%/トレード** — 手数料負けの可能性あり")
    else:
        st.error(f"期待値: **{ev:+.1f}%/トレード** — ルール見直し必要")

    if stats.get("draws", 0) > 0:
        st.caption(f"引き分け（±5%以内）: {stats['draws']}件")

    if stats["near_misses"] > 0:
        st.warning(f"+20%以上到達したが利確できなかった: {stats['near_misses']}件 → 売り目標の見直し余地あり")

    with st.expander(f"全トレード詳細（{stats['count']}件）"):
        for t in stats["trades"]:
            reason_label = {"target_hit": "利確", "stop_hit": "損切", "expired": "期限切れ"}.get(t["exit_reason"], "不明")
            pnl = t["pnl_pct"]
            if pnl >= 5:
                result_icon = "🟢"
            elif pnl <= -5:
                result_icon = "🔴"
            else:
                result_icon = "⚪"
            mfe_info = f"期中最高{t.get('mfe_pct', 0):+.1f}%" if t.get("mfe_pct") else ""
            st.markdown(f"{result_icon} **{t['name']}** {t['code']} — {reason_label} {pnl:+.1f}% | {t.get('exit_date', '')} | {mfe_info}")
