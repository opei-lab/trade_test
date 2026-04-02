"""セクター相対強弱分析

同セクター内での相対的なパフォーマンスを評価する。
「セクター全体が弱い中で1銘柄だけ強い」= 大口が集めているサイン。
"""

import pandas as pd
import numpy as np
from src.data.price import fetch_price, fetch_prices_bulk
from src.data.stocklist import get_stocks_by_sector


def calc_relative_strength(
    code: str,
    sector_keyword: str,
    period_days: int = 60,
    max_peers: int = 20,
) -> dict:
    """同セクター内での相対的な強さを算出する。

    Returns:
        {
            "relative_rank": セクター内の順位（1=最強）,
            "total_peers": 比較対象数,
            "percentile": パーセンタイル（100=最強）,
            "sector_return": セクター平均リターン,
            "stock_return": この銘柄のリターン,
            "outperformance": セクター平均からの超過リターン,
            "is_sector_leader": セクター内で突出して強いか,
            "description": 説明,
        }
    """
    # 同セクター銘柄を取得
    peers_df = get_stocks_by_sector(sector_keyword)
    peer_codes = peers_df["code"].tolist()[:max_peers]

    if code not in peer_codes:
        peer_codes.append(code)

    # 株価を取得
    returns = {}
    for pc in peer_codes:
        try:
            df = fetch_price(pc, period_days=period_days)
            if df.empty or len(df) < 10:
                continue
            close = df["Close"]
            ret = (float(close.iloc[-1]) - float(close.iloc[0])) / float(close.iloc[0]) * 100
            returns[pc] = ret
        except Exception:
            continue

    if code not in returns or len(returns) < 3:
        return {
            "relative_rank": 0,
            "total_peers": 0,
            "percentile": 50,
            "sector_return": 0,
            "stock_return": 0,
            "outperformance": 0,
            "is_sector_leader": False,
            "description": "比較データ不足",
        }

    stock_return = returns[code]
    all_returns = sorted(returns.values(), reverse=True)
    sector_avg = np.mean(list(returns.values()))
    rank = all_returns.index(stock_return) + 1
    total = len(returns)
    percentile = round((1 - (rank - 1) / total) * 100)
    outperformance = stock_return - sector_avg

    # セクターリーダー判定
    # セクターが下がっている中で上がっている or 下げ幅が小さい = リーダー
    is_leader = (sector_avg < 0 and stock_return > sector_avg + 5) or percentile >= 80

    desc = ""
    if is_leader and sector_avg < 0:
        desc = f"セクター平均{sector_avg:+.1f}%の中で{stock_return:+.1f}%（{percentile}パーセンタイル）。下落相場で耐えており、大口が支えている可能性"
    elif is_leader:
        desc = f"セクター内{rank}位/{total}銘柄。平均+{sector_avg:.1f}%に対して+{stock_return:.1f}%（+{outperformance:.1f}%超過）"
    else:
        desc = f"セクター内{rank}位/{total}銘柄。平均との差{outperformance:+.1f}%"

    return {
        "relative_rank": rank,
        "total_peers": total,
        "percentile": percentile,
        "sector_return": round(sector_avg, 1),
        "stock_return": round(stock_return, 1),
        "outperformance": round(outperformance, 1),
        "is_sector_leader": is_leader,
        "description": desc,
    }
