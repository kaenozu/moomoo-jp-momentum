from __future__ import annotations

import json
import os
import sqlite3
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import src.cycle_run as cycle_run
from src.cycle_run import (
    CycleAlreadyRunningError,
    CycleControlError,
    CycleControlSettings,
    CycleFileLock,
    CycleRerunRequiredError,
    CycleRunLedger,
    config_fingerprint,
)


class StubConfig:
    def __init__(self, config_path: Path, database_path: Path) -> None:
        self.config_path = config_path
        self.database_path = str(database_path)
        self._values: dict[str, Any] = {}

    def get(self, key_path: str, default: Any = None) -> Any:
        return self._values.get(key_path, default)


def read_rows(database_path: Path, query: str) -> list[sqlite3.Row]:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(query).fetchall()


def test_windows_pid_check_never_calls_os_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cycle_run, "_IS_WINDOWS", True)
    monkeypatch.setattr(cycle_run, "_windows_pid_is_alive", lambda pid: pid == 42)

    def forbidden_kill(pid: int, signal_number: int) -> None:
        raise AssertionError(f"os.kill must not be called: {pid=} {signal_number=}")

    monkeypatch.setattr(cycle_run.os, "kill", forbidden_kill)

    assert cycle_run._pid_is_alive(42)
    assert not cycle_run._pid_is_alive(43)


def test_file_lock_rejects_concurrent_owner(tmp_path: Path) -> None:
    settings = CycleControlSettings(tmp_path / "locks", 3600)
    first = CycleFileLock(settings, "2026-07-13")
    second = CycleFileLock(settings, "2026-07-13")

    first.acquire()
    try:
        with pytest.raises(CycleAlreadyRunningError):
            second.acquire()
    finally:
        first.release()


def test_young_malformed_lock_is_not_recovered(tmp_path: Path) -> None:
    settings = CycleControlSettings(tmp_path / "locks", 3600)
    lock = CycleFileLock(settings, "2026-07-13")
    lock.path.parent.mkdir(parents=True)
    lock.path.write_text("", encoding="utf-8")

    with pytest.raises(CycleAlreadyRunningError):
        lock.acquire()

    assert lock.path.exists()


def test_old_malformed_lock_is_recovered(tmp_path: Path) -> None:
    settings = CycleControlSettings(tmp_path / "locks", 60)
    lock = CycleFileLock(settings, "2026-07-13")
    lock.path.parent.mkdir(parents=True)
    lock.path.write_text("not-json", encoding="utf-8")
    old_timestamp = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).timestamp()
    os.utime(lock.path, (old_timestamp, old_timestamp))

    acquisition = lock.acquire()
    try:
        assert acquisition.recovered_stale_lock
    finally:
        lock.release()


def test_stale_dead_lock_is_recovered(tmp_path: Path) -> None:
    settings = CycleControlSettings(tmp_path / "locks", 60)
    lock = CycleFileLock(settings, "2026-07-13")
    lock.path.parent.mkdir(parents=True)
    lock.path.write_text(
        json.dumps(
            {
                "token": "stale",
                "target_date": "2026-07-13",
                "created_at": (
                    datetime.now(timezone.utc) - timedelta(hours=2)
                ).isoformat(),
                "pid": 99999999,
                "hostname": socket.gethostname(),
            }
        ),
        encoding="utf-8",
    )

    acquisition = lock.acquire()
    try:
        assert acquisition.recovered_stale_lock
        payload = json.loads(lock.path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
    finally:
        lock.release()


def test_completed_date_requires_force_rerun(tmp_path: Path) -> None:
    database_path = tmp_path / "cycle.db"
    ledger = CycleRunLedger(database_path)
    ledger.start_run(
        target_date="2026-07-13",
        force_rerun=False,
        rerun_reason=None,
        git_commit_sha="abc",
        config_sha256="def",
        stale_lock_recovered=False,
    )
    ledger.finish_run("SUCCEEDED", result={"signals": 2})

    second = CycleRunLedger(database_path)
    with pytest.raises(CycleRerunRequiredError):
        second.start_run(
            target_date="2026-07-13",
            force_rerun=False,
            rerun_reason=None,
            git_commit_sha="abc",
            config_sha256="def",
            stale_lock_recovered=False,
        )


def test_rerun_preflight_is_read_only(tmp_path: Path) -> None:
    database_path = tmp_path / "cycle.db"
    ledger = CycleRunLedger(database_path)
    ledger.start_run(
        target_date="2026-07-13",
        force_rerun=False,
        rerun_reason=None,
        git_commit_sha="abc",
        config_sha256="def",
        stale_lock_recovered=False,
    )
    ledger.finish_run("SUCCEEDED")
    before = database_path.read_bytes()

    with pytest.raises(CycleRerunRequiredError):
        CycleRunLedger(database_path).assert_rerun_allowed(
            target_date="2026-07-13",
            force_rerun=False,
            rerun_reason=None,
        )

    assert database_path.read_bytes() == before


def test_force_rerun_requires_reason_and_records_it(tmp_path: Path) -> None:
    database_path = tmp_path / "cycle.db"
    first = CycleRunLedger(database_path)
    first.start_run(
        target_date="2026-07-13",
        force_rerun=False,
        rerun_reason=None,
        git_commit_sha="abc",
        config_sha256="def",
        stale_lock_recovered=False,
    )
    first.finish_run("FAILED", error=RuntimeError("first failure"))

    no_reason = CycleRunLedger(database_path)
    with pytest.raises(CycleControlError, match="rerun-reason"):
        no_reason.start_run(
            target_date="2026-07-13",
            force_rerun=True,
            rerun_reason="",
            git_commit_sha="abc",
            config_sha256="def",
            stale_lock_recovered=False,
        )

    forced = CycleRunLedger(database_path)
    record = forced.start_run(
        target_date="2026-07-13",
        force_rerun=True,
        rerun_reason="corrected market data",
        git_commit_sha="abc",
        config_sha256="def",
        stale_lock_recovered=False,
    )
    forced.finish_run("SUCCEEDED")

    rows = read_rows(
        database_path,
        "SELECT id, force_rerun, rerun_reason FROM cycle_runs ORDER BY id",
    )
    assert record.run_id == 2
    assert rows[1]["force_rerun"] == 1
    assert rows[1]["rerun_reason"] == "corrected market data"


def test_stage_start_finish_and_failure_are_recorded(tmp_path: Path) -> None:
    database_path = tmp_path / "cycle.db"
    ledger = CycleRunLedger(database_path)
    ledger.start_run(
        target_date="2026-07-13",
        force_rerun=False,
        rerun_reason=None,
        git_commit_sha="abc",
        config_sha256="def",
        stale_lock_recovered=False,
    )
    ledger.start_stage("pre_cycle_backup", {"enabled": True})
    ledger.finish_stage({"backup_path": "backup.sqlite3"})
    error = RuntimeError("OpenD unavailable")
    ledger.start_stage("opend_connect")
    ledger.fail_stage(error)
    ledger.finish_run("FAILED", error=error)

    stages = read_rows(
        database_path,
        "SELECT stage_name, status, details_json, error_message "
        "FROM cycle_run_stages ORDER BY id",
    )
    runs = read_rows(
        database_path,
        "SELECT status, error_type, error_message FROM cycle_runs",
    )
    assert [row["status"] for row in stages] == ["SUCCEEDED", "FAILED"]
    assert "backup.sqlite3" in stages[0]["details_json"]
    assert stages[1]["error_message"] == "OpenD unavailable"
    assert runs[0]["status"] == "FAILED"
    assert runs[0]["error_type"] == "RuntimeError"


def test_stale_recovery_marks_prior_running_record_failed(tmp_path: Path) -> None:
    database_path = tmp_path / "cycle.db"
    first = CycleRunLedger(database_path)
    first.start_run(
        target_date="2026-07-13",
        force_rerun=False,
        rerun_reason=None,
        git_commit_sha="abc",
        config_sha256="def",
        stale_lock_recovered=False,
    )

    recovered = CycleRunLedger(database_path)
    recovered.start_run(
        target_date="2026-07-13",
        force_rerun=True,
        rerun_reason="recover stale process",
        git_commit_sha="abc",
        config_sha256="def",
        stale_lock_recovered=True,
    )

    rows = read_rows(
        database_path,
        "SELECT status, error_type FROM cycle_runs ORDER BY id",
    )
    assert rows[0]["status"] == "FAILED"
    assert rows[0]["error_type"] == "stale_lock_recovered"
    assert rows[1]["status"] == "RUNNING"


def test_config_fingerprint_changes_with_file_content(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("value: 1\n", encoding="utf-8")
    config = StubConfig(config_path, tmp_path / "cycle.db")
    first = config_fingerprint(config)
    config_path.write_text("value: 2\n", encoding="utf-8")
    second = config_fingerprint(config)

    assert len(first) == 64
    assert first != second
