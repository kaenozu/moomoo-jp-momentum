"""
Recalculate indicators from daily_bars for all codes with sufficient history.

Uses existing calculate_indicators_batch which calls add_cross_sectional_stats.
"""
import logging
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path("C:/gemini-desktop/moomoo").resolve()))

from src.indicators import calculate_indicators_batch, indicators_to_dataframe
from src.data_store import DataStore
from src.config import load_config
from src.indicators import add_relative_strength

DB_PATH = "data/moomoo.db"
MIN_HISTORY = 25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    config = load_config("config.yaml")
    data_store = DataStore(config)
    db_path = Path(DB_PATH)

    # Get all symbols with their names
    with sqlite3.connect(db_path) as conn:
        sym_rows = conn.execute(
            "SELECT code, name FROM symbols WHERE enabled=1"
        ).fetchall()
    symbols_info = {r[0]: r[1] for r in sym_rows}
    logger.info("symbols: %d enabled codes", len(symbols_info))

    # Get all codes in daily_bars
    with sqlite3.connect(db_path) as conn:
        bar_codes = [
            r[0] for r in conn.execute("SELECT DISTINCT code FROM daily_bars").fetchall()
        ]
    logger.info("daily_bars codes: %d", len(bar_codes))

    # For each code, read daily_bars sorted by date
    data_dict = {}
    for code in bar_codes:
        with sqlite3.connect(db_path) as conn:
            df = pd.read_sql_query(
                "SELECT code, date as time_key, open, high, low, close, volume, turnover "
                "FROM daily_bars WHERE code = ? ORDER BY date",
                conn, params=(code,),
            )
        if len(df) < MIN_HISTORY:
            logger.debug("  skip %s: %d rows (< %d)", code, len(df), MIN_HISTORY)
            continue
        data_dict[code] = df

    logger.info("codes with >= %d days: %d", MIN_HISTORY, len(data_dict))

    # Calculate indicators (includes cross-sectional stats internally)
    indicators = calculate_indicators_batch(data_dict, symbols_info)
    logger.info("indicators computed: %d", len(indicators))

    # Convert to DataFrame
    df = indicators_to_dataframe(indicators)
    logger.info("indicators DataFrame: %d rows", len(df))

    if df.empty:
        logger.error("No indicators computed!")
        return

    # Add relative strength vs benchmark
    benchmark_code = config.get("signals.relative_strength.benchmark_code", "JP.1306")
    df = add_relative_strength(df, benchmark_code)
    logger.info("relative strength added (benchmark=%s)", benchmark_code)

    # Save to indicators table using the shared helper
    from daily_update import save_indicators_to_db, save_benchmark_prices_from_indicators
    saved_count = save_indicators_to_db(data_store, df)
    benchmark_code_set = set()
    with sqlite3.connect(db_path) as conn:
        for r in conn.execute("SELECT code FROM symbols WHERE role='benchmark'").fetchall():
            benchmark_code_set.add(r[0])
    bench_count = save_benchmark_prices_from_indicators(data_store, df, benchmark_code_set)
    logger.info("saved indicators=%d benchmark_prices=%d", saved_count, bench_count)

    # Report
    with sqlite3.connect(db_path) as conn:
        cnt = conn.execute("SELECT COUNT(DISTINCT code) FROM indicators").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]
        latest = conn.execute("SELECT MAX(date) FROM indicators").fetchone()[0]
    logger.info("indicators table: %d unique codes, %d rows, latest date=%s", cnt, total, latest)


if __name__ == "__main__":
    main()
