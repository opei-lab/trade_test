"""需給スコアリングモジュール

株価・出来高データから需給の逼迫度を数値化する。
スコアは0〜100で、高いほど「上がりやすい状態」を示す。
"""

import pandas as pd
import numpy as np


def calc_volume_anomaly(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """出来高異常度を計算する。

    直近出来高 / 過去N日の平均出来高。
    1.0が平常、3.0以上で「異常な出来高」。

    仕手Phase A: じわじわ上昇（1.2〜2.0）
    仕手Phase B/D: 急騰（3.0〜10.0+）
    """
    avg_volume = df["Volume"].rolling(window=window).mean()
    avg_volume = avg_volume.replace(0, np.nan)  # ゼロ除算防止
    return df["Volume"] / avg_volume


def calc_volatility_squeeze(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """ボリンジャーバンド幅からボラティリティ収縮を検出する。

    バンド幅が小さいほどスクイーズ状態（爆発前の静寂）。
    返り値は0〜100。100に近いほどスクイーズが強い。
    """
    close = df["Close"]
    sma = close.rolling(window=window).mean()
    std = close.rolling(window=window).std()

    # バンド幅 = (上限 - 下限) / 中心線
    band_width = (4 * std) / sma

    # 過去の分布内での位置（パーセンタイルの逆数: 小さいほど高スコア）
    rank = band_width.rolling(window=252, min_periods=window).rank(pct=True)
    squeeze_score = (1 - rank) * 100

    return squeeze_score


def calc_price_position(df: pd.DataFrame, window: int = 252) -> pd.Series:
    """株価が過去N日の値幅のどこに位置するかを返す。

    0: 底値付近、100: 高値付近。
    底値で買いたいので、低いほど好ましい。
    """
    close = df["Close"]
    rolling_high = close.rolling(window=window, min_periods=20).max()
    rolling_low = close.rolling(window=window, min_periods=20).min()

    range_width = rolling_high - rolling_low
    range_width = range_width.replace(0, np.nan)  # ゼロ除算防止
    position = (close - rolling_low) / range_width * 100
    position = position.fillna(50)  # レンジなし = 中立
    return position


def calc_volume_price_divergence(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """出来高と株価の乖離を検出する。

    株価が下がっているのに出来高が減少 → 売り枯れ（底値サイン）
    株価が上がっているのに出来高が減少 → 上昇に勢いがない

    返り値:
      正の値 → 売り枯れ（底打ちの兆候）
      負の値 → 買い枯れ
    """
    close_change = df["Close"].pct_change(window)
    volume_change = df["Volume"].pct_change(window)

    # 株価下落 + 出来高減少 → 正のスコア（売り枯れ）
    divergence = np.where(
        close_change < 0,
        -volume_change * 100,  # 出来高が減っていれば正
        volume_change * -50,
    )
    return pd.Series(divergence, index=df.index)


def calc_accumulation_signal(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """買い集めシグナルを検出する。

    Williams' Accumulation/Distribution に基づく。
    安値圏で出来高を伴う買いが入っているかを判定。
    """
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    volume = df["Volume"]

    # Close Location Value: 終値がレンジのどこにあるか (-1〜+1)
    clv = ((close - low) - (high - close)) / (high - low + 1e-10)

    # Accumulation/Distribution Line
    ad = (clv * volume).cumsum()
    ad_change = ad.pct_change(window) * 100

    return ad_change


def calc_supply_score(df: pd.DataFrame) -> dict:
    """需給の総合スコアを計算する。

    Returns:
        {
            "total": 総合スコア (0-100),
            "volume_anomaly": 出来高異常度,
            "squeeze": ボラティリティ収縮スコア,
            "price_position": 株価位置 (0=底, 100=天井),
            "divergence": 出来高乖離,
            "accumulation": 買い集めシグナル,
            "is_bottom": 底値判定 (True/False),
        }
    """
    if len(df) < 30:
        return {"total": 0, "error": "データ不足（30日以上必要）"}

    vol_anomaly = calc_volume_anomaly(df)
    squeeze = calc_volatility_squeeze(df)
    price_pos = calc_price_position(df)
    divergence = calc_volume_price_divergence(df)
    accumulation = calc_accumulation_signal(df)

    def safe_last(series, default=0):
        """NaN/inf安全に最新値を取得する。"""
        valid = series.replace([np.inf, -np.inf], np.nan).dropna()
        return round(float(valid.iloc[-1]), 2) if not valid.empty else default

    # 最新値を取得
    latest = {
        "volume_anomaly": safe_last(vol_anomaly),
        "squeeze": safe_last(squeeze),
        "price_position": safe_last(price_pos, 50),
        "divergence": safe_last(divergence),
        "accumulation": safe_last(accumulation),
    }

    # 底値判定: 複合条件
    is_bottom = (
        latest["price_position"] < 20  # 底値圏
        and latest["squeeze"] > 60  # ボラ収縮
        and latest["divergence"] > 0  # 売り枯れ
    )

    # 総合スコア計算
    # 底値圏ほど高スコア（逆転: 100 - price_position）
    bottom_score = max(0, 100 - latest["price_position"])
    squeeze_score = min(100, latest["squeeze"])
    divergence_score = min(100, max(0, latest["divergence"]))
    accumulation_score = min(100, max(-30, latest["accumulation"]))  # 分配中なら負の寄与

    # 出来高異常は文脈依存: 底値圏なら加点、天井圏なら減点
    vol_score = 0
    if latest["price_position"] < 30 and latest["volume_anomaly"] > 1.5:
        vol_score = min(100, latest["volume_anomaly"] * 20)
    elif latest["price_position"] > 70 and latest["volume_anomaly"] > 3:
        vol_score = -30  # 天井圏での出来高急増は警戒

    # 重み配分（バックテスト実績ベース: 底値圏が最も効果的）
    # 底値圏: リフト+1.7% → 最大重み
    # 売り枯れ(divergence): 需給枯渇の直接指標
    # 出来高異常: 底値圏での出来高増加は強いシグナル
    # 買い集め: 底値圏とのセットで有効
    # ボラ収縮: 単独では逆効果（-1.6%）→ 重みを大幅削減
    total = (
        bottom_score * 0.40
        + divergence_score * 0.25
        + vol_score * 0.15
        + accumulation_score * 0.10
        + squeeze_score * 0.10
    )
    total = round(max(0, min(100, total)), 1)

    latest["total"] = total
    latest["is_bottom"] = is_bottom

    return latest
