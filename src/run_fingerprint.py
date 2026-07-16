"""Reproducibility metadata for backtest runs."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import Config
from .execution_engine import EXECUTION_ENGINE_VERSION


@dataclass(frozen=True)
class BacktestRunMetadata:
    market: str
    git_commit: str | None
    config_hash: str
    universe_hash: str
    data_snapshot_hash: str
    data_max_date: str | None
    engine_version: str
    adjustment_policy: str


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _hash_rows(rows: Iterable[sqlite3.Row]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        payload = [row[key] for key in row.keys()]
        digest.update(_canonical_json(payload))
        digest.update(b"\n")
    return digest.hexdigest()


def resolve_git_commit(repository_root: Path | None = None) -> str | None:
    for env_name in ("GIT_COMMIT", "GITHUB_SHA"):
        value = os.environ.get(env_name)
        if value:
            return value.strip()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def ensure_backtest_run_metadata_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)")}
    definitions = {
        "market": "TEXT NOT NULL DEFAULT 'JP'",
        "git_commit": "TEXT",
        "config_hash": "TEXT NOT NULL DEFAULT ''",
        "universe_hash": "TEXT NOT NULL DEFAULT ''",
        "data_snapshot_hash": "TEXT NOT NULL DEFAULT ''",
        "data_max_date": "TEXT",
        "engine_version": "TEXT NOT NULL DEFAULT 'legacy'",
        "adjustment_policy": "TEXT NOT NULL DEFAULT 'qfq_no_additional_adjustment'",
    }
    for name, definition in definitions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE backtest_runs ADD COLUMN {name} {definition}")


def collect_backtest_run_metadata(
    conn: sqlite3.Connection,
    config: Config,
    start_date: str,
    end_date: str,
    market: str,
) -> BacktestRunMetadata:
    normalized_market = market.upper()
    config_path = Path(config.config_path)
    config_hash = _sha256_bytes(config_path.read_bytes())

    universe_rows = conn.execute(
        """
        SELECT code, name, market, type, role, tradable, benchmark_group, enabled
        FROM symbols
        WHERE enabled=1 AND UPPER(COALESCE(market, 'JP'))=?
        ORDER BY code
        """,
        (normalized_market,),
    )
    universe_hash = _hash_rows(universe_rows)

    data_rows = conn.execute(
        """
        SELECT b.code, b.date, b.open, b.high, b.low, b.close, b.volume,
               b.turnover, b.source, b.turnover_source
        FROM daily_bars AS b
        JOIN symbols AS s ON s.code=b.code
        WHERE s.enabled=1
          AND UPPER(COALESCE(s.market, 'JP'))=?
          AND b.date>=? AND b.date<=?
        ORDER BY b.code, b.date
        """,
        (normalized_market, start_date, end_date),
    )
    data_snapshot_hash = _hash_rows(data_rows)
    max_date_row = conn.execute(
        """
        SELECT MAX(b.date)
        FROM daily_bars AS b
        JOIN symbols AS s ON s.code=b.code
        WHERE s.enabled=1
          AND UPPER(COALESCE(s.market, 'JP'))=?
          AND b.date>=? AND b.date<=?
        """,
        (normalized_market, start_date, end_date),
    ).fetchone()
    data_max_date = str(max_date_row[0]) if max_date_row and max_date_row[0] else None

    repository_root = config_path.resolve().parent
    while repository_root.parent != repository_root and not (repository_root / ".git").exists():
        repository_root = repository_root.parent
    git_commit = resolve_git_commit(
        repository_root if (repository_root / ".git").exists() else None
    )
    adjustment_policy = str(
        config.get("backtest.adjustment_policy", "qfq_no_additional_adjustment")
    )
    return BacktestRunMetadata(
        market=normalized_market,
        git_commit=git_commit,
        config_hash=config_hash,
        universe_hash=universe_hash,
        data_snapshot_hash=data_snapshot_hash,
        data_max_date=data_max_date,
        engine_version=EXECUTION_ENGINE_VERSION,
        adjustment_policy=adjustment_policy,
    )
