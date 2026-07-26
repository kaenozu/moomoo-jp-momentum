"""
yfinance の未調整データを moomoo QFQ 基準に合わせて選択的に補正する。

背景:
  yfinance が株式分割を認識していない ETF などで、
  moomoo と yfinance の境界に価格不連続が発生する。
  このスクリプトは境界での価格比率を計算し、
  yfinance 行だけを一括調整して統一する。

使い方:
  uv run python scripts/apply_yfinance_boundary_adjustment.py [--codes JP.2559,JP.2558] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path("data/moomoo.db")
RATIO_THRESHOLD = 1.5  # この値以上（または1/この値以下）で不連続と判定


def detect_boundary_gaps(db_path: Path) -> list[dict]:
    """moomoo/yfinance 境界の不連続を検出する。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    codes = [
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT code FROM daily_bars
            WHERE source = 'moomoo'
              AND code IN (
                  SELECT code FROM daily_bars WHERE source = 'yfinance' AND code LIKE 'JP.%'
              )
            ORDER BY code
            """
        ).fetchall()
    ]

    gaps = []
    for code in codes:
        first_moomoo = conn.execute(
            "SELECT MIN(date), close FROM daily_bars WHERE code = ? AND source = 'moomoo'",
            (code,),
        ).fetchone()

        if not first_moomoo or not first_moomoo[0]:
            continue

        prev_y = conn.execute(
            """
            SELECT date, close FROM daily_bars
            WHERE code = ? AND source = 'yfinance' AND date < ?
            ORDER BY date DESC LIMIT 1
            """,
            (code, first_moomoo[0]),
        ).fetchone()

        if prev_y and prev_y[1] and first_moomoo[1]:
            ratio = first_moomoo[1] / prev_y[1]
            if ratio > RATIO_THRESHOLD or ratio < 1.0 / RATIO_THRESHOLD:
                gaps.append(
                    {
                        "code": code,
                        "yfinance_date": prev_y[0],
                        "yfinance_close": prev_y[1],
                        "moomoo_date": first_moomoo[0],
                        "moomoo_close": first_moomoo[1],
                        "ratio": ratio,
                        "adjustment_factor": first_moomoo[1] / prev_y[1],
                    }
                )

    conn.close()
    return gaps


def apply_adjustment(db_path: Path, code: str, factor: float, dry_run: bool = False) -> int:
    """yfinance 行の価格を一括調整する。

    Args:
        factor: yfinance_close * factor = moomoo_close となる係数
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT id, open, high, low, close, volume, turnover
        FROM daily_bars
        WHERE code = ? AND source = 'yfinance'
        """,
        (code,),
    ).fetchall()

    if not rows:
        conn.close()
        return 0

    updates = []
    for row in rows:
        new_open = row["open"] * factor if row["open"] is not None else None
        new_high = row["high"] * factor if row["high"] is not None else None
        new_low = row["low"] * factor if row["low"] is not None else None
        new_close = row["close"] * factor if row["close"] is not None else None
        new_volume = int(round(row["volume"] / factor)) if row["volume"] is not None else 0
        new_turnover = (new_close * new_volume) if new_close is not None and new_volume is not None else None

        updates.append(
            (new_open, new_high, new_low, new_close, new_volume, new_turnover, row["id"])
        )

    if not dry_run:
        conn.executemany(
            """
            UPDATE daily_bars
            SET open = ?, high = ?, low = ?, close = ?, volume = ?, turnover = ?
            WHERE id = ?
            """,
            updates,
        )
        conn.commit()

    conn.close()
    return len(updates)


def record_adjustment(db_path: Path, code: str, factor: float, note: str = "") -> None:
    """調整履歴を yfinance_source_adjustments テーブルに記録する。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS yfinance_source_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            adjustment_factor REAL NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            note TEXT,
            UNIQUE(code)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO yfinance_source_adjustments (code, adjustment_factor, note)
        VALUES (?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            adjustment_factor = excluded.adjustment_factor,
            applied_at = datetime('now', 'localtime'),
            note = excluded.note
        """,
        (code, factor, note),
    )
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="yfinance境界補正")
    parser.add_argument("--db", default=str(DB_PATH), help="DBパス")
    parser.add_argument("--codes", type=str, help="カンマ区切りの銘柄コード（省略時は全不連続銘柄）")
    parser.add_argument("--dry-run", action="store_true", help="実行せず候補のみ表示")
    parser.add_argument("--threshold", type=float, default=RATIO_THRESHOLD, help="不連続判定閾値")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        logger.error("DBが見つかりません: %s", db_path)
        sys.exit(1)

    gaps = detect_boundary_gaps(db_path)
    logger.info("不連続銘柄: %d件", len(gaps))

    if args.codes:
        target_codes = [c.strip() for c in args.codes.split(",")]
        gaps = [g for g in gaps if g["code"] in target_codes]
        logger.info("対象銘柄: %s", target_codes)

    for g in gaps:
        logger.info(
            "  %s: yfinance(%s)=%.2f -> moomoo(%s)=%.2f, ratio=%.4f, factor=%.4f",
            g["code"],
            g["yfinance_date"],
            g["yfinance_close"],
            g["moomoo_date"],
            g["moomoo_close"],
            g["ratio"],
            g["adjustment_factor"],
        )

    if not gaps:
        logger.info("補正対象なし。終了。")
        return

    if not args.dry_run:
        import shutil

        backup_path = db_path.with_suffix(".db.before-boundary-adjust")
        shutil.copy2(db_path, backup_path)
        logger.info("バックアップ作成: %s", backup_path)

    total = 0
    for g in gaps:
        code = g["code"]
        factor = g["adjustment_factor"]

        if args.dry_run:
            logger.info("[dry-run] %s: factor=%.6f で調整", code, factor)
            total += 1
        else:
            count = apply_adjustment(db_path, code, factor)
            record_adjustment(db_path, code, factor, note=f"boundary ratio={g['ratio']:.4f}")
            logger.info("%s: %d行を調整 (factor=%.6f)", code, count, factor)
            total += count

    if args.dry_run:
        logger.info("dry-run完了。%d銘柄が調整対象。", total)
    else:
        logger.info("調整完了。%d銘柄を処理。", total)


if __name__ == "__main__":
    main()
