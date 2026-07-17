from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from src.v2_validation import file_sha256, validate_database_migration


def write_config(tmp_path: Path, database: Path) -> Path:
    symbols = tmp_path / "symbols.json"
    symbols.write_text("[]", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "database": {"path": str(database)},
                "watchlist": {"symbols_file": str(symbols)},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return config


def create_database_with_foreign_key_violation(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys=OFF;
            CREATE TABLE parent_records (id INTEGER PRIMARY KEY);
            CREATE TABLE child_records (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL,
                FOREIGN KEY(parent_id) REFERENCES parent_records(id)
            );
            CREATE TABLE backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                initial_cash REAL NOT NULL DEFAULT 100000
            );
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                score REAL,
                reason TEXT,
                risk_warnings TEXT,
                price_at_signal REAL,
                created_at TEXT,
                UNIQUE(code, date)
            );
            INSERT INTO child_records(id, parent_id) VALUES (1, 999);
            """
        )


def test_migration_gate_rejects_foreign_key_violations_without_touching_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "invalid.db"
    create_database_with_foreign_key_violation(source)
    config = write_config(tmp_path, source)
    before_hash = file_sha256(source)

    report = validate_database_migration(
        source_database=source,
        config_path=config,
        output_directory=tmp_path / "validation",
    )

    assert report.status == "MIGRATION_FAILED"
    assert report.source_unchanged is True
    assert file_sha256(source) == before_hash
    assert report.foreign_key_violations
    assert any("foreign_key_check" in error for error in report.errors)
