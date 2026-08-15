from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class CanonicalBar:
    code: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("code must not be empty")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC range is inconsistent")
        if self.volume < 0:
            raise ValueError("volume must not be negative")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    date: date
    bars: tuple[CanonicalBar, ...]
    benchmark: str | None = None

    @classmethod
    def from_bars(
        cls, bars: Sequence[CanonicalBar], *, benchmark: str | None = None
    ) -> "MarketSnapshot":
        if not bars:
            raise ValueError("snapshot requires at least one bar")
        dates = {bar.date for bar in bars}
        if len(dates) != 1:
            raise ValueError("all bars in a snapshot must have the same date")
        return cls(date=next(iter(dates)), bars=tuple(sorted(bars, key=lambda bar: bar.code)), benchmark=benchmark)


class Strategy(Protocol):
    name: str

    def scores(self, snapshot: MarketSnapshot) -> dict[str, float]:
        """Return deterministic non-negative preference scores by symbol."""
        ...
