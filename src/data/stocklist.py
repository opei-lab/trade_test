"""東証上場銘柄リスト取得モジュール

JPX公式の上場銘柄一覧（Excel）を取得し、銘柄コード・銘柄名・市場区分・業種を返す。
"""

import pandas as pd
from pathlib import Path
from datetime import datetime

CACHE_DIR = Path(__file__).parent.parent.parent / "data"
CACHE_FILE = CACHE_DIR / "stocklist.csv"
JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"


def fetch_stocklist(use_cache: bool = True) -> pd.DataFrame:
    """JPX公式の上場銘柄一覧を取得する。

    Returns:
        DataFrame with columns: code, name, market, sector
    """
    # キャッシュが今日のものならそれを使う
    if use_cache and CACHE_FILE.exists():
        cache_mtime = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        if cache_mtime.date() == datetime.now().date():
            return pd.read_csv(CACHE_FILE, dtype={"code": str})

    try:
        df = pd.read_excel(JPX_URL)
    except Exception:
        # URL変更時のフォールバック
        import requests
        from bs4 import BeautifulSoup

        landing = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
        resp = requests.get(landing)
        soup = BeautifulSoup(resp.content, "html.parser")
        link = soup.find("a", href=lambda h: h and "data_j" in h and h.endswith(".xls"))
        if link:
            xls_url = "https://www.jpx.co.jp" + link["href"]
            df = pd.read_excel(xls_url)
        else:
            if CACHE_FILE.exists():
                return pd.read_csv(CACHE_FILE, dtype={"code": str})
            raise RuntimeError("JPX stocklist unavailable and no cache found")

    # カラム名を正規化
    result = pd.DataFrame({
        "code": df.iloc[:, 1].astype(str),
        "name": df.iloc[:, 2].astype(str),
        "market": df.iloc[:, 3].astype(str),
        "sector": df.iloc[:, 5].astype(str),
    })

    # 数字のコードのみ（ETF等の除外）
    result = result[result["code"].str.match(r"^\d{4}$")].reset_index(drop=True)

    # キャッシュ保存
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    result.to_csv(CACHE_FILE, index=False)

    return result


def get_growth_stocks() -> pd.DataFrame:
    """グロース市場の銘柄一覧を返す。"""
    df = fetch_stocklist()
    return df[df["market"].str.contains("グロース")].reset_index(drop=True)


def get_low_price_stocks(max_price: int = 500) -> pd.DataFrame:
    """全市場（プライム+スタンダード+グロース）から低価格銘柄を返す。

    バックテスト検証: 500円以下で全市場コンボが効く（S+ 92.5%, T1 77.4%）。
    スタンダードでもbot20+gf30で68%。銘柄数が約2倍に増える。
    """
    df = fetch_stocklist()
    # ETF・REIT等を除外（市場名にプライム/スタンダード/グロースを含むもののみ）
    markets = df["market"].str.contains("プライム|スタンダード|グロース", na=False)
    return df[markets].reset_index(drop=True)


def get_stocks_by_sector(sector_keyword: str) -> pd.DataFrame:
    """業種キーワードで銘柄を絞り込む。"""
    df = fetch_stocklist()
    return df[df["sector"].str.contains(sector_keyword, na=False)].reset_index(drop=True)
