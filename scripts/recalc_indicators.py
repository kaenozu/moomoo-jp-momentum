"""daily_bars全期間からindicatorsを再計算する。"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from daily_update import save_benchmark_prices_from_indicators, save_indicators_to_db
from src.config import load_config
from src.data_store import DataStore
from src.indicators import (
    add_relative_strength,
    calculate_indicators_batch,
    indicators_to_dataframe,
)

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def recalculate(config_path: str, *, min_history: int = 25) -> dict[str, int | str | None]:
    if min_history <= 0:
        raise ValueError("min_historyは1以上にしてください")

    config = load_config(config_path)
    data_store = DataStore(config)
    db_path = Path(config.database_path)

    with sqlite3.connect(db_path) as conn:
        symbol_rows = conn.execute(
            "SELECT code, name FROM symbols WHERE enabled = 1 ORDER BY code"
        ).fetchall()
        bar_codes = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT d.code
                FROM daily_bars d
                JOIN symbols s ON s.code = d.code
                WHERE s.enabled = 1
                  AND COALESCE(s.role, 'trade_candidate') != 'excluded'
                ORDER BY d.code
                """
            ).fetchall()
        ]
    symbols_info = {str(row[0]): str(row[1]) for row in symbol_rows}
    logger.info("symbols=%d daily_bars_codes=%d", len(symbols_info), len(bar_codes))

    data_dict: dict[str, pd.DataFrame] = {}
    with sqlite3.connect(db_path) as conn:
        for code in bar_codes:
            frame = pd.read_sql_query(
                """
                SELECT code, date AS time_key, open, high, low, close, volume, turnover
                FROM daily_bars
                WHERE code = ?
                ORDER BY date
                """,
                conn,
                params=(code,),
            )
            if len(frame) >= min_history:
                data_dict[code] = frame

    logger.info("codes_with_history>=%d: %d", min_history, len(data_dict))
    indicators = calculate_indicators_batch(data_dict, symbols_info)
    indicators_df = indicators_to_dataframe(indicators)
    if indicators_df.empty:
        raise RuntimeError("指標を計算できませんでした")

    benchmark_code = config.get(
        "signals.relative_strength.benchmark_code",
        "JP.1306",
    )
    indicators_df = add_relative_strength(indicators_df, benchmark_code)
    saved_count = save_indicators_to_db(data_store, indicators_df)

    with sqlite3.connect(db_path) as conn:
        benchmark_codes = {
            str(row[0])
            for row in conn.execute(
                "SELECT code FROM symbols WHERE role = 'benchmark' AND enabled = 1"
            ).fetchall()
        }
    benchmark_count = save_benchmark_prices_from_indicators(
        data_store,
        indicators_df,
        benchmark_codes,
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT code), COUNT(*), MAX(date) FROM indicators"
        ).fetchone()
    result: dict[str, int | str | None] = {
        "eligible_codes": len(data_dict),
        "indicator_rows_saved": int(saved_count),
        "benchmark_rows_saved": int(benchmark_count),
        "indicator_codes": int(row[0] or 0),
        "indicator_rows": int(row[1] or 0),
        "latest_date": row[2],
    }
    logger.info("recalculation complete: %s", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="daily_barsからindicatorsを再計算")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--min-history", type=int, default=25)
    args = parser.parse_args()
    configure_logging()
    try:
        recalculate(args.config, min_history=args.min_history)
    except Exception:
        logger.exception("指標再計算に失敗しました")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
