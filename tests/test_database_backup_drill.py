from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import yaml

import database_backup
from src.models import CREATE_TABLES_SQL


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _create_empty_virtual_trade_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(CREATE_TABLES_SQL)
        connection.commit()


def _logical_snapshot(path: Path) -> dict[str, object]:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        quick = [row[0] for row in connection.execute("PRAGMA quick_check")]
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "virtual_orders",
                "virtual_fills",
                "virtual_positions",
                "virtual_equity_curve",
                "daily_bars",
            )
        }
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    return {
        "quick_check": quick,
        "tables": sorted(tables),
        "counts": counts,
        "user_version": user_version,
    }


def _run_cli(capsys, *args: str) -> tuple[int, str]:
    exit_code = database_backup.main(list(args))
    output = capsys.readouterr().out
    return exit_code, output


def test_isolated_backup_recovery_drill_end_to_end(tmp_path: Path, capsys) -> None:
    live_db = tmp_path / "live" / "moomoo.db"
    live_db.parent.mkdir()
    _create_empty_virtual_trade_db(live_db)

    primary_dir = tmp_path / "primary"
    secondary_dir = tmp_path / "secondary"
    restore_path = tmp_path / "restore" / "moomoo-restored.db"
    config_path = tmp_path / "drill-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "database": {"path": str(live_db)},
                "database_backup": {
                    "enabled": True,
                    "directory": str(primary_dir),
                    "retain_daily": 100,
                    "retain_weekly": 100,
                    "retain_pre_cycle": 100,
                    "retain_post_cycle": 100,
                    "verify_after_backup": True,
                },
                "virtual_trade": {
                    "enabled": True,
                    "initial_cash": 150000,
                    "commission": 0,
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    live_hash_before = _sha256(live_db)
    live_snapshot_before = _logical_snapshot(live_db)

    backup_exit, backup_output = _run_cli(
        capsys,
        "--config",
        str(config_path),
        "backup",
        "--kind",
        "daily",
    )
    assert backup_exit == 0, backup_output
    backup_result = json.loads(backup_output)
    assert backup_result["pruned_files"] == []

    backup_path = Path(backup_result["backup_path"])
    metadata_path = Path(backup_result["metadata_path"])
    assert backup_path.is_file()
    assert metadata_path.is_file()
    assert backup_path.parent == primary_dir

    verify_exit, verify_output = _run_cli(
        capsys,
        "--config",
        str(config_path),
        "verify",
        str(backup_path),
    )
    assert verify_exit == 0, verify_output
    assert json.loads(verify_output)["quick_check"] == "ok"

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["source_database_path"] == str(live_db.resolve())
    assert metadata["backup_path"] == str(backup_path.resolve())
    assert metadata["sha256"] == _sha256(backup_path)

    secondary_dir.mkdir()
    secondary_backup = secondary_dir / backup_path.name
    secondary_metadata = secondary_backup.with_suffix(secondary_backup.suffix + ".json")
    shutil.copy2(backup_path, secondary_backup)
    shutil.copy2(metadata_path, secondary_metadata)
    assert _sha256(secondary_backup) == _sha256(backup_path)
    assert _sha256(secondary_metadata) == _sha256(metadata_path)

    secondary_verify_exit, secondary_verify_output = _run_cli(
        capsys,
        "--config",
        str(config_path),
        "verify",
        str(secondary_backup),
    )
    assert secondary_verify_exit == 0, secondary_verify_output

    dry_run_exit, dry_run_output = _run_cli(
        capsys,
        "--config",
        str(config_path),
        "restore",
        str(secondary_backup),
        str(restore_path),
        "--strategy",
        "momentum",
        "--dry-run",
    )
    assert dry_run_exit == 0, dry_run_output
    dry_run_result = json.loads(dry_run_output)
    assert dry_run_result["dry_run"] is True
    assert dry_run_result["quick_check"] == "ok"
    assert dry_run_result["integrity_errors"] == 0
    assert not restore_path.exists()

    restore_exit, restore_output = _run_cli(
        capsys,
        "--config",
        str(config_path),
        "restore",
        str(secondary_backup),
        str(restore_path),
        "--strategy",
        "momentum",
    )
    assert restore_exit == 0, restore_output
    restore_result = json.loads(restore_output)
    assert restore_result["dry_run"] is False
    assert restore_result["quick_check"] == "ok"
    assert restore_result["integrity_errors"] == 0
    assert restore_path.is_file()

    assert _logical_snapshot(restore_path) == live_snapshot_before
    assert _logical_snapshot(live_db) == live_snapshot_before
    assert _sha256(live_db) == live_hash_before

    corrupt_dir = tmp_path / "corrupt"
    corrupt_dir.mkdir()
    corrupt_backup = corrupt_dir / secondary_backup.name
    corrupt_metadata = corrupt_backup.with_suffix(corrupt_backup.suffix + ".json")
    corrupt_restore_path = corrupt_dir / "must-not-exist.db"
    shutil.copy2(secondary_backup, corrupt_backup)
    shutil.copy2(secondary_metadata, corrupt_metadata)
    with corrupt_backup.open("ab") as handle:
        handle.write(b"\x00")

    corrupt_verify_exit, _ = _run_cli(
        capsys,
        "--config",
        str(config_path),
        "verify",
        str(corrupt_backup),
    )
    assert corrupt_verify_exit != 0

    corrupt_restore_exit, _ = _run_cli(
        capsys,
        "--config",
        str(config_path),
        "restore",
        str(corrupt_backup),
        str(corrupt_restore_path),
        "--strategy",
        "momentum",
        "--dry-run",
    )
    assert corrupt_restore_exit != 0
    assert not corrupt_restore_path.exists()
