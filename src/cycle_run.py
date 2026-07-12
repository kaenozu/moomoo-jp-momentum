"""Cross-entrypoint locking and SQLite execution ledger for daily cycles."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

_IS_WINDOWS = os.name == "nt"


class ConfigLike(Protocol):
    @property
    def database_path(self) -> str: ...

    @property
    def config_path(self) -> Path: ...

    def get(self, key_path: str, default: Any = None) -> Any: ...


class CycleControlError(RuntimeError):
    """Base error for cycle locking and ledger operations."""


class CycleAlreadyRunningError(CycleControlError):
    """Raised when another live process owns the target-date lock."""


class CycleRerunRequiredError(CycleControlError):
    """Raised when a target date already has a ledger entry."""


@dataclass(frozen=True)
class CycleControlSettings:
    lock_directory: Path = Path(".runtime/cycle-locks")
    stale_after_seconds: int = 6 * 60 * 60

    @classmethod
    def from_config(cls, config: ConfigLike) -> "CycleControlSettings":
        raw = config.get("cycle_control", {})
        if not isinstance(raw, dict):
            raise CycleControlError("cycle_control設定はmappingで指定してください")
        directory = raw.get("lock_directory", ".runtime/cycle-locks")
        stale_after = raw.get("stale_after_seconds", 6 * 60 * 60)
        if not isinstance(directory, str) or not directory.strip():
            raise CycleControlError(
                "cycle_control.lock_directoryは空でない文字列で指定してください"
            )
        if (
            isinstance(stale_after, bool)
            or not isinstance(stale_after, int)
            or stale_after <= 0
        ):
            raise CycleControlError(
                "cycle_control.stale_after_secondsは1以上の整数で指定してください"
            )
        return cls(Path(directory), stale_after)


@dataclass(frozen=True)
class LockAcquisition:
    path: Path
    token: str
    recovered_stale_lock: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _windows_pid_is_alive(pid: int) -> bool:
    """Query a Windows process handle without sending a signal."""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    kernel32 = windll.kernel32

    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit_code.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    get_last_error = kernel32.GetLastError
    get_last_error.argtypes = []
    get_last_error.restype = wintypes.DWORD

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return int(get_last_error()) == error_access_denied
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _IS_WINDOWS:
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _required_lastrowid(cursor: sqlite3.Cursor) -> int:
    value = cursor.lastrowid
    if value is None:
        raise CycleControlError("SQLite行IDを取得できませんでした")
    return value


def _validate_rerun_reason(force_rerun: bool, rerun_reason: str | None) -> None:
    if force_rerun and not (rerun_reason or "").strip():
        raise CycleControlError(
            "--force-rerun使用時は--rerun-reasonを指定してください"
        )


def _raise_existing_run(
    target_date: str,
    previous: sqlite3.Row,
) -> None:
    raise CycleRerunRequiredError(
        "対象日には既存の実行記録があります。再実行には"
        "--force-rerunと--rerun-reasonが必要です: "
        f"target_date={target_date}, run_id={previous['id']}, "
        f"status={previous['status']}"
    )


class CycleFileLock:
    """Atomic target-date lock shared by scheduler and manual CLI runs."""

    def __init__(self, settings: CycleControlSettings, target_date: str):
        self.settings = settings
        self.target_date = target_date
        safe_date = target_date.replace("/", "-").replace("\\", "-")
        self.path = settings.lock_directory / f"daily-cycle-{safe_date}.lock"
        self._acquisition: LockAcquisition | None = None

    def _read_existing(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _is_stale(self, payload: dict[str, Any]) -> bool:
        created_at = _parse_datetime(payload.get("created_at"))
        if created_at is None:
            try:
                created_at = datetime.fromtimestamp(
                    self.path.stat().st_mtime,
                    tz=timezone.utc,
                )
            except OSError:
                return False
        age = (_utc_now() - created_at).total_seconds()
        if age < self.settings.stale_after_seconds:
            return False
        owner_host = payload.get("hostname")
        owner_pid = payload.get("pid")
        if owner_host == socket.gethostname() and isinstance(owner_pid, int):
            return not _pid_is_alive(owner_pid)
        return True

    def acquire(self) -> LockAcquisition:
        if self._acquisition is not None:
            raise CycleControlError("cycle lockは既に取得済みです")
        self.settings.lock_directory.mkdir(parents=True, exist_ok=True)
        recovered = False
        for attempt in range(2):
            token = uuid.uuid4().hex
            payload = {
                "token": token,
                "target_date": self.target_date,
                "created_at": _iso_now(),
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
            }
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                existing = self._read_existing()
                if attempt == 0 and self._is_stale(existing):
                    self.path.unlink(missing_ok=True)
                    recovered = True
                    continue
                raise CycleAlreadyRunningError(
                    "同じ対象日の日次サイクルが実行中です: "
                    f"target_date={self.target_date}, lock={self.path}, "
                    f"owner={existing}"
                ) from None
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                self.path.unlink(missing_ok=True)
                raise
            self._acquisition = LockAcquisition(self.path, token, recovered)
            return self._acquisition
        raise CycleAlreadyRunningError(
            f"cycle lockを取得できませんでした: {self.path}"
        )

    def release(self) -> None:
        acquisition = self._acquisition
        if acquisition is None:
            return
        existing = self._read_existing()
        if existing.get("token") == acquisition.token:
            self.path.unlink(missing_ok=True)
        self._acquisition = None

    def __enter__(self) -> LockAcquisition:
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cycle_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_date TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    current_stage TEXT,
    rerun_reason TEXT,
    force_rerun INTEGER NOT NULL DEFAULT 0,
    git_commit_sha TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    pid INTEGER NOT NULL,
    hostname TEXT NOT NULL,
    stale_lock_recovered INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    error_message TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cycle_runs_target_date
ON cycle_runs(target_date, id DESC);

CREATE TABLE IF NOT EXISTS cycle_run_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    details_json TEXT,
    error_message TEXT,
    FOREIGN KEY (run_id) REFERENCES cycle_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_cycle_run_stages_run
ON cycle_run_stages(run_id, id);
"""


@dataclass(frozen=True)
class CycleRunRecord:
    run_id: int
    target_date: str
    status: str


def config_fingerprint(config: ConfigLike) -> str:
    path = Path(config.config_path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_git_commit_sha(repository_root: Path | None = None) -> str:
    environment_sha = os.environ.get("GITHUB_SHA")
    if environment_sha:
        return environment_sha
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = completed.stdout.strip()
    return value or "unknown"


class CycleRunLedger:
    """Persist run and stage state in the operational SQLite database."""

    TERMINAL_STATUSES = {"SUCCEEDED", "SKIPPED", "FAILED"}

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.run_id: int | None = None
        self._active_stage_id: int | None = None
        self._active_stage_name: str | None = None

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_schema(self) -> None:
        with self._connection() as connection:
            connection.executescript(SCHEMA_SQL)

    def assert_rerun_allowed(
        self,
        *,
        target_date: str,
        force_rerun: bool,
        rerun_reason: str | None,
    ) -> None:
        """Read-only preflight used before pre-cycle backup side effects."""
        _validate_rerun_reason(force_rerun, rerun_reason)
        if not self.database_path.is_file():
            return
        uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=30)
        except sqlite3.Error as error:
            raise CycleControlError(
                f"実行台帳の事前確認に失敗しました: {error}"
            ) from error
        connection.row_factory = sqlite3.Row
        try:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'cycle_runs'"
            ).fetchone()
            if table_exists is None:
                return
            previous = connection.execute(
                "SELECT id, status FROM cycle_runs "
                "WHERE target_date = ? ORDER BY id DESC LIMIT 1",
                (target_date,),
            ).fetchone()
            if previous is not None and not force_rerun:
                _raise_existing_run(target_date, previous)
        except sqlite3.Error as error:
            raise CycleControlError(
                f"実行台帳の事前確認に失敗しました: {error}"
            ) from error
        finally:
            connection.close()

    def start_run(
        self,
        *,
        target_date: str,
        force_rerun: bool,
        rerun_reason: str | None,
        git_commit_sha: str,
        config_sha256: str,
        stale_lock_recovered: bool,
    ) -> CycleRunRecord:
        _validate_rerun_reason(force_rerun, rerun_reason)
        now = _iso_now()
        with self._connection() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.execute("BEGIN IMMEDIATE")
            if stale_lock_recovered:
                connection.execute(
                    """
                    UPDATE cycle_runs
                    SET status = 'FAILED', finished_at = ?, updated_at = ?,
                        error_type = 'stale_lock_recovered',
                        error_message = 'stale cycle lock was recovered'
                    WHERE target_date = ? AND status = 'RUNNING'
                    """,
                    (now, now, target_date),
                )
            previous = connection.execute(
                "SELECT id, status FROM cycle_runs "
                "WHERE target_date = ? ORDER BY id DESC LIMIT 1",
                (target_date,),
            ).fetchone()
            if previous is not None and not force_rerun:
                _raise_existing_run(target_date, previous)
            cursor = connection.execute(
                """
                INSERT INTO cycle_runs (
                    target_date, status, started_at, rerun_reason, force_rerun,
                    git_commit_sha, config_fingerprint, pid, hostname,
                    stale_lock_recovered, created_at, updated_at
                ) VALUES (?, 'RUNNING', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_date,
                    now,
                    (rerun_reason or "").strip() or None,
                    int(force_rerun),
                    git_commit_sha,
                    config_sha256,
                    os.getpid(),
                    socket.gethostname(),
                    int(stale_lock_recovered),
                    now,
                    now,
                ),
            )
            run_id = _required_lastrowid(cursor)
        self.run_id = run_id
        return CycleRunRecord(run_id, target_date, "RUNNING")

    def start_stage(
        self,
        stage_name: str,
        details: dict[str, Any] | None = None,
    ) -> int:
        run_id = self._require_run_id()
        if self._active_stage_id is not None:
            raise CycleControlError(
                f"工程が完了していません: {self._active_stage_name}"
            )
        now = _iso_now()
        details_json = (
            json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
            if details
            else None
        )
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO cycle_run_stages (
                    run_id, stage_name, status, started_at, details_json
                ) VALUES (?, ?, 'RUNNING', ?, ?)
                """,
                (run_id, stage_name, now, details_json),
            )
            stage_id = _required_lastrowid(cursor)
            connection.execute(
                "UPDATE cycle_runs SET current_stage = ?, updated_at = ? WHERE id = ?",
                (stage_name, now, run_id),
            )
        self._active_stage_id = stage_id
        self._active_stage_name = stage_name
        return stage_id

    def finish_stage(self, details: dict[str, Any] | None = None) -> None:
        self._finish_active_stage("SUCCEEDED", details=details)

    def fail_stage(self, error: BaseException) -> None:
        self._finish_active_stage("FAILED", error_message=str(error))

    def _finish_active_stage(
        self,
        status: str,
        *,
        details: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        stage_id = self._active_stage_id
        run_id = self._require_run_id()
        if stage_id is None:
            return
        now = _iso_now()
        details_json = (
            json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
            if details
            else None
        )
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE cycle_run_stages
                SET status = ?, finished_at = ?,
                    details_json = COALESCE(?, details_json), error_message = ?
                WHERE id = ?
                """,
                (status, now, details_json, error_message, stage_id),
            )
            connection.execute(
                "UPDATE cycle_runs SET updated_at = ? WHERE id = ?",
                (now, run_id),
            )
        self._active_stage_id = None
        self._active_stage_name = None

    def finish_run(
        self,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        if status not in self.TERMINAL_STATUSES:
            raise CycleControlError(f"終端statusが不正です: {status}")
        run_id = self._require_run_id()
        if self._active_stage_id is not None:
            if error is None:
                self.finish_stage()
            else:
                self.fail_stage(error)
        now = _iso_now()
        result_json = (
            json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
            if result is not None
            else None
        )
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE cycle_runs
                SET status = ?, finished_at = ?, current_stage = NULL,
                    error_type = ?, error_message = ?, result_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    now,
                    type(error).__name__ if error is not None else None,
                    str(error) if error is not None else None,
                    result_json,
                    now,
                    run_id,
                ),
            )

    def _require_run_id(self) -> int:
        if self.run_id is None:
            raise CycleControlError("cycle runが開始されていません")
        return self.run_id
