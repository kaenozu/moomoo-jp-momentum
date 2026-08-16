from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

from ..contracts import CanonicalBar, MarketSnapshot


class SQLiteReadOnlyBarSource:
    """Read daily_bars without allowing the V2 process to mutate the database."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.resolve()

    def load_snapshots(
        self,
        start: date,
        end: date,
        *,
        codes: set[str] | None = None,
        benchmark_code: str | None = None,
    ) -> tuple[MarketSnapshot, ...]:
        if not self.db_path.is_file():
            raise FileNotFoundError(f"database not found: {self.db_path}")
        if end < start:
            raise ValueError("end must not precede start")
        grouped: defaultdict[date, list[CanonicalBar]] = defaultdict(list)
        with self._connect() as connection:
            query = """
                SELECT code, date, open, high, low, close, volume
                FROM daily_bars
                WHERE date >= ? AND date <= ?
                  AND open IS NOT NULL AND high IS NOT NULL
                  AND low IS NOT NULL AND close IS NOT NULL
                ORDER BY date, code
            """
            for row in connection.execute(query, (start.isoformat(), end.isoformat())):
                code = str(row[0])
                if codes is not None and code not in codes:
                    continue
                bar_date = date.fromisoformat(str(row[1]))
                grouped[bar_date].append(
                    CanonicalBar(
                        code=code,
                        date=bar_date,
                        open=float(row[2]),
                        high=float(row[3]),
                        low=float(row[4]),
                        close=float(row[5]),
                        volume=float(row[6] or 0.0),
                    )
                )
        return tuple(
            MarketSnapshot.from_bars(bars, benchmark=benchmark_code)
            for _, bars in sorted(grouped.items())
            if bars
        )

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        return sqlite3.connect(uri, uri=True)
