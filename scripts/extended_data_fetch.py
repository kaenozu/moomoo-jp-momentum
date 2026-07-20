"""2022-01-01以降の日本株日足をyfinanceで補完する。

- 無効・既知の上場廃止銘柄を除外
- yfinance ``auto_adjust=True`` で分割調整済みOHLCを取得
- 分割イベントを corporate_actions に記録
- moomoo行を上書きせず、yfinance行のみ更新
- 完了後に scripts/recalc_indicators.py を実行
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Config, load_config
from src.data_store import DataStore
from src.yfinance_data import (
    fetch_adjusted_history,
    is_stale_unavailable,
    record_splits,
    upsert_yfinance_bars,
)

JST = ZoneInfo("Asia/Tokyo")
DELISTED_KEYWORDS = ("上場廃止", "delisted", "listing terminated", "上場終了")
logger = logging.getLogger(__name__)


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def config_hash(config_path: str | Path) -> str:
    return hashlib.sha256(Path(config_path).read_bytes()).hexdigest()


def git_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def is_known_delisted(symbol: sqlite3.Row | dict[str, Any]) -> bool:
    """symbolsメタデータから既知の上場廃止・除外銘柄を判定する。"""
    enabled = bool(symbol["enabled"])
    role = str(symbol["role"] or "trade_candidate")
    notes = str(symbol["notes"] or "").lower()
    if not enabled or role == "excluded":
        return True
    return any(keyword.lower() in notes for keyword in DELISTED_KEYWORDS)


def load_fetch_symbols(config: Config) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """取得対象と除外対象を返す。"""
    with sqlite3.connect(config.database_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT code, name, market, role, tradable, enabled, notes
            FROM symbols
            WHERE UPPER(market) = 'JP'
            ORDER BY code
            """
        ).fetchall()

    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if is_known_delisted(row):
            excluded.append(item)
        else:
            eligible.append(item)
    return eligible, excluded


def database_range(db_path: str | Path) -> dict[str, Any]:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT code), COUNT(*), MIN(date), MAX(date)
            FROM daily_bars
            """
        ).fetchone()
    return {
        "code_count": int(row[0] or 0),
        "row_count": int(row[1] or 0),
        "start_date": row[2],
        "end_date": row[3],
    }


def run_recalculation(config_path: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "recalc_indicators.py"),
        "--config",
        str(Path(config_path).resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout_lines = completed.stdout.splitlines()
    stderr_lines = completed.stderr.splitlines()
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": stdout_lines[-40:],
        "stderr_tail": stderr_lines[-40:],
    }


def report_path(config: Config) -> Path:
    root = Path(str(config.get("report.output_dir", "reports")))
    timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    return root / "data_fetch" / f"extended_data_fetch_{timestamp}.json"


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def fetch_extended_data(
    *,
    config_path: str,
    start_date: str,
    end_date: str,
    max_retries: int,
    sleep_seconds: float,
    limit: int | None,
    recalc: bool,
) -> dict[str, Any]:
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")
    if start_date > end_date:
        raise ValueError("start_dateはend_date以前にしてください")
    if max_retries < 0:
        raise ValueError("max_retriesは0以上にしてください")
    if sleep_seconds < 0:
        raise ValueError("sleep_secondsは0以上にしてください")

    config = load_config(config_path)
    data_store = DataStore(config)
    data_store.sync_symbols_from_json(config.watchlist_file)
    eligible, excluded = load_fetch_symbols(config)
    if limit is not None:
        eligible = eligible[:limit]

    output = report_path(config)
    report: dict[str, Any] = {
        "status": "running",
        "started_at": datetime.now(JST).isoformat(timespec="seconds"),
        "config_hash": config_hash(config_path),
        "git_sha": git_sha(),
        "requested_period": {"start": start_date, "end": end_date},
        "requested_codes": len(eligible),
        "excluded_codes": [
            {
                "code": item["code"],
                "name": item["name"],
                "role": item["role"],
                "notes": item["notes"],
            }
            for item in excluded
        ],
        "results": [],
        "database_before": database_range(config.database_path),
    }

    success_count = 0
    error_count = 0
    unavailable_count = 0
    rows_written = 0
    split_count = 0

    try:
        for index, symbol in enumerate(eligible, 1):
            code = str(symbol["code"])
            logger.info("[%d/%d] %s %s", index, len(eligible), code, symbol["name"])
            last_error: Exception | None = None
            fetched = None
            for attempt in range(max_retries + 1):
                try:
                    fetched = fetch_adjusted_history(code, start_date, end_date)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "取得失敗: %s attempt=%d/%d - %s",
                        code,
                        attempt + 1,
                        max_retries + 1,
                        exc,
                    )
                    if attempt < max_retries:
                        time.sleep(max(sleep_seconds, 0.5) * (attempt + 1))

            if fetched is None or last_error is not None:
                error_count += 1
                report["results"].append(
                    {
                        "code": code,
                        "name": symbol["name"],
                        "status": "error",
                        "error": str(last_error),
                    }
                )
                continue

            stale = is_stale_unavailable(fetched.bars, end_date=end_date)
            if fetched.bars.empty or stale:
                unavailable_count += 1
                report["results"].append(
                    {
                        "code": code,
                        "name": symbol["name"],
                        "status": "possibly_delisted_or_unavailable",
                        "rows": int(len(fetched.bars)),
                        "latest_date": (
                            str(fetched.bars["time_key"].max())[:10]
                            if not fetched.bars.empty
                            else None
                        ),
                    }
                )
                continue

            stats = upsert_yfinance_bars(config.database_path, code, fetched.bars)
            recorded_splits = record_splits(
                config.database_path,
                code,
                fetched.splits,
            )
            success_count += 1
            rows_written += stats.written
            split_count += recorded_splits
            report["results"].append(
                {
                    "code": code,
                    "name": symbol["name"],
                    "status": "completed",
                    "fetched_rows": int(len(fetched.bars)),
                    "first_date": str(fetched.bars["time_key"].min())[:10],
                    "last_date": str(fetched.bars["time_key"].max())[:10],
                    "upsert": asdict(stats),
                    "split_events": recorded_splits,
                }
            )
            if sleep_seconds:
                time.sleep(sleep_seconds)

        report["summary"] = {
            "requested_codes": len(eligible),
            "fetched_codes": success_count,
            "error_codes": error_count,
            "possibly_delisted_or_unavailable_codes": unavailable_count,
            "known_delisted_or_excluded_codes": len(excluded),
            "rows_written": rows_written,
            "split_events": split_count,
            "period": {"start": start_date, "end": end_date},
        }
        report["database_after_fetch"] = database_range(config.database_path)

        if recalc:
            report["recalculation"] = run_recalculation(config_path)
            if report["recalculation"]["returncode"] != 0:
                raise RuntimeError("scripts/recalc_indicators.py が失敗しました")
        else:
            report["recalculation"] = {"skipped": True}

        report["database_after_recalc"] = database_range(config.database_path)
        report["status"] = "completed"
        report["completed_at"] = datetime.now(JST).isoformat(timespec="seconds")
        write_report(output, report)
        report["report_path"] = str(output)
        return report
    except Exception as exc:
        report["status"] = "failed"
        report["completed_at"] = datetime.now(JST).isoformat(timespec="seconds")
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        write_report(output, report)
        report["report_path"] = str(output)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="yfinance長期日足取得")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument(
        "--end",
        default=datetime.now(JST).strftime("%Y-%m-%d"),
    )
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--no-recalc",
        action="store_true",
        help="検証用。通常運用では指定しない",
    )
    args = parser.parse_args()
    configure_logging()

    try:
        report = fetch_extended_data(
            config_path=args.config,
            start_date=args.start,
            end_date=args.end,
            max_retries=args.max_retries,
            sleep_seconds=args.sleep_seconds,
            limit=args.limit,
            recalc=not args.no_recalc,
        )
    except Exception:
        logger.exception("期間延長データ取得に失敗しました")
        return 1

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report: {report['report_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
