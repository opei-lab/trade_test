"""共通UIコンポーネント

画面間で統一されたスタイルとコンポーネントを提供する。
"""

import streamlit as st


def inject_global_css():
    """グローバルCSSを注入する。"""
    st.markdown("""
    <style>
        /* ベースレイアウト */
        .block-container {
            padding-top: 1.5rem !important;
            padding-left: 1.5rem !important;
            padding-right: 1.5rem !important;
            max-width: 1200px;
        }
        [data-testid="stSidebar"] { min-width: 240px; max-width: 300px; }
        [data-testid="stToolbar"] { z-index: 999; }

        /* タイポグラフィ */
        h1 { font-size: 1.6em !important; font-weight: 700 !important; margin-bottom: 0.3em !important; }
        h2 { font-size: 1.25em !important; font-weight: 600 !important; }
        h3 { font-size: 1.1em !important; font-weight: 600 !important; }
        p, div, span { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }

        /* ボタン */
        .stButton > button {
            border-radius: 10px;
            font-weight: 600;
            font-size: 1em;
            padding: 0.6em 1.2em;
            transition: all 0.15s ease;
        }
        .stButton > button:hover { transform: translateY(-1px); }
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, #00D4AA, #00B894) !important;
            border: none !important;
        }

        /* expander */
        .streamlit-expanderHeader {
            font-size: 0.9em;
            font-weight: 500;
            color: #90A4AE;
        }

        /* リンク */
        a { transition: opacity 0.15s; }
        a:hover { opacity: 0.75; }

        /* カスタムカード */
        .stock-card {
            background: #1A1F2E;
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 16px;
            border-left: 4px solid #78909C;
            transition: border-color 0.2s;
        }
        .stock-card:hover { border-left-color: #00D4AA; }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 14px;
        }

        .card-prices {
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
            margin-bottom: 14px;
        }

        .card-price-item {
            text-align: center;
            min-width: 70px;
        }
        .card-price-label {
            font-size: 0.7em;
            margin-bottom: 2px;
        }
        .card-price-value {
            font-size: 1.2em;
            font-weight: 700;
        }

        .card-reason {
            background: #151923;
            padding: 12px 14px;
            border-radius: 8px;
        }

        .badge {
            padding: 3px 12px;
            border-radius: 10px;
            font-size: 0.8em;
            font-weight: 600;
            color: #fff;
            display: inline-block;
        }

        /* レスポンシブ */
        @media (max-width: 768px) {
            .block-container {
                padding-left: 0.8rem !important;
                padding-right: 0.8rem !important;
            }
            .card-prices { gap: 12px; }
            .card-price-item { min-width: 55px; }
            .card-price-value { font-size: 1em; }
            h1 { font-size: 1.3em !important; }
        }

        @media (max-width: 480px) {
            .card-prices {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 8px;
            }
        }

        /* プログレスバーの色 */
        .stProgress > div > div > div { background-color: #00D4AA !important; }

        /* キャプション */
        .stCaption { color: #78909C !important; }
    </style>
    """, unsafe_allow_html=True)


def render_header():
    """全ページ共通のヘッダーをサイドバー内に表示する。"""
    inject_global_css()
    with st.sidebar:
        st.markdown("""
        <div style="padding:4px 0 8px;border-bottom:1px solid #2A2F3E;margin-bottom:8px">
            <span style="color:#E0E0E0;font-weight:700;font-size:1.1em">📊 Stock Screener</span>
        </div>
        """, unsafe_allow_html=True)

        # ウォッチリストの注視銘柄をサイドバーに表示
        try:
            from src.data.watchlist import get_watchlist_summary
            wl = get_watchlist_summary()
            attention = [w for w in wl if w["status"] in ("attention", "action")]
            if attention:
                st.markdown(f"<div style='border-top:1px solid #2A2F3E;margin:8px 0;padding-top:6px;font-size:0.8em;color:#90A4AE'>注視銘柄</div>", unsafe_allow_html=True)
                for w in attention[:5]:
                    icon = "🔴" if w["status"] == "action" else "🟡"
                    st.markdown(f"<div style='font-size:0.85em;padding:2px 0'>{icon} {w['name']} <span style='color:#90A4AE'>{w['code']}</span></div>", unsafe_allow_html=True)
        except Exception:
            pass


# カラーパレット
COLORS = {
    "buy": "#00D4AA",       # 買いシグナル（緑）
    "sell": "#FF4B4B",      # 売りシグナル（赤）
    "caution": "#FFA726",   # 警戒（オレンジ）
    "neutral": "#78909C",   # 中立（グレー）
    "info": "#42A5F5",      # 情報（青）
    "bg_card": "#1A1F2E",   # カード背景
    "text_primary": "#E0E0E0",
    "text_secondary": "#90A4AE",
}

PHASE_CONFIG = {
    "A": {"color": COLORS["info"], "icon": "🔵", "label": "仕込み", "action": "監視"},
    "B": {"color": COLORS["caution"], "icon": "🟠", "label": "試し玉", "action": "待機"},
    "C": {"color": COLORS["buy"], "icon": "🟢", "label": "振るい落とし", "action": "エントリー準備"},
    "D": {"color": COLORS["sell"], "icon": "🔴", "label": "本上昇", "action": "利確検討"},
    "E": {"color": COLORS["neutral"], "icon": "⚫", "label": "売り抜け後", "action": "手出し禁止"},
    "NONE": {"color": COLORS["neutral"], "icon": "⚪", "label": "非該当", "action": "—"},
}


def render_signal_badge(label: str, signal_type: str = "neutral") -> str:
    """シグナルバッジのHTMLを返す。"""
    color = COLORS.get(signal_type, COLORS["neutral"])
    return f'<span style="background:{color};color:#fff;padding:2px 10px;border-radius:12px;font-size:0.85em;font-weight:600">{label}</span>'


def render_phase_card(phase_result: dict):
    """仕手フェーズを見やすいカードで表示する。"""
    phase = phase_result.get("phase", "NONE")
    config = PHASE_CONFIG.get(phase, PHASE_CONFIG["NONE"])
    confidence = phase_result.get("confidence", 0)
    desc = phase_result.get("description", "")

    st.markdown(f"""
    <div style="background:{COLORS['bg_card']};padding:20px;border-radius:12px;border-left:4px solid {config['color']}">
        <div style="display:flex;justify-content:space-between;align-items:center">
            <div>
                <span style="font-size:1.5em">{config['icon']}</span>
                <span style="font-size:1.3em;font-weight:700;margin-left:8px">Phase {phase}: {config['label']}</span>
            </div>
            <div style="text-align:right">
                <div style="font-size:0.85em;color:{COLORS['text_secondary']}">確信度</div>
                <div style="font-size:1.5em;font-weight:700;color:{config['color']}">{confidence}%</div>
            </div>
        </div>
        <div style="margin-top:10px;color:{COLORS['text_secondary']}">{desc}</div>
        <div style="margin-top:8px">
            <span style="background:{config['color']};color:#fff;padding:4px 12px;border-radius:8px;font-size:0.85em;font-weight:600">
                推奨アクション: {config['action']}
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_score_card(title: str, score: float, max_score: float = 100, description: str = ""):
    """スコアカードを表示する。"""
    pct = score / max_score if max_score > 0 else 0
    if pct >= 0.7:
        color = COLORS["buy"]
    elif pct >= 0.4:
        color = COLORS["caution"]
    else:
        color = COLORS["neutral"]

    st.markdown(f"""
    <div style="background:{COLORS['bg_card']};padding:16px;border-radius:10px;text-align:center">
        <div style="font-size:0.85em;color:{COLORS['text_secondary']}">{title}</div>
        <div style="font-size:2em;font-weight:700;color:{color}">{score:.1f}</div>
        <div style="background:#2A2F3E;border-radius:4px;height:6px;margin-top:8px">
            <div style="background:{color};width:{pct*100:.0f}%;height:100%;border-radius:4px"></div>
        </div>
        <div style="font-size:0.75em;color:{COLORS['text_secondary']};margin-top:4px">{description}</div>
    </div>
    """, unsafe_allow_html=True)


def render_price_target(
    current: float, entry: float, target: float, stop_loss: float
):
    """エントリー/目標/損切りを視覚的に表示する。"""
    risk = current - stop_loss
    reward = target - current
    rr = reward / risk if risk > 0 else 0

    rr_color = COLORS["buy"] if rr >= 2 else COLORS["caution"] if rr >= 1 else COLORS["sell"]

    st.markdown(f"""
    <div style="background:{COLORS['bg_card']};padding:20px;border-radius:12px">
        <div style="display:flex;justify-content:space-between;margin-bottom:16px">
            <div style="text-align:center;flex:1">
                <div style="color:{COLORS['sell']};font-size:0.85em">損切り</div>
                <div style="font-size:1.3em;font-weight:700">¥{stop_loss:,.0f}</div>
                <div style="color:{COLORS['sell']};font-size:0.85em">-{(current-stop_loss)/current*100:.1f}%</div>
            </div>
            <div style="text-align:center;flex:1;border-left:1px solid #333;border-right:1px solid #333">
                <div style="color:{COLORS['info']};font-size:0.85em">エントリー</div>
                <div style="font-size:1.3em;font-weight:700">¥{entry:,.0f}</div>
                <div style="color:{COLORS['text_secondary']};font-size:0.85em">現在値: ¥{current:,.0f}</div>
            </div>
            <div style="text-align:center;flex:1">
                <div style="color:{COLORS['buy']};font-size:0.85em">目標</div>
                <div style="font-size:1.3em;font-weight:700">¥{target:,.0f}</div>
                <div style="color:{COLORS['buy']};font-size:0.85em">+{(target-current)/current*100:.1f}%</div>
            </div>
        </div>
        <div style="text-align:center;padding-top:12px;border-top:1px solid #333">
            <span style="color:{COLORS['text_secondary']}">リスクリワード比: </span>
            <span style="font-size:1.3em;font-weight:700;color:{rr_color}">{rr:.2f}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
