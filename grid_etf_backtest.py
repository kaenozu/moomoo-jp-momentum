"""SQLiteの日足を読み取り専用で `grid_etf_v1` に渡す検証CLI。"""

import argparse
import sqlite3
from pathlib import Path

from src.grid_etf import GridBar, GridConfig, GridEtfV1


def load_bars(db_path: Path, code: str, start: str, end: str) -> list[GridBar]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT date, open, high, low, close
            FROM daily_bars
            WHERE code = ? AND date >= ? AND date <= ?
              AND open IS NOT NULL AND high IS NOT NULL
              AND low IS NOT NULL AND close IS NOT NULL
            ORDER BY date
            """,
            (code, start, end),
        ).fetchall()
    return [GridBar(str(date), float(open_), float(high), float(low), float(close)) for date, open_, high, low, close in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="grid_etf_v1 の読み取り専用バックテスト")
    parser.add_argument("--db", type=Path, default=Path("data/moomoo.db"))
    parser.add_argument("--code", required=True, help="例: JP.1306")
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    args = parser.parse_args()

    bars = load_bars(args.db, args.code, args.start, args.end)
    if not bars:
        parser.error("指定期間の日足がありません")
    result = GridEtfV1(GridConfig(initial_cash=args.initial_cash)).backtest(bars)
    print(f"strategy={result.strategy_name}")
    print(f"code={args.code} bars={len(bars)}")
    print(f"initial_cash={result.initial_cash:.0f}")
    print(f"final_equity={result.final_equity:.0f}")
    print(f"max_drawdown_pct={result.max_drawdown_pct:.2f}")
    print(f"fills={len(result.fills)} stopped={result.stopped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
