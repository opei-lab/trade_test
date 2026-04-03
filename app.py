"""Stock Screener"""

import streamlit as st

from src.data.database import init_db
from src.scheduler.background import start_background_job

init_db()
start_background_job()

# ページ構成を明示的に制御（自動ナビを上書き）
pages = st.navigation([
    st.Page("views/01_screener.py", title="おすすめ銘柄", icon="🔍", default=True),
    st.Page("views/02_analysis.py", title="個別分析", icon="📈"),
    st.Page("views/03_watchlist.py", title="ウォッチリスト", icon="👁"),
])
pages.run()
