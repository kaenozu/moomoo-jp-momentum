"""
Recalculate indicators from daily_bars for one target date.

This keeps cross-sectional stats date-consistent: volume percentile/rank are
computed only among symbols that have a bar on the target date.
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from daily_update import add_relative_strength, save_benchmark_prices_from_indicators, save_indicators_to_db
from src.config import load_config
from src.data_store import DataStore
from src.indicators import calculate_indicators_batch, indicators_to_dataframe

logger = logging.getLogger(__name__)


def _latest_bar_date(db_path: str) -> str | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT MAX(date) FROM daily_bars").fetchone()
    return row[0] if row and row[0] else None


def _codes_with_bar_on_date(db_path: str, target_date: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT d.code
            FROM daily_bars d
            JOIN symbols s ON s.code = d.code
            WHERE d.date = ?
              AND COALESCE(s.enabled, 1) = 1
            ORDER BY d.code
            """,
            (target_date,),
        ).fetchall()
    return [r[0] for r in rows]


def _symbols_info(db_path: str) -> tuple[dict[str, str], set[str]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT code, name, role FROM symbols WHERE COALESCE(enabled, 1) = 1"
        ).fetchall()
    names = {r[0]: r[1] for r in rows}
    benchmarks = {r[0] for r in rows if r[2] == "benchmark"}
    return names, benchmarks


def _bars_up_to_date(db_path: str, code: str, target_date: str, limit: int) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(
            """
            SELECT code, date AS time_key, open, high, low, close, volume, turnover
            FROM daily_bars
            WHERE code = ? AND date <= ?
            ORDER BY date DESC
            LIMIT ?
            """,
            conn,
            params=[code, target_date, limit],
        )


def recalculate_for_date(
    config_path: str,
    target_date: str | None,
    min_history: int,
    limit: int,
) -> int:
    config = load_config(config_path)
    data_store = DataStore(config)
    db_path = config.database_path
    target = target_date or _latest_bar_date(db_path)
    if not target:
        raise RuntimeError("daily_bars にデータがありません")

    names, benchmark_codes = _symbols_info(db_path)
    codes = _codes_with_bar_on_date(db_path, target)
    logger.info("target_date=%s codes_with_bar=%d", target, len(codes))

    data_dict: dict[str, pd.DataFrame] = {}
    skipped_short = 0
    for code in codes:
        df = _bars_up_to_date(db_path, code, target, limit)
        if len(df) < min_history:
            skipped_short += 1
            continue
        data_dict[code] = df

    logger.info("codes_with_history=%d skipped_short=%d", len(data_dict), skipped_short)
    indicators = calculate_indicators_batch(data_dict, names)
    indicators_df = indicators_to_dataframe(indicators)
    if indicators_df.empty:
        raise RuntimeError("指標計算結果が空です")

    benchmark_code = config.get("signals.relative_strength.benchmark_code", "JP.1306")
    indicators_df = add_relative_strength(indicators_df, benchmark_code)
    saved = save_indicators_to_db(data_store, indicators_df)
    bench_saved = save_benchmark_prices_from_indicators(data_store, indicators_df, benchmark_codes)

    logger.info("saved indicators=%d benchmark_prices=%d", saved, bench_saved)
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="daily_bars から指定日付の indicators を再計算します")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--date", default=None, help="対象日付 YYYY-MM-DD。省略時はdaily_barsの最新日")
    parser.add_argument("--min-history", type=int, default=25)
    parser.add_argument("--limit", type=int, default=250)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    saved = recalculate_for_date(args.config, args.date, args.min_history, args.limit)
    print(f"[OK] indicators recalculated: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
