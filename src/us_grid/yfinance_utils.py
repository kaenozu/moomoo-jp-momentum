"""Shared yfinance helpers for the US grid data layer."""

from __future__ import annotations

from datetime import datetime, timedelta


def exclusive_end(end_date: str) -> str:
    """Convert an inclusive end date to yfinance's exclusive end."""
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    return (end + timedelta(days=1)).strftime("%Y-%m-%d")
