"""株価・出来高データ取得モジュール

yfinanceを使用して日本株の日次OHLCV（始値・高値・安値・終値・出来高）を取得する。
東証の銘柄コードは末尾に".T"を付与してyfinanceに渡す。
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta


def to_yf_ticker(code: str | int) -> str:
    """銘柄コードをyfinance形式に変換する。

    Args:
        code: 銘柄コード（例: "4572", 4572, "4572.T"）

    Returns:
        yfinance形式のティッカー（例: "4572.T"）
    """
    code_str = str(code).strip()
    if not code_str.endswith(".T"):
        code_str += ".T"
    return code_str


def fetch_price(
    code: str | int,
    period_days: int = 365,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """指定銘柄の日次OHLCVデータを取得する。

    Args:
        code: 銘柄コード
        period_days: 取得期間（日数）。start/endが指定された場合は無視
        start: 開始日（YYYY-MM-DD）
        end: 終了日（YYYY-MM-DD）

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
        indexはDatetimeIndex
    """
    ticker = to_yf_ticker(code)

    if start and end:
        df = yf.download(ticker, start=start, end=end, progress=False)
    else:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=period_days)
        df = yf.download(
            ticker,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            progress=False,
        )

    if df.empty:
        return pd.DataFrame()

    # yfinance v0.2.40+ returns MultiIndex (Price, Ticker); flatten to Price level
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # 必要な列のみ保持
    cols = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in cols if c in df.columns]].copy()

    # NaN行を除去（取引日だが値が未確定の行など）
    df = df.dropna(subset=["Close"])

    # データ品質チェック（分割未調整値の混入防止）
    if len(df) >= 2:
        pct = df["Close"].pct_change().abs()
        # 1日で80%以上変動した行は分割/併合の調整ミスの可能性
        suspicious = pct > 0.8
        if suspicious.any():
            # 直近の値を信頼し、異常変動以前のデータを除去
            first_bad = suspicious.idxmax()
            df = df.loc[first_bad:]
            df = df.iloc[1:]  # 異常変動の行自体も除去

    return df


def fetch_prices_bulk(
    codes: list[str | int],
    period_days: int = 365,
) -> dict[str, pd.DataFrame]:
    """複数銘柄の株価を一括取得する。

    Args:
        codes: 銘柄コードのリスト
        period_days: 取得期間（日数）

    Returns:
        {銘柄コード: DataFrame} の辞書
    """
    results = {}
    tickers = [to_yf_ticker(c) for c in codes]
    code_map = {to_yf_ticker(c): str(c).replace(".T", "") for c in codes}

    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)

    # yfinanceの一括ダウンロード
    df_all = yf.download(
        tickers,
        start=start_date.strftime("%Y-%m-%d"),
        end=end_date.strftime("%Y-%m-%d"),
        progress=False,
        group_by="ticker",
    )

    if df_all.empty:
        return results

    for ticker in tickers:
        code = code_map[ticker]
        try:
            if len(tickers) == 1:
                # 単一銘柄の場合はMultiIndex構造が異なる
                df = df_all.copy()
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
            else:
                df = df_all[ticker].copy()

            df = df.dropna(how="all")
            cols = ["Open", "High", "Low", "Close", "Volume"]
            df = df[[c for c in cols if c in df.columns]]

            if not df.empty:
                results[code] = df
        except (KeyError, TypeError):
            continue

    return results


_info_cache = {}


def get_stock_info(code: str | int) -> dict:
    """銘柄の基本情報を取得する（キャッシュ付き）。"""
    code_str = str(code).replace(".T", "")
    if code_str in _info_cache:
        return _info_cache[code_str]

    ticker = yf.Ticker(to_yf_ticker(code))
    info = ticker.info

    result = {
        "code": code_str,
        "name": info.get("longName") or info.get("shortName", ""),
        "sector": info.get("sector", ""),
        "industry": info.get("industry", ""),
        "market_cap": info.get("marketCap", 0),
        "shares_outstanding": info.get("sharesOutstanding", 0),
        "float_shares": info.get("floatShares", 0),
        "fifty_two_week_low": info.get("fiftyTwoWeekLow", 0),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh", 0),
        "average_volume": info.get("averageVolume", 0),
        "average_volume_10d": info.get("averageDailyVolume10Day", 0),
        # ファンダ指標（バリュエーション用）
        "trailingPE": info.get("trailingPE", 0),
        "forwardPE": info.get("forwardPE", 0),
        "priceToBook": info.get("priceToBook", 0),
        "revenueGrowth": info.get("revenueGrowth", 0),
        "earningsGrowth": info.get("earningsGrowth", 0),
        "profitMargins": info.get("profitMargins", 0),
        "debtToEquity": info.get("debtToEquity", 0),
        "freeCashflow": info.get("freeCashflow", 0),
        "totalRevenue": info.get("totalRevenue", 0),
        "dividendYield": info.get("dividendYield", 0),
    }
    _info_cache[code_str] = result
    return result
