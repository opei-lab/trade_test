"""テーマ特需モニター画面

テーマ（AI電力、核融合、バイオ等）の勢いを監視し、
初動銘柄と出遅れ銘柄を表示する。
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from src.analysis.theme import load_themes, detect_theme_momentum, scan_all_themes
from src.ui.components import COLORS, render_header

st.set_page_config(page_title="テーマモニター", page_icon="🔥", layout="wide")
render_header()
st.title("テーマ特需モニター")

with st.sidebar:
    st.markdown("### テーマ選択")
    themes = load_themes()
    theme_options = {v.get("name", k): k for k, v in themes.items()}

    mode = st.radio("モード", ["全テーマスキャン", "個別テーマ分析"])

    if mode == "個別テーマ分析":
        selected_name = st.selectbox("テーマ", list(theme_options.keys()))
        selected_key = theme_options[selected_name]

    period = st.selectbox("分析期間", [30, 60, 90, 180], index=2, format_func=lambda x: f"{x}日")
    run = st.button("分析開始", type="primary", use_container_width=True)

if not run:
    st.markdown("""
    <div style="background:#1A1F2E;padding:40px;border-radius:16px;text-align:center;margin-top:40px">
        <div style="font-size:3em;margin-bottom:16px">🔥</div>
        <div style="font-size:1.3em;font-weight:600;margin-bottom:8px">テーマ特需モニター</div>
        <div style="color:#90A4AE;line-height:1.8">
            AI電力・核融合・光半導体等のテーマを監視<br>
            <span style="color:#00D4AA">初動銘柄</span>と<span style="color:#FFA726">出遅れ銘柄</span>を自動検出
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

if mode == "全テーマスキャン":
    with st.spinner("全テーマをスキャン中..."):
        results = scan_all_themes(period_days=period)

    if not results:
        st.warning("テーマデータを取得できませんでした")
        st.stop()

    st.markdown("## テーマ強度ランキング")

    for r in results:
        strength = r["strength"]
        color = COLORS["buy"] if strength > 10 else COLORS["caution"] if strength > 0 else COLORS["sell"]

        st.markdown(f"""
        <div style="background:#1A1F2E;padding:16px;border-radius:10px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">
            <div>
                <span style="font-size:1.1em;font-weight:600">{r['theme_name']}</span>
                <span style="color:{COLORS['text_secondary']};margin-left:8px;font-size:0.85em">{r['total_stocks']}銘柄</span>
            </div>
            <div style="text-align:right">
                <div style="color:{color};font-size:1.3em;font-weight:700">{strength:+.1f}%</div>
                <div style="color:{COLORS['text_secondary']};font-size:0.75em">
                    初動{r['early_movers_count']}銘柄 / 出遅れ{r['laggards_count']}銘柄
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

else:
    with st.spinner(f"{selected_name}を分析中..."):
        momentum = detect_theme_momentum(selected_key, period_days=period)

    st.markdown(f"## {momentum['theme_name']}")

    col1, col2, col3 = st.columns(3)
    col1.metric("テーマ強度", f"{momentum['theme_strength']:+.1f}%")
    col2.metric("分析銘柄数", momentum["total_stocks"])
    col3.metric("初動 / 出遅れ", f"{len(momentum['early_movers'])} / {len(momentum['laggards'])}")

    # 初動銘柄
    if momentum["early_movers"]:
        st.markdown(f"""
        <div style="background:linear-gradient(135deg, #1a3a2a, #1A1F2E);padding:12px 20px;border-radius:10px;margin:16px 0">
            <span style="color:{COLORS['buy']};font-size:1.1em;font-weight:700">
                初動銘柄（既にテーマに乗っている）
            </span>
        </div>
        """, unsafe_allow_html=True)

        df_early = pd.DataFrame(momentum["early_movers"])
        df_early = df_early[["code", "name", "period_return", "recent_return", "vol_change", "current_price"]]
        df_early.columns = ["コード", "銘柄名", f"{period}日リターン%", "20日リターン%", "出来高変化", "現在値"]
        st.dataframe(df_early, use_container_width=True, hide_index=True)

    # 出遅れ銘柄
    if momentum["laggards"]:
        st.markdown(f"""
        <div style="background:#1A1F2E;padding:12px 20px;border-radius:10px;margin:16px 0">
            <span style="color:{COLORS['caution']};font-size:1.1em;font-weight:700">
                出遅れ銘柄（テーマに乗り遅れている → 狙い目の可能性）
            </span>
        </div>
        """, unsafe_allow_html=True)

        df_lag = pd.DataFrame(momentum["laggards"])
        df_lag = df_lag[["code", "name", "period_return", "recent_return", "vol_change", "current_price"]]
        df_lag.columns = ["コード", "銘柄名", f"{period}日リターン%", "20日リターン%", "出来高変化", "現在値"]
        st.dataframe(df_lag, use_container_width=True, hide_index=True)

    if not momentum["early_movers"] and not momentum["laggards"]:
        st.info("このテーマに明確な動きは検出されていません")
