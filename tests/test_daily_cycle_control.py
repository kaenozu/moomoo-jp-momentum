from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import run_daily_cycle
from src.database_backup import BackupError, DatabaseBackupManager
from src.models import CREATE_TABLES_SQL


class StubConfig:
    def __init__(
        self,
        config_path: Path,
        database_path: Path,
        lock_directory: Path,
        backup_directory: Path,
        *,
        backup_enabled: bool = False,
        control_enabled: bool = True,
    ) -> None:
        self.config_path = config_path
        self.database_path = str(database_path)
        self.watchlist_file = "unused.json"
        self._values: dict[str, Any] = {
            "cycle_control.enabled": control_enabled,
            "cycle_control": {
                "enabled": control_enabled,
                "lock_directory": str(lock_directory),
                "stale_after_seconds": 3600,
            },
            "database_backup": {
                "enabled": backup_enabled,
                "directory": str(backup_directory),
                "retain_daily": 2,
                "retain_weekly": 1,
                "retain_pre_cycle": 2,
                "retain_post_cycle": 2,
                "verify_after_backup": True,
            },
        }

    def get(self, key_path: str, default: Any = None) -> Any:
        return self._values.get(key_path, default)


def configure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    backup_enabled: bool = False,
    control_enabled: bool = True,
) -> StubConfig:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("test: true\n", encoding="utf-8")
    config = StubConfig(
        config_path,
        tmp_path / "cycle.db",
        tmp_path / "locks",
        tmp_path / "backups",
        backup_enabled=backup_enabled,
        control_enabled=control_enabled,
    )
    monkeypatch.setattr(run_daily_cycle, "load_config", lambda _path: config)
    monkeypatch.setattr(
        run_daily_cycle,
        "get_jpx_calendar",
        lambda: SimpleNamespace(is_trading_day=lambda _date: True),
    )
    monkeypatch.setattr(
        run_daily_cycle,
        "_run_cycle_core",
        lambda *_args, **_kwargs: {
            "calendar_checked": True,
            "is_trading_day": True,
            "cycle_skipped": False,
            "signals": 2,
            "fills": 1,
            "alerts": 0,
        },
    )
    return config


def read_run_rows(database_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT status, force_rerun, rerun_reason, result_json, "
            "error_type, error_message FROM cycle_runs ORDER BY id"
        ).fetchall()


def test_controlled_cycle_records_success_and_releases_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = configure(monkeypatch, tmp_path)

    result = run_daily_cycle.run_cycle("2026-07-13", config_path="ignored.yaml")

    assert result["cycle_lock_acquired"] is True
    assert result["cycle_run_id"] == 1
    assert not (tmp_path / "locks" / "daily-cycle-2026-07-13.lock").exists()
    rows = read_run_rows(Path(config.database_path))
    assert rows[0]["status"] == "SUCCEEDED"
    with sqlite3.connect(config.database_path) as connection:
        stages = connection.execute(
            "SELECT stage_name, status FROM cycle_run_stages ORDER BY id"
        ).fetchall()
    assert stages == [
        ("pre_cycle_backup", "SUCCEEDED"),
        ("daily_pipeline", "SUCCEEDED"),
    ]


def test_same_date_requires_force_rerun_with_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = configure(monkeypatch, tmp_path)
    run_daily_cycle.run_cycle("2026-07-13", config_path="ignored.yaml")

    with pytest.raises(run_daily_cycle.DailyCycleStoppedError) as caught:
        run_daily_cycle.run_cycle("2026-07-13", config_path="ignored.yaml")
    assert caught.value.event_type == "cycle_concurrency_failure"

    result = run_daily_cycle.run_cycle(
        "2026-07-13",
        config_path="ignored.yaml",
        force_rerun=True,
        rerun_reason="corrected source data",
    )

    assert result["cycle_run_id"] == 2
    rows = read_run_rows(Path(config.database_path))
    assert rows[1]["force_rerun"] == 1
    assert rows[1]["rerun_reason"] == "corrected source data"


def test_duplicate_date_is_rejected_before_new_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = configure(monkeypatch, tmp_path, backup_enabled=True)
    with sqlite3.connect(config.database_path) as connection:
        connection.executescript(CREATE_TABLES_SQL)
    run_daily_cycle.run_cycle("2026-07-13", config_path="ignored.yaml")
    backup_dir = tmp_path / "backups"
    before = sorted(path.name for path in backup_dir.iterdir())

    with pytest.raises(run_daily_cycle.DailyCycleStoppedError):
        run_daily_cycle.run_cycle("2026-07-13", config_path="ignored.yaml")

    assert sorted(path.name for path in backup_dir.iterdir()) == before


def test_backup_only_mode_runs_duplicate_preflight_before_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = configure(monkeypatch, tmp_path, backup_enabled=True)
    with sqlite3.connect(config.database_path) as connection:
        connection.executescript(CREATE_TABLES_SQL)
    run_daily_cycle.run_cycle("2026-07-13", config_path="ignored.yaml")
    backup_dir = tmp_path / "backups"
    before = sorted(path.name for path in backup_dir.iterdir())
    config._values["cycle_control.enabled"] = False
    cycle_control = config._values["cycle_control"]
    assert isinstance(cycle_control, dict)
    cycle_control["enabled"] = False

    with pytest.raises(run_daily_cycle.DailyCycleStoppedError) as caught:
        run_daily_cycle.run_cycle("2026-07-13", config_path="ignored.yaml")

    assert caught.value.event_type == "cycle_concurrency_failure"
    assert sorted(path.name for path in backup_dir.iterdir()) == before


def test_stage_setup_failure_marks_run_failed_and_releases_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = configure(monkeypatch, tmp_path)

    def fail_start_stage(
        self: run_daily_cycle.CycleRunLedger,
        _stage_name: str,
        _details: dict[str, Any] | None = None,
    ) -> int:
        raise RuntimeError("stage setup failed")

    monkeypatch.setattr(
        run_daily_cycle.CycleRunLedger,
        "start_stage",
        fail_start_stage,
    )

    with pytest.raises(RuntimeError, match="stage setup failed"):
        run_daily_cycle.run_cycle("2026-07-13", config_path="ignored.yaml")

    rows = read_run_rows(Path(config.database_path))
    assert rows[0]["status"] == "FAILED"
    assert rows[0]["error_type"] == "RuntimeError"
    assert rows[0]["error_message"] == "stage setup failed"
    assert not (tmp_path / "locks" / "daily-cycle-2026-07-13.lock").exists()


def test_missing_source_records_pre_cycle_skip_detail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = configure(monkeypatch, tmp_path, backup_enabled=True)

    run_daily_cycle.run_cycle("2026-07-13", config_path="ignored.yaml")

    with sqlite3.connect(config.database_path) as connection:
        row = connection.execute(
            "SELECT details_json FROM cycle_run_stages "
            "WHERE stage_name = 'pre_cycle_backup'"
        ).fetchone()
    assert row is not None
    assert json.loads(row[0]) == {"skipped": "source_database_missing"}


def test_pre_and_post_backups_are_created(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = configure(monkeypatch, tmp_path, backup_enabled=True)
    with sqlite3.connect(config.database_path) as connection:
        connection.executescript(CREATE_TABLES_SQL)

    result = run_daily_cycle.run_cycle("2026-07-13", config_path="ignored.yaml")

    assert result["pre_cycle_backup"]
    assert result["post_cycle_backup"]
    assert len(list((tmp_path / "backups").glob("*-pre_cycle.sqlite3"))) == 1
    assert len(list((tmp_path / "backups").glob("*-post_cycle.sqlite3"))) == 1
    with sqlite3.connect(config.database_path) as connection:
        stages = connection.execute(
            "SELECT stage_name, status FROM cycle_run_stages ORDER BY id"
        ).fetchall()
    assert stages[-1] == ("post_cycle_backup", "SUCCEEDED")


@pytest.mark.parametrize(
    "failure",
    [BackupError("disk unavailable"), OSError("storage I/O failure")],
)
def test_backup_failure_has_stable_operational_classification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: Exception,
) -> None:
    config = configure(monkeypatch, tmp_path, backup_enabled=True)
    with sqlite3.connect(config.database_path) as connection:
        connection.executescript(CREATE_TABLES_SQL)

    def fail_backup(
        self: DatabaseBackupManager,
        **_kwargs: Any,
    ) -> Any:
        raise failure

    monkeypatch.setattr(DatabaseBackupManager, "create_backup", fail_backup)

    with pytest.raises(run_daily_cycle.DailyCycleStoppedError) as caught:
        run_daily_cycle.run_cycle("2026-07-13", config_path="ignored.yaml")

    assert caught.value.event_type == "database_backup_failure"
    assert caught.value.context == {"backup_kind": "pre_cycle"}
    assert not (tmp_path / "locks" / "daily-cycle-2026-07-13.lock").exists()


def test_dry_run_bypasses_lock_ledger_and_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = configure(monkeypatch, tmp_path, backup_enabled=True)

    result = run_daily_cycle.run_cycle(
        "2026-07-13",
        dry_run=True,
        config_path="ignored.yaml",
    )

    assert result["calendar_checked"] is True
    assert not Path(config.database_path).exists()
    assert not (tmp_path / "locks").exists()
    assert not (tmp_path / "backups").exists()
