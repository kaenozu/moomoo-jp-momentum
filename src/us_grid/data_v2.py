"""Fail-closed market-data policy for the US grid research.

Yahoo/yfinance raw OHLC is split-adjusted but not dividend-adjusted. The
research therefore uses ``auto_adjust=False`` OHLC and credits cash dividends
exactly once in the accounting layer. Old caches created from dividend-
adjusted OHLC are rejected instead of being silently mixed with this policy.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from . import data as _legacy
from .data import UsDataBundle

PRICE_BASIS = "YAHOO_SPLIT_ADJUSTED_OHLC_CASH_DIVIDENDS_V2"


class UsDataPolicyError(ValueError):
    """Raised when cached data does not satisfy the canonical price policy."""


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    columns = [str(column) for column in frame.columns]
    return [
        {column: value for column, value in zip(columns, row, strict=True)}
        for row in frame.itertuples(index=False, name=None)
    ]


def _hash_data(bundle: UsDataBundle) -> str:
    payload = json.dumps(
        {
            "price_basis": PRICE_BASIS,
            "bars": bundle.bars,
            "splits_audit_only": bundle.splits,
            "dividends": bundle.dividends,
            "fx": bundle.fx,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _read_price_cache(path: Path) -> list[dict]:
    frame = pd.read_csv(path)
    if frame.empty:
        return []
    if "price_basis" not in frame.columns:
        raise UsDataPolicyError(
            f"legacy US-grid cache has no price_basis and must be regenerated: {path}"
        )
    bases = {str(value) for value in frame["price_basis"].dropna().unique()}
    if bases != {PRICE_BASIS}:
        raise UsDataPolicyError(
            f"unsupported US-grid cache price_basis {sorted(bases)}: {path}"
        )
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise UsDataPolicyError(f"US-grid cache is missing columns {sorted(missing)}: {path}")
    return _records(frame.drop(columns=["price_basis"]))


def _fetch_symbol(
    ticker: str,
    start_date: str,
    end_date: str,
    cache_path: Path,
) -> list[dict]:
    import yfinance as yf

    from .yfinance_utils import exclusive_end

    frame = yf.Ticker(ticker).history(
        start=start_date,
        end=exclusive_end(end_date),
        interval="1d",
        auto_adjust=False,
        actions=False,
    )
    if frame.empty:
        cache_path.write_text("", encoding="utf-8")
        return []
    frame = frame.reset_index()
    frame.columns = [str(column) for column in frame.columns]
    frame["date"] = pd.to_datetime(frame.iloc[:, 0]).dt.strftime("%Y-%m-%d")
    output = frame[["date", "Open", "High", "Low", "Close", "Volume"]].copy()
    output.columns = ["date", "open", "high", "low", "close", "volume"]
    output["price_basis"] = PRICE_BASIS
    output.to_csv(cache_path, index=False)
    return _records(output.drop(columns=["price_basis"]))


def load_or_fetch(
    symbols: list[str],
    start_date: str,
    end_date: str,
    data_dir: str | Path,
    *,
    fetch: bool = True,
    fx_start_date: str | None = None,
) -> UsDataBundle:
    """Load canonical caches or explicitly fetch and replace stale caches."""
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    bundle = UsDataBundle()

    for symbol in symbols:
        ticker = _legacy._normalize_ticker(symbol)
        cache_path = directory / f"{ticker}.csv"
        rows: list[dict] | None = None
        if cache_path.exists() and cache_path.stat().st_size > 0:
            try:
                rows = _read_price_cache(cache_path)
            except UsDataPolicyError:
                if not fetch:
                    raise
        if rows is None:
            if not fetch:
                continue
            rows = _fetch_symbol(ticker, start_date, end_date, cache_path)
            bundle.sources.append(f"yfinance-raw:{ticker}")
        else:
            bundle.sources.append(f"cache-v2:{ticker}")
        if rows:
            bundle.bars[symbol] = rows

    fx_cache = directory / "USDJPY.csv"
    if fx_cache.exists() and fx_cache.stat().st_size > 0:
        frame = pd.read_csv(fx_cache)
        bundle.fx = _records(frame)
        bundle.sources.append("cache:USDJPY")
    elif fetch:
        bundle.fx = _legacy._fetch_fx(
            fx_start_date or start_date,
            end_date,
            fx_cache,
        )
        bundle.sources.append("yfinance:USDJPY")

    splits_path = directory / "splits.json"
    dividends_path = directory / "dividends.json"
    if splits_path.exists():
        bundle.splits = json.loads(splits_path.read_text(encoding="utf-8"))
    if dividends_path.exists():
        bundle.dividends = json.loads(dividends_path.read_text(encoding="utf-8"))
    if fetch:
        for symbol in symbols:
            ticker = _legacy._normalize_ticker(symbol)
            bundle.splits[symbol], bundle.dividends[symbol] = _legacy._fetch_actions(
                ticker,
                start_date,
                end_date,
                directory,
            )
        splits_path.write_text(
            json.dumps(bundle.splits, sort_keys=True),
            encoding="utf-8",
        )
        dividends_path.write_text(
            json.dumps(bundle.dividends, sort_keys=True),
            encoding="utf-8",
        )

    bundle.data_hash = _hash_data(bundle)
    return bundle


def attach_corporate_actions(
    bundle: UsDataBundle,
) -> dict[str, list[dict]]:
    """Return cash dividends only; split events are audit metadata.

    Yahoo raw OHLC is already split-adjusted. Applying split ratios to
    quantities again would double-adjust the portfolio.
    """
    merged: dict[str, list[dict]] = {}
    for symbol in bundle.bars:
        actions = [
            {
                "date": dividend["date"],
                "kind": "dividend",
                "per_share": dividend["per_share"],
            }
            for dividend in bundle.dividends.get(symbol, [])
        ]
        actions.sort(key=lambda action: str(action["date"]))
        merged[symbol] = actions
    return merged
