"""
US ETF market data loading for the grid backtest.

Data is fetched from yfinance with ``auto_adjust=True`` (split- and
dividend-adjusted OHLC), which matches the existing project's policy for the
JP pipeline (src/split_adjustment.py treats prices as QFQ-adjusted and applies
no additional adjustment). Raw split/dividend events are recorded separately
from yfinance's ``actions`` frame so the accounting layer can credit
dividends and adjust quantities without double counting.

Reproducibility: fetched bars are cached to a per-symbol CSV under the
configured data_dir; the manifest records a data hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class UsDataBundle:
    bars: dict[str, list[dict]] = field(
        default_factory=dict
    )  # code -> [{date, open, high, low, close, volume}]
    splits: dict[str, list[dict]] = field(
        default_factory=dict
    )  # code -> [{date, ratio}]
    dividends: dict[str, list[dict]] = field(
        default_factory=dict
    )  # code -> [{date, per_share}]
    fx: list[dict] = field(default_factory=list)  # [{date, rate}] USDJPY
    sources: list[str] = field(default_factory=list)
    data_hash: str = ""


def _hash_data(bundle: UsDataBundle) -> str:
    payload = json.dumps(
        {
            "bars": bundle.bars,
            "splits": bundle.splits,
            "dividends": bundle.dividends,
            "fx": bundle.fx,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _normalize_ticker(symbol: str) -> str:
    """US.SPY -> SPY."""
    if symbol.startswith("US."):
        return symbol.removeprefix("US.")
    return symbol


def load_or_fetch(
    symbols: list[str],
    start_date: str,
    end_date: str,
    data_dir: str | Path,
    *,
    fetch: bool = True,
    fx_start_date: str | None = None,
) -> UsDataBundle:
    """Load cached data or fetch from yfinance, returning a data bundle.

    When ``fetch=False``, only cached data is used and symbols without a
    cache are skipped (the backtest never makes network calls by default).
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    bundle = UsDataBundle()
    bundle.sources = []
    for symbol in symbols:
        ticker = _normalize_ticker(symbol)
        cache_path = data_dir / f"{ticker}.csv"
        if cache_path.exists():
            df = pd.read_csv(cache_path)
            if not df.empty:
                rows = df.to_dict(orient="records")
                bundle.bars[symbol] = rows
                bundle.sources.append(f"cache:{ticker}")
                continue
        if not fetch:
            continue
        bundle.bars[symbol] = _fetch_symbol(ticker, start_date, end_date, cache_path)
        bundle.sources.append(f"yfinance:{ticker}")

    # FX: USDJPY.
    fx_cache = data_dir / "USDJPY.csv"
    if fx_cache.exists():
        df = pd.read_csv(fx_cache)
        bundle.fx = df.to_dict(orient="records")
        bundle.sources.append("cache:USDJPY")
    elif fetch:
        bundle.fx = _fetch_fx(fx_start_date or start_date, end_date, fx_cache)
        bundle.sources.append("yfinance:USDJPY")

    # Corporate actions.
    splits_path = data_dir / "splits.json"
    dividends_path = data_dir / "dividends.json"
    if splits_path.exists():
        bundle.splits = json.loads(splits_path.read_text(encoding="utf-8"))
    if dividends_path.exists():
        bundle.dividends = json.loads(dividends_path.read_text(encoding="utf-8"))
    if fetch:
        for symbol in symbols:
            ticker = _normalize_ticker(symbol)
            bundle.splits[symbol], bundle.dividends[symbol] = _fetch_actions(
                ticker, start_date, end_date, data_dir
            )
        if splits_path.exists() or bundle.splits:
            splits_path.write_text(json.dumps(bundle.splits), encoding="utf-8")
        if dividends_path.exists() or bundle.dividends:
            dividends_path.write_text(json.dumps(bundle.dividends), encoding="utf-8")

    bundle.data_hash = _hash_data(bundle)
    return bundle


def _fetch_symbol(
    ticker: str, start_date: str, end_date: str, cache_path: Path
) -> list[dict]:
    import yfinance as yf

    from .yfinance_utils import exclusive_end

    data = yf.Ticker(ticker).history(
        start=start_date,
        end=exclusive_end(end_date),
        interval="1d",
        auto_adjust=True,
    )
    if data.empty:
        cache_path.write_text("", encoding="utf-8")
        return []
    df = data.reset_index()
    # The index column name varies (Date/Datetime); normalise explicitly.
    df.columns = [str(c) for c in df.columns]
    df["date"] = pd.to_datetime(df.iloc[:, 0]).dt.strftime("%Y-%m-%d")
    out = df[["date", "Open", "High", "Low", "Close", "Volume"]].rename(  # type: ignore[call-overload]
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    rows = out.to_dict(orient="records")  # type: ignore[call-overload]
    out.to_csv(cache_path, index=False)
    return rows


def _fetch_fx(start_date: str, end_date: str, cache_path: Path) -> list[dict]:
    import yfinance as yf

    from .yfinance_utils import exclusive_end

    data = yf.Ticker("JPY=X").history(
        start=start_date,
        end=exclusive_end(end_date),
        interval="1d",
        auto_adjust=True,
    )
    if data.empty:
        return []
    df = data.reset_index()
    df.columns = [str(c) for c in df.columns]
    df["date"] = pd.to_datetime(df.iloc[:, 0]).dt.strftime("%Y-%m-%d")
    df = df.rename(columns={"Close": "rate"})  # type: ignore[call-overload]
    rows = (
        df[["date", "rate"]]
        .dropna()
        .to_dict(  # type: ignore[call-overload]
            orient="records"
        )
    )
    pd.DataFrame(rows).to_csv(cache_path, index=False)
    return rows


def _fetch_actions(
    ticker: str, start_date: str, end_date: str, data_dir: Path
) -> tuple[list[dict], list[dict]]:
    import yfinance as yf

    from .yfinance_utils import exclusive_end

    splits: list[dict] = []
    dividends: list[dict] = []
    try:
        data = yf.Ticker(ticker).history(
            start=start_date,
            end=exclusive_end(end_date),
            interval="1d",
            auto_adjust=False,
            actions=True,
        )
        if data.empty:
            return splits, dividends
        if "Stock Splits" in data.columns:
            for date, value in data["Stock Splits"].items():
                ratio = _scalar(value)
                if ratio > 0:
                    splits.append(
                        {
                            "date": _date_str(date),
                            "ratio": ratio,
                        }
                    )
        if "Dividends" in data.columns:
            for date, value in data["Dividends"].items():
                amount = _scalar(value)
                if amount > 0:
                    dividends.append(
                        {
                            "date": _date_str(date),
                            "per_share": amount,
                        }
                    )
    except Exception as exc:  # pragma: no cover - network boundary
        # Corporate action data is best-effort. Prices are QFQ-adjusted by the
        # fetcher, so a missing actions frame means dividends/splits are NOT
        # credited while the price history IS adjusted — surface this so
        # results are not silently overstated.
        import warnings

        warnings.warn(
            f"corporate action fetch failed for {ticker}: {exc}; "
            "dividends/splits for this symbol will not be credited",
            stacklevel=2,
        )
    return splits, dividends


def _scalar(value: object) -> float:
    """Coerce a pandas scalar (numpy type / Series of size 1) to float."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _date_str(value: object) -> str:
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")  # type: ignore[arg-type]
    except Exception:
        return str(value)


def attach_corporate_actions(
    bundle: UsDataBundle,
) -> dict[str, list[dict[str, Any]]]:
    """Merge splits + dividends into a per-symbol action list for the engine."""
    merged: dict[str, list[dict[str, Any]]] = {}
    for symbol in bundle.bars:
        actions: list[dict[str, Any]] = []
        for split in bundle.splits.get(symbol, []):
            actions.append(
                {"date": split["date"], "kind": "split", "ratio": split["ratio"]}
            )
        for dividend in bundle.dividends.get(symbol, []):
            actions.append(
                {
                    "date": dividend["date"],
                    "kind": "dividend",
                    "per_share": dividend["per_share"],
                }
            )
        actions.sort(key=lambda a: a["date"])
        merged[symbol] = actions
    return merged
