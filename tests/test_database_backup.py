from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from src.database_backup import (
    BackupError,
    BackupVerificationError,
    DatabaseBackupManager,
    quick_check,
)
from src.models import CREATE_TABLES_SQL


class StubConfig:
    def __init__(
        self,
        db_path: Path,
        backup_dir: Path,
        *,
        daily: int = 7,
        weekly: int = 4,
    ) -> None:
        self.database_path = str(db_path)
        self._values: dict[str, Any] = {
            "database_backup": {
                "enabled": True,
                "directory": str(backup_dir),
                "retain_daily": daily,
                "retain_weekly": weekly,
                "verify_after_backup": True,
            },
            "virtual_trade": {"initial_cash": 100000, "commission": 0},
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


def create_db(path: Path, *, wal: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(CREATE_TABLES_SQL)
    connection.execute("CREATE TABLE sample(value TEXT)")
    connection.execute("INSERT INTO sample(value) VALUES ('initial')")
    connection.commit()
    return connection


def test_wal_backup_and_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "source.db"
    writer = create_db(database_path, wal=True)
    writer.execute("INSERT INTO sample(value) VALUES ('from-wal')")
    writer.execute(
        "INSERT INTO virtual_fills "
        "(id, order_id, strategy_name, code, side, quantity, price, filled_at, "
        "fill_mode, commission) "
        "VALUES (1,1,'momentum','JP.0001','BUY',1,100,'2026-07-10','next',0)"
    )
    writer.execute(
        "INSERT INTO virtual_equity_curve "
        "(id, strategy_name, date, cash, position_value, total_equity) "
        "VALUES (1,'momentum','2026-07-11',100,0,100)"
    )
    writer.commit()

    manager = DatabaseBackupManager(
        StubConfig(database_path, tmp_path / "backups")
    )
    result = manager.create_backup(
        created_at=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )

    assert result.backup_path
    backup_path = Path(result.backup_path)
    assert quick_check(backup_path) == "ok"
    with sqlite3.connect(backup_path) as connection:
        values = [
            row[0]
            for row in connection.execute(
                "SELECT value FROM sample ORDER BY rowid"
            )
        ]
    assert values == ["initial", "from-wal"]
    assert result.metadata is not None
    assert result.metadata.latest_virtual_fill_date == "2026-07-10"
    assert result.metadata.latest_equity_curve_date == "2026-07-11"
    assert len(result.metadata.sha256) == 64
    writer.close()


def test_corrupt_backup_detected(tmp_path: Path) -> None:
    database_path = tmp_path / "source.db"
    create_db(database_path).close()
    manager = DatabaseBackupManager(
        StubConfig(database_path, tmp_path / "backups")
    )
    corrupt = tmp_path / "broken.sqlite3"
    corrupt.write_bytes(b"not sqlite")

    with pytest.raises(BackupVerificationError):
        manager.verify_backup(corrupt)


def test_prune_only_managed_generations(tmp_path: Path) -> None:
    database_path = tmp_path / "source.db"
    create_db(database_path).close()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    manager = DatabaseBackupManager(
        StubConfig(database_path, backup_dir, daily=2, weekly=1)
    )
    for index in range(4):
        path = backup_dir / (
            f"source-2026070{index + 1}T000000.000000Z-daily.sqlite3"
        )
        path.write_bytes(b"x")
        path.with_suffix(path.suffix + ".json").write_text("{}", encoding="utf-8")
    unrelated = backup_dir / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")

    deleted = manager.prune()

    assert len([path for path in deleted if path.suffix == ".sqlite3"]) == 2
    assert unrelated.exists()
    assert len(list(backup_dir.glob("*-daily.sqlite3"))) == 2


def test_dry_run_creates_no_files(tmp_path: Path) -> None:
    database_path = tmp_path / "source.db"
    create_db(database_path).close()
    backup_dir = tmp_path / "missing" / "backups"
    manager = DatabaseBackupManager(StubConfig(database_path, backup_dir))

    result = manager.create_backup(dry_run=True)

    assert result.dry_run
    assert not backup_dir.exists()


def test_restore_to_separate_path(tmp_path: Path) -> None:
    database_path = tmp_path / "source.db"
    create_db(database_path).close()
    manager = DatabaseBackupManager(
        StubConfig(database_path, tmp_path / "backups")
    )
    backup_path = Path(manager.create_backup().backup_path or "")
    destination = tmp_path / "restore" / "restored.db"

    result = manager.restore_backup(backup_path, destination)

    assert result.quick_check == "ok"
    assert destination.exists()
    assert quick_check(destination) == "ok"
    assert database_path.exists()


def test_restore_refuses_live_database_path(tmp_path: Path) -> None:
    database_path = tmp_path / "source.db"
    create_db(database_path).close()
    manager = DatabaseBackupManager(
        StubConfig(database_path, tmp_path / "backups")
    )
    backup_path = Path(manager.create_backup().backup_path or "")

    with pytest.raises(BackupError, match="同じパス"):
        manager.restore_backup(backup_path, database_path)


def test_checksum_mismatch_detected(tmp_path: Path) -> None:
    database_path = tmp_path / "source.db"
    create_db(database_path).close()
    manager = DatabaseBackupManager(
        StubConfig(database_path, tmp_path / "backups")
    )
    result = manager.create_backup()
    backup_path = Path(result.backup_path or "")
    metadata_path = Path(result.metadata_path or "")
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["sha256"] = "0" * 64
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BackupVerificationError):
        manager.verify_backup(backup_path)


def test_backup_does_not_modify_source_database(tmp_path: Path) -> None:
    database_path = tmp_path / "source.db"
    create_db(database_path).close()
    before = database_path.read_bytes()
    manager = DatabaseBackupManager(
        StubConfig(database_path, tmp_path / "backups")
    )

    manager.create_backup()

    assert database_path.read_bytes() == before
