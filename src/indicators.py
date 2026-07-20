"""
指標計算モジュール

ファイルパス: src/indicators.py
何をするか: 日足データから各種テクニカル指標を計算する
なぜ存在するか: シグナル判定に必要な指標を一括計算するため
関連ファイル: data_store.py, models.py
"""

import logging
import weakref
from collections.abc import Callable
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
    return_20d: Optional[float] = None
    return_60d: Optional[float] = None

    history_days: int = 0

    return_5d_vs_benchmark: Optional[float] = None
    return_20d_vs_benchmark: Optional[float] = None
    return_60d_vs_benchmark: Optional[float] = None
    relative_strength_rank: Optional[int] = None

    score: Optional[float] = None
    signal_type: Optional[str] = None
    reason: Optional[str] = None

    # クロスセクション統計（バッチ計算後に入る）
    volume_ratio_percentile: Optional[float] = None
    volume_ratio_rank: Optional[int] = None
    relative_volume_ratio: Optional[float] = None
    market_median_volume_ratio: Optional[float] = None


CrossSectionalObserver = Callable[[list[StockIndicators]], None]
_cross_sectional_observers: list[weakref.ReferenceType] = []


def register_cross_sectional_observer(observer: CrossSectionalObserver) -> None:
    """日次クロスセクション計算後に呼ぶobserverを弱参照で登録する。"""
    reference: weakref.ReferenceType
    if getattr(observer, "__self__", None) is not None:
        reference = weakref.WeakMethod(observer)
    else:
        reference = weakref.ref(observer)

    for existing in _cross_sectional_observers:
        if existing() == observer:
            return
    _cross_sectional_observers.append(reference)


def unregister_cross_sectional_observer(observer: CrossSectionalObserver) -> None:
    """登録済みobserverを解除する。"""
    _cross_sectional_observers[:] = [
        reference
        for reference in _cross_sectional_observers
        if reference() is not None and reference() != observer
    ]


def _notify_cross_sectional_observers(indicators: list[StockIndicators]) -> None:
    live_references: list[weakref.ReferenceType] = []
    for reference in _cross_sectional_observers:
        observer = reference()
        if observer is None:
            continue
        live_references.append(reference)
        observer(indicators)
    _cross_sectional_observers[:] = live_references


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


def _period_return(df: pd.DataFrame, days: int) -> Optional[float]:
    if len(df) < days + 1:
        return None
    close_now = float(df["close"].iloc[0])
    close_then = float(df["close"].iloc[days])
    if close_then <= 0:
        return None
    return (close_now - close_then) / close_then * 100


def calculate_indicators(df: pd.DataFrame, code: str, name: Optional[str] = None) -> Optional[StockIndicators]:
    """日足データから指標を計算する。"""
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

    ma5 = float(df["close"].iloc[:5].mean()) if len(df) >= 5 else None
    ma25 = float(df["close"].iloc[:25].mean()) if len(df) >= 25 else None
    ma5_deviation = close - ma5 if ma5 is not None else None
    ma25_deviation = close - ma25 if ma25 is not None else None

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

    return_5d = _period_return(df, 5)
    return_20d = _period_return(df, 20)
    return_60d = _period_return(df, 60)

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
        return_20d=return_20d,
        return_60d=return_60d,
        history_days=len(df),
    )


def add_cross_sectional_stats(indicators: list[StockIndicators]) -> list[StockIndicators]:
    """クロスセクション統計を計算し、登録済み戦略へ日次候補集合を通知する。"""
    if not indicators:
        return indicators

    ratios = [ind.volume_ratio for ind in indicators if ind.volume_ratio is not None]
    if ratios:
        import statistics

        market_median = statistics.median(ratios)
        sorted_ratios = sorted(ratios)

        for ind in indicators:
            vr = ind.volume_ratio
            if vr is not None:
                ind.market_median_volume_ratio = market_median
                ind.relative_volume_ratio = vr / market_median if market_median > 0 else 1.0
                count_le = sum(1 for ratio in sorted_ratios if ratio <= vr)
                ind.volume_ratio_percentile = count_le / len(sorted_ratios) * 100
                ind.volume_ratio_rank = sum(1 for ratio in sorted_ratios if ratio > vr) + 1

    _notify_cross_sectional_observers(indicators)
    return indicators


def calculate_indicators_batch(
    data_dict: dict[str, pd.DataFrame],
    symbols_info: Optional[dict[str, str]] = None,
) -> list[StockIndicators]:
    results = []
    for code, df in data_dict.items():
        name = symbols_info.get(code) if symbols_info else None
        indicators = calculate_indicators(df, code, name)
        if indicators:
            results.append(indicators)
    results = add_cross_sectional_stats(results)
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
            "return_20d": ind.return_20d,
            "return_60d": ind.return_60d,
            "history_days": ind.history_days,
            "return_5d_vs_benchmark": ind.return_5d_vs_benchmark,
            "return_20d_vs_benchmark": ind.return_20d_vs_benchmark,
            "return_60d_vs_benchmark": ind.return_60d_vs_benchmark,
            "relative_strength_rank": ind.relative_strength_rank,
            "score": ind.score,
            "signal_type": ind.signal_type,
            "reason": ind.reason,
            "volume_ratio_percentile": ind.volume_ratio_percentile,
            "volume_ratio_rank": ind.volume_ratio_rank,
            "relative_volume_ratio": ind.relative_volume_ratio,
            "market_median_volume_ratio": ind.market_median_volume_ratio,
        })
    return pd.DataFrame(records)


def add_relative_strength(indicators_df: pd.DataFrame, benchmark_code: str = "JP.1306") -> pd.DataFrame:
    """同一日付のベンチマークリターンとの差分を日付でjoinして計算する。"""
    if indicators_df.empty or benchmark_code not in set(indicators_df["code"]):
        return indicators_df

    df = indicators_df.copy()
    bench = df.loc[df["code"] == benchmark_code, ["date", "return_5d", "return_20d", "return_60d"]]
    if bench.empty:
        return df

    bench = bench.rename(columns={
        "return_5d": "bench_return_5d",
        "return_20d": "bench_return_20d",
        "return_60d": "bench_return_60d",
    })

    df = df.merge(bench, on="date", how="left")
    df["return_5d_vs_benchmark"] = df["return_5d"] - df["bench_return_5d"]
    if "return_20d" in df.columns:
        df["return_20d_vs_benchmark"] = df["return_20d"] - df["bench_return_20d"]
    if "return_60d" in df.columns:
        df["return_60d_vs_benchmark"] = df["return_60d"] - df["bench_return_60d"]
    df["relative_strength_rank"] = df["return_5d_vs_benchmark"].rank(ascending=False, method="min")
    df = df.drop(columns=["bench_return_5d", "bench_return_20d", "bench_return_60d"], errors="ignore")
    return df
