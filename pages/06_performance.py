"""自走改善ダッシュボード

システムの推奨成績を追跡し、改善が進んでいるかを可視化する。
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from src.data.database import get_session, Recommendation, ScoreWeights
from src.feedback.tracker import get_hit_rate
from src.ui.components import COLORS, render_header

st.set_page_config(page_title="成績", page_icon="📊", layout="wide")
render_header()
st.title("自走改善ダッシュボード")

session = get_session()

# --- 推奨記録の統計 ---
total_recs = session.query(Recommendation).count()
reviewed = session.query(Recommendation).filter(Recommendation.hit.isnot(None)).count()
pending = total_recs - reviewed

col1, col2, col3, col4 = st.columns(4)
col1.metric("推奨記録数", total_recs)
col2.metric("答え合わせ済み", reviewed)
col3.metric("未確定", pending)

stats = get_hit_rate()
if stats["total"] > 0:
    col4.metric("的中率", f"{stats['hit_rate']}%", f"{stats['hits']}/{stats['total']}")
else:
    col4.metric("的中率", "データ不足", f"あと{30 - total_recs}件必要")

# --- 改善ループの状態 ---
st.markdown("### 自走改善の進行状況")

steps = [
    {"name": "日次スキャン", "done": total_recs > 0, "desc": f"推奨{total_recs}件記録済み"},
    {"name": "答え合わせ", "done": reviewed > 0, "desc": f"{reviewed}件確認済み"},
    {"name": "パラメータ最適化", "done": reviewed >= 30, "desc": f"{'有効化済み' if reviewed >= 30 else f'あと{max(0, 30 - reviewed)}件で有効化'}"},
]

for step in steps:
    icon = "✅" if step["done"] else "⏳"
    color = COLORS["buy"] if step["done"] else COLORS["neutral"]
    st.markdown(f"""
    <div style="background:#1A1F2E;padding:12px 16px;border-radius:8px;margin-bottom:6px;display:flex;align-items:center;gap:12px">
        <span style="font-size:1.3em">{icon}</span>
        <div>
            <span style="font-weight:600;color:{color}">{step['name']}</span>
            <span style="color:{COLORS['text_secondary']};margin-left:8px;font-size:0.9em">{step['desc']}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

# --- 推奨履歴 ---
if total_recs > 0:
    st.markdown("### 推奨履歴")
    recs = session.query(Recommendation).order_by(Recommendation.recommended_at.desc()).limit(50).all()

    data = []
    for r in recs:
        data.append({
            "日時": r.recommended_at.strftime("%Y-%m-%d %H:%M") if r.recommended_at else "",
            "コード": r.code,
            "銘柄名": r.name or "",
            "エントリー": f"¥{r.entry_price:,.0f}" if r.entry_price else "",
            "目標": f"¥{r.target_price:,.0f}" if r.target_price else "",
            "戦略": r.strategy_type or "",
            "実績高値": f"¥{r.actual_high:,.0f}" if r.actual_high else "未確定",
            "損益%": f"{r.profit_pct:+.1f}%" if r.profit_pct is not None else "—",
            "的中": "○" if r.hit == 1 else "×" if r.hit == 0 else "—",
        })

    if data:
        st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)

# --- MLモデル ---
st.markdown("### ML予測モデル")
from src.ml.predictor import get_model_report, train_model as ml_train
ml_report = get_model_report()
if ml_report:
    m = ml_report.get("metrics", {})
    precision = m.get("test_precision", 0)
    pos_rate = ml_report.get("positive_rate", 5)

    if precision < 1:
        st.warning(f"MLモデルは勝ち予測ができていません（陽性的中率0%）。勝ちサンプルが{pos_rate}%と少なすぎるため。ルールベースの確度判定を優先してください。")
    else:
        col_ml1, col_ml2, col_ml3 = st.columns(3)
        col_ml1.metric("テスト精度", f"{m.get('test_accuracy', 0)}%")
        col_ml2.metric("勝ち的中率", f"{precision}%")
        col_ml3.metric("過学習", f"{m.get('overfit_gap', 0)}%")

    fi = ml_report.get("feature_importance", {})
    if fi:
        st.markdown("特徴量重要度:")
        for feat, imp in fi.items():
            st.progress(min(1.0, imp * 3), text=f"{feat}: {imp:.4f}")
else:
    st.info("MLモデル未学習。下の「過去データで重み最適化」を実行するとモデルも学習されます。")

st.markdown("---")

# --- 勝ちパターン探索 ---
st.markdown("### 勝ちパターン探索")
text_sec2 = COLORS["text_secondary"]
st.markdown(f"<div style='color:{text_sec2};font-size:0.9em'>複数指標の組み合わせから、勝率が高いパターンを過去データで探索します。</div>", unsafe_allow_html=True)

pat_stocks = st.number_input("探索銘柄数", min_value=20, max_value=150, value=60, key="pat_stocks")
if st.button("パターン探索を実行", use_container_width=True):
    with st.spinner("パターン探索中（数分かかります）..."):
        try:
            from src.data.stocklist import get_growth_stocks as get_gs2
            from src.strategy.pattern_discovery import discover_patterns, format_pattern_report

            gs2 = get_gs2()
            codes2 = gs2["code"].tolist()[:pat_stocks]

            progress2 = st.progress(0)
            def on_prog2(c, t, code):
                progress2.progress((c + 1) / t)
            pat_result = discover_patterns(codes2, progress_callback=on_prog2)
            progress2.empty()

            st.markdown(format_pattern_report(pat_result))
        except Exception as e:
            st.error(f"エラー: {e}")

st.markdown("---")

# --- 過去データで重み最適化 ---
st.markdown("### 過去データで重み最適化")
text_sec = COLORS["text_secondary"]
st.markdown(f"<div style='color:{text_sec};font-size:0.9em'>過去2年分の株価データで「どの条件が実際に効いたか」を検証し、重みを最適化します。推奨記録が溜まるのを待たずに今すぐ改善できます。</div>", unsafe_allow_html=True)

optimize_col1, optimize_col2 = st.columns(2)
with optimize_col1:
    opt_stocks = st.number_input("検証銘柄数", min_value=10, max_value=200, value=50, help="多いほど精度が上がるが時間がかかる")
with optimize_col2:
    opt_target = st.selectbox("最適化対象", ["is_clean_win", "is_quick_win", "path_quality"],
                              format_func=lambda x: {
                                  "is_clean_win": "安全な勝ち（+30%, DD10%以下, 20日以内）",
                                  "is_quick_win": "即効勝ち（+15%, DD5%以下, 10日以内）",
                                  "path_quality": "パスの質（総合評価）",
                              }[x])

if st.button("過去データで最適化を実行", type="primary", use_container_width=True):
    with st.spinner("過去データを分析中（数分かかります）..."):
        try:
            from src.data.stocklist import get_growth_stocks
            from src.feedback.historical_optimizer import (
                run_historical_backtest, optimize_weights, apply_optimized_weights, format_optimization_report
            )

            stocks = get_growth_stocks()
            codes = stocks["code"].tolist()[:opt_stocks]

            progress = st.progress(0)
            status = st.empty()
            def on_progress(current, total, code):
                progress.progress((current + 1) / total)
                status.text(f"{code} を検証中... ({current + 1}/{total})")

            bt_df = run_historical_backtest(codes, period_days=730, progress_callback=on_progress)
            progress.empty()
            status.empty()

            if bt_df.empty:
                st.warning("データ不足で最適化できませんでした")
            else:
                st.info(f"{len(bt_df)}サンプルで検証完了")

                # パス分析の概要
                col_a, col_b, col_c, col_d = st.columns(4)
                clean = bt_df["is_clean_win"].sum() if "is_clean_win" in bt_df.columns else 0
                quick = bt_df["is_quick_win"].sum() if "is_quick_win" in bt_df.columns else 0
                painful = bt_df["is_painful_win"].sum() if "is_painful_win" in bt_df.columns else 0
                loss = bt_df["is_loss"].sum() if "is_loss" in bt_df.columns else 0
                col_a.metric("安全な勝ち", f"{clean}件 ({clean/len(bt_df)*100:.0f}%)")
                col_b.metric("即効勝ち", f"{quick}件 ({quick/len(bt_df)*100:.0f}%)")
                col_c.metric("苦しい勝ち", f"{painful}件", help="利益は出たが途中DD15%超")
                col_d.metric("負け", f"{loss}件 ({loss/len(bt_df)*100:.0f}%)")

                # 重み最適化
                optimized = optimize_weights(bt_df, target=opt_target)
                apply_optimized_weights(optimized)

                # パターン発見
                from src.feedback.historical_optimizer import find_quick_patterns
                patterns = find_quick_patterns(bt_df)

                st.markdown(format_optimization_report(optimized, patterns))
                st.success("重みを更新しました。次のスクリーニングから反映されます。")

        except Exception as e:
            st.error(f"エラー: {e}")

st.markdown("---")

# --- 手動実行ボタン ---
st.markdown("### 手動実行")
col1, col2 = st.columns(2)
with col1:
    if st.button("今すぐ日次バッチを実行", use_container_width=True):
        with st.spinner("実行中..."):
            try:
                from src.scheduler.jobs import run_daily
                run_daily()
                st.success("完了")
                st.rerun()
            except Exception as e:
                st.error(f"エラー: {e}")

with col2:
    if st.button("答え合わせを実行", use_container_width=True):
        with st.spinner("確認中..."):
            try:
                from src.feedback.tracker import check_outcomes
                check_outcomes(days_after=30)
                st.success("完了")
                st.rerun()
            except Exception as e:
                st.error(f"エラー: {e}")

st.markdown(f"""
<div style="background:#1A1F2E;padding:16px;border-radius:10px;margin-top:20px">
    <div style="color:{COLORS['text_secondary']};font-size:0.9em;line-height:1.8">
        <b>自走改善の仕組み:</b><br>
        アプリ起動中、バックグラウンドで自動実行されます（1日1回）<br>
        1. 自動スキャン → 上位銘柄をDBに記録<br>
        2. 30日後に自動で答え合わせ → 実際に上がったか確認<br>
        3. 30件溜まったら → どの条件が的中率高いか分析 → 重みを自動調整<br>
        4. 調整された重みで次のスクリーニング → 確度が上がる<br>
        <br>
        <b>start.bat でアプリを起動するだけで自走します。他の設定は不要です。</b>
    </div>
</div>
""", unsafe_allow_html=True)

session.close()
