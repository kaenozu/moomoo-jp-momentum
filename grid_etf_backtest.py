"""SQLiteの日足を読み取り専用で `grid_etf_v1` に渡す検証CLI。"""

import argparse
import sqlite3
from pathlib import Path

from src.grid_etf import GridBar, GridConfig, GridEtfV1
from src.grid_etf_ledger import GridEtfStateStore


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
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--atr-multiplier", type=float, default=0.75)
    parser.add_argument("--levels", type=int, default=4)
    parser.add_argument("--strategy-name", default="grid_etf_v1")
    parser.add_argument(
        "--persist",
        action="store_true",
        help="専用grid_etf_*テーブルへ状態を保存し、次回から再開する",
    )
    args = parser.parse_args()

    bars = load_bars(args.db, args.code, args.start, args.end)
    if not bars:
        parser.error("指定期間の日足がありません")
    config = GridConfig(
        initial_cash=args.initial_cash,
        strategy_name=args.strategy_name,
        atr_period=args.atr_period,
        atr_multiplier=args.atr_multiplier,
        levels=args.levels,
    )
    if args.persist:
        store = GridEtfStateStore(args.db)
        strategy = store.load(args.strategy_name, args.code, config)
        if strategy is not None and strategy._bars:
            bars = [bar for bar in bars if bar.date > strategy._bars[-1].date]
        for bar in bars:
            store.apply_bar(args.strategy_name, args.code, config, bar)
        strategy = store.load(args.strategy_name, args.code, config)
        if strategy is None:
            parser.error("grid_etf状態を保存できませんでした")
        result = strategy.backtest([])
    else:
        result = GridEtfV1(config).backtest(bars)
    print(f"strategy={result.strategy_name}")
    print(f"code={args.code} bars={len(bars)}")
    print(f"initial_cash={result.initial_cash:.0f}")
    print(f"final_equity={result.final_equity:.0f}")
    print(f"max_drawdown_pct={result.max_drawdown_pct:.2f}")
    print(f"fills={len(result.fills)} stopped={result.stopped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
