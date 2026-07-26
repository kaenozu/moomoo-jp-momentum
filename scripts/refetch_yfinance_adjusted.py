"""
yfinanceデータをauto_adjust=Trueで再取得し、QFQ調整済みに統一する。

背景:
  yf_supplement.py が auto_adjust=False で取得したため、
  moomoo (QFQ調整済み) と yfinance (未調整) で価格基準が異なる。
  このスクリプトは全yfinanceデータをauto_adjust=Trueで再取得し、
  moomooと統一する。

使い方:
  uv run python scripts/refetch_yfinance_adjusted.py [--codes JP.2559,JP.2558] [--batch-size 50]
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Config
from src.data_store import DataStore
from src.yfinance_data import (
    to_yfinance_ticker,
    _exclusive_end,
    fetch_adjusted_history,
    upsert_yfinance_bars,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path("data/moomoo.db")
BATCH_SIZE = 50
SLEEP_SEC = 1.0


def get_all_yfinance_codes(db_path: Path) -> list[str]:
    """yfinanceデータを持つ全銘柄コードを取得（JP銘柄のみ）"""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT DISTINCT code FROM daily_bars WHERE source = 'yfinance' AND code LIKE 'JP.%' ORDER BY code"
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_date_range(db_path: Path) -> tuple[str, str]:
    """DBの日付範囲を取得"""
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT MIN(date), MAX(date) FROM daily_bars").fetchone()
    conn.close()
    return row[0], row[1]


def refetch_single(config: Config, code: str, start: str, end: str) -> int:
    """単一銘柄をauto_adjust=Trueで再取得"""
    try:
        result = fetch_adjusted_history(code, start, end)
        if result.bars.empty:
            logger.warning("  %s: データなし", code)
            return 0
        store = DataStore(config)
        stats = upsert_yfinance_bars(store.db_path, code, result.bars)
        if stats.written > 0:
            logger.info("  %s: %d件 (insert=%d, update=%d, preserved=%d)",
                        code, stats.written, stats.inserted, stats.updated, stats.preserved)
        return stats.written
    except Exception as e:
        logger.error("  %s: 取得失敗 - %s", code, e)
        return 0


def refetch_batch(config: Config, codes: list[str], start: str, end: str) -> int:
    """複数銘柄をバッチで再取得（yf.download使用）"""
    total = 0
    tickers = []
    valid_codes = []
    for code in codes:
        ticker = to_yfinance_ticker(code)
        if ticker:
            tickers.append(ticker)
            valid_codes.append(code)

    for i in range(0, len(tickers), BATCH_SIZE):
        batch_tickers = tickers[i:i + BATCH_SIZE]
        batch_codes = valid_codes[i:i + BATCH_SIZE]

        try:
            df = yf.download(
                batch_tickers,
                start=start,
                end=_exclusive_end(end),
                interval="1d",
                auto_adjust=True,
                actions=True,
                repair=False,
                progress=False,
                threads=True,
            )

            if df.empty:
                logger.warning("  バッチ%d: データなし (%s)", i // BATCH_SIZE + 1, batch_tickers)
                time.sleep(SLEEP_SEC)
                continue

            store = DataStore(config)

            for j, code in enumerate(batch_codes):
                ticker = batch_tickers[j]
                try:
                    ticker_df = df.xs(ticker, axis=1, level=1)

                    if ticker_df.empty:
                        continue

                    ticker_df = ticker_df.reset_index()
                    ticker_df["time_key"] = pd.to_datetime(ticker_df["Date"]).dt.strftime("%Y-%m-%d")

                    bars = ticker_df.rename(columns={
                        "Open": "open", "High": "high", "Low": "low",
                        "Close": "close", "Volume": "volume",
                    })[["time_key", "open", "high", "low", "close", "volume"]]

                    bars = bars.dropna(subset=["open", "high", "low", "close"])
                    bars["volume"] = bars["volume"].fillna(0).astype(int)
                    bars["turnover"] = bars["volume"] * bars["close"].astype(float)
                    bars["source"] = "yfinance"
                    bars["turnover_source"] = "estimated"

                    stats = upsert_yfinance_bars(store.db_path, code, bars)
                    total += stats.written
                    logger.info("  %s: %d件", code, stats.written)
                except Exception as e:
                    logger.error("  %s: 処理失敗 - %s", code, e)

        except Exception as e:
            logger.error("  バッチ%d: 取得失敗 - %s", i // BATCH_SIZE + 1, e)

        time.sleep(SLEEP_SEC)

    return total


def main():
    parser = argparse.ArgumentParser(description="yfinance再取得 (auto_adjust=True)")
    parser.add_argument("--db", default=str(DB_PATH), help="DBパス")
    parser.add_argument("--codes", type=str, help="カンマ区切りの銘柄コード（省略時は全yfinance銘柄）")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="バッチサイズ")
    parser.add_argument("--start", type=str, help="開始日 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="終了日 (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="実行せず件数のみ表示")
    parser.add_argument("--skip-delete", action="store_true", help="既存yfinanceデータを削除しない")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        logger.error("DBが見つかりません: %s", db_path)
        sys.exit(1)

    config = Config("config.yaml")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
    else:
        codes = get_all_yfinance_codes(db_path)

    if not codes:
        logger.info("yfinanceデータのある銘柄なし。終了。")
        return

    start, end = get_date_range(db_path)
    if args.start:
        start = args.start
    if args.end:
        end = args.end

    logger.info("対象銘柄: %d件", len(codes))
    logger.info("期間: %s ~ %s", start, end)

    if not args.dry_run:
        import shutil
        backup_path = db_path.with_suffix(".db.before-refetch")
        shutil.copy2(db_path, backup_path)
        logger.info("バックアップ作成: %s", backup_path)

        if not args.skip_delete:
            logger.info("既存yfinanceデータを削除中...")
            conn = sqlite3.connect(str(db_path))
            placeholders = ",".join("?" * len(codes))
            conn.execute(
                f"DELETE FROM daily_bars WHERE source = 'yfinance' AND code IN ({placeholders})",
                codes,
            )
            conn.commit()
            deleted = conn.total_changes
            conn.close()
            logger.info("削除完了: %d件", deleted)

    total = 0
    batch_size = args.batch_size

    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        logger.info("バッチ %d/%d (%d銘柄)", i // batch_size + 1, (len(codes) + batch_size - 1) // batch_size, len(batch))

        if args.dry_run:
            total += len(batch)
            logger.info("[dry-run] %s を再取得対象", ", ".join(batch))
        else:
            count = refetch_batch(config, batch, start, end)
            total += count

    if args.dry_run:
        logger.info("dry-run完了。合計 %d銘柄が再取得対象。", total)
    else:
        logger.info("再取得完了。合計 %d件を書き込み。", total)


if __name__ == "__main__":
    main()
