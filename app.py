"""Stock Screener"""

import streamlit as st
from src.data.database import init_db
from src.ui.components import COLORS, inject_global_css, render_header
from src.scheduler.background import start_background_job

st.set_page_config(page_title="Stock Screener", page_icon="📊", layout="wide")

init_db()
start_background_job()
render_header()

sec = COLORS["text_secondary"]

st.markdown(f"""
<div style="text-align:center;padding:40px 0 20px">
    <h1>Stock Screener</h1>
    <p style="color:{sec}">確度が高い銘柄を厳選し、買い時・売り時・理由を提示</p>
</div>
""", unsafe_allow_html=True)

st.page_link("pages/01_screener.py", label="おすすめ銘柄を探す", icon="🔍", use_container_width=True)

st.markdown(f"""
<div style="color:{sec};font-size:0.9em;text-align:center;margin-top:20px;line-height:2">
    「おすすめ銘柄を探す」を押すだけ。あとはシステムが全自動で分析します。<br>
    個別分析・テーマ・シミュレーター・ポートフォリオはサイドバーから。
</div>
""", unsafe_allow_html=True)
