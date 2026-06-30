"""
指標計算モジュール

ファイルパス: src/indicators.py
何をするか: 日足データから各種テクニカル指標を計算する
なぜ存在するか: シグナル判定に必要な指標を一括計算するため
関連ファイル: data_store.py, models.py
"""

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class StockIndicators:
    """1銘柄の指標計算結果"""
    code: str
    name: Optional[str]
    date: str

    close: float
    open: float
    high: float
    low: float

    ma5: Optional[float] = None
    ma25: Optional[float] = None
    ma5_deviation: Optional[float] = None
    ma25_deviation: Optional[float] = None

    volume: int = 0
    volume_ma20: Optional[float] = None
    volume_ratio: Optional[float] = None

    turnover: float = 0.0
    turnover_yen: float = 0.0

    high_20d: Optional[float] = None
    high_20d_distance: Optional[float] = None

    prev_close: Optional[float] = None
    daily_return: Optional[float] = None
    return_5d: Optional[float] = None

    history_days: int = 0

    return_5d_vs_benchmark: Optional[float] = None
    return_20d_vs_benchmark: Optional[float] = None
    return_60d_vs_benchmark: Optional[float] = None
    relative_strength_rank: Optional[int] = None

    score: Optional[float] = None
    signal_type: Optional[str] = None
    reason: Optional[str] = None


def _normalize_daily_df(df: pd.DataFrame) -> pd.DataFrame:
    """futu-api取得DFとDB取得DFの両方を計算しやすい形に正規化する。"""
    if df.empty:
        return df

    df = df.copy()
    if "time_key" not in df.columns and "date" in df.columns:
        df["time_key"] = df["date"]

    for col in ["close", "open", "high", "low", "volume", "turnover"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.sort_values("time_key", ascending=False).reset_index(drop=True)


def calculate_indicators(
    df: pd.DataFrame,
    code: str,
    name: Optional[str] = None,
) -> Optional[StockIndicators]:
    """
    日足データから指標を計算する。

    Args:
        df: 日足データ。time_key または date を含むDataFrame。
        code: 銘柄コード
        name: 銘柄名

    Returns:
        StockIndicators。データ不足の場合はNone。
    """
    if df.empty or len(df) < 5:
        logger.warning("データ不足: %s (%s件)", code, len(df))
        return None

    df = _normalize_daily_df(df)

    latest = df.iloc[0]
    date = str(latest.get("time_key", ""))[:10]
    close = float(latest.get("close", 0) or 0)
    open_price = float(latest.get("open", 0) or 0)
    high = float(latest.get("high", 0) or 0)
    low = float(latest.get("low", 0) or 0)
    volume = int(latest.get("volume", 0) or 0)
    turnover = float(latest.get("turnover", 0) or 0)

    ma5 = None
    ma5_deviation = None
    if len(df) >= 5:
        ma5 = float(df["close"].iloc[:5].mean())
        ma5_deviation = close - ma5

    ma25 = None
    ma25_deviation = None
    if len(df) >= 25:
        ma25 = float(df["close"].iloc[:25].mean())
        ma25_deviation = close - ma25

    volume_ma20 = None
    volume_ratio = None
    if len(df) >= 20:
        volume_ma20 = float(df["volume"].iloc[:20].mean())
        if volume_ma20 > 0:
            volume_ratio = volume / volume_ma20

    high_20d = None
    high_20d_distance = None
    if len(df) >= 20:
        high_20d = float(df["high"].iloc[:20].max())
        if high_20d > 0:
            high_20d_distance = (close - high_20d) / high_20d * 100

    prev_close = None
    daily_return = None
    if len(df) >= 2:
        prev_close = float(df["close"].iloc[1])
        if prev_close > 0:
            daily_return = (close - prev_close) / prev_close * 100

    return_5d = None
    if len(df) >= 6:
        close_5d_ago = float(df["close"].iloc[5])
        if close_5d_ago > 0:
            return_5d = (close - close_5d_ago) / close_5d_ago * 100

    return StockIndicators(
        code=code,
        name=name,
        date=date,
        close=close,
        open=open_price,
        high=high,
        low=low,
        ma5=ma5,
        ma25=ma25,
        ma5_deviation=ma5_deviation,
        ma25_deviation=ma25_deviation,
        volume=volume,
        volume_ma20=volume_ma20,
        volume_ratio=volume_ratio,
        turnover=turnover,
        turnover_yen=turnover,
        high_20d=high_20d,
        high_20d_distance=high_20d_distance,
        prev_close=prev_close,
        daily_return=daily_return,
        return_5d=return_5d,
        history_days=len(df),
    )


def calculate_indicators_batch(
    data_dict: dict[str, pd.DataFrame],
    symbols_info: Optional[dict[str, str]] = None,
) -> list[StockIndicators]:
    """複数銘柄の指標を一括計算する"""
    results = []

    for code, df in data_dict.items():
        name = symbols_info.get(code) if symbols_info else None
        indicators = calculate_indicators(df, code, name)
        if indicators:
            results.append(indicators)

    logger.info("指標計算完了: %s/%s銘柄", len(results), len(data_dict))
    return results


def indicators_to_dataframe(indicators: list[StockIndicators]) -> pd.DataFrame:
    """指標リストをDataFrameに変換する"""
    if not indicators:
        return pd.DataFrame()

    records = []
    for ind in indicators:
        records.append({
            "code": ind.code,
            "name": ind.name,
            "date": ind.date,
            "close": ind.close,
            "open": ind.open,
            "high": ind.high,
            "low": ind.low,
            "ma5": ind.ma5,
            "ma25": ind.ma25,
            "ma5_deviation": ind.ma5_deviation,
            "ma25_deviation": ind.ma25_deviation,
            "volume": ind.volume,
            "volume_ma20": ind.volume_ma20,
            "volume_ratio": ind.volume_ratio,
            "turnover": ind.turnover,
            "turnover_yen": ind.turnover_yen,
            "high_20d": ind.high_20d,
            "high_20d_distance": ind.high_20d_distance,
            "prev_close": ind.prev_close,
            "daily_return": ind.daily_return,
            "return_5d": ind.return_5d,
            "history_days": ind.history_days,
            "score": ind.score,
            "signal_type": ind.signal_type,
            "reason": ind.reason,
        })

    return pd.DataFrame(records)
