"""Verified SQLite backup, retention, and recovery tooling.

Backups use SQLite's online backup API instead of copying database files. Restore
always targets a separate path and validates the restored snapshot before an
atomic rename makes it visible.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, cast


class ConfigLike(Protocol):
    @property
    def database_path(self) -> str: ...

    def get(self, key_path: str, default: Any = None) -> Any: ...


class BackupError(RuntimeError):
    """Base error for verified backup and recovery operations."""


class BackupVerificationError(BackupError):
    """Raised when a source, backup, or restored database fails verification."""


@dataclass(frozen=True)
class BackupSettings:
    enabled: bool = False
    directory: Path = Path("backups")
    retain_daily: int = 7
    retain_weekly: int = 4
    retain_pre_cycle: int = 7
    retain_post_cycle: int = 7
    verify_after_backup: bool = True

    @classmethod
    def from_config(cls, config: ConfigLike) -> "BackupSettings":
        raw = config.get("database_backup", {})
        if not isinstance(raw, dict):
            raise BackupError("database_backup設定はmappingで指定してください")
        directory = raw.get("directory", "backups")
        if not isinstance(directory, str) or not directory.strip():
            raise BackupError("database_backup.directoryは空でない文字列で指定してください")
        retain_daily = _positive_int(raw.get("retain_daily", 7), "retain_daily")
        retain_weekly = _positive_int(raw.get("retain_weekly", 4), "retain_weekly")
        retain_pre_cycle = _positive_int(
            raw.get("retain_pre_cycle", retain_daily),
            "retain_pre_cycle",
        )
        retain_post_cycle = _positive_int(
            raw.get("retain_post_cycle", retain_daily),
            "retain_post_cycle",
        )
        enabled = raw.get("enabled", False)
        verify = raw.get("verify_after_backup", True)
        if not isinstance(enabled, bool):
            raise BackupError("database_backup.enabledはbooleanで指定してください")
        if not isinstance(verify, bool):
            raise BackupError("database_backup.verify_after_backupはbooleanで指定してください")
        return cls(
            enabled=enabled,
            directory=Path(directory),
            retain_daily=retain_daily,
            retain_weekly=retain_weekly,
            retain_pre_cycle=retain_pre_cycle,
            retain_post_cycle=retain_post_cycle,
            verify_after_backup=verify,
        )


@dataclass(frozen=True)
class BackupMetadata:
    created_at: str
    backup_kind: str
    source_database_path: str
    backup_path: str
    schema_version: int
    file_size: int
    sha256: str
    latest_virtual_fill_date: str | None
    latest_equity_curve_date: str | None


@dataclass(frozen=True)
class BackupResult:
    dry_run: bool
    backup_path: str | None
    metadata_path: str | None
    metadata: BackupMetadata | None
    pruned_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class RestoreResult:
    dry_run: bool
    backup_path: str
    destination_path: str
    quick_check: str
    integrity_exit_code: int
    integrity_errors: int
    integrity_warnings: int


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BackupError(f"database_backup.{name}は1以上の整数で指定してください")
    return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def quick_check(path: Path) -> str:
    """Run SQLite quick_check through a read-only, explicitly closed connection."""
    if not path.is_file():
        raise BackupVerificationError(f"SQLiteファイルが見つかりません: {path}")
    try:
        with closing(sqlite3.connect(_read_only_uri(path), uri=True)) as connection:
            rows = [
                str(row[0])
                for row in connection.execute("PRAGMA quick_check").fetchall()
            ]
    except sqlite3.Error as error:
        raise BackupVerificationError(
            f"SQLite quick_checkを実行できません: {path}: {error}"
        ) from error
    if rows != ["ok"]:
        detail = "; ".join(rows) if rows else "結果なし"
        raise BackupVerificationError(
            f"SQLite quick_checkに失敗しました: {path}: {detail}"
        )
    return "ok"


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _latest_value(
    connection: sqlite3.Connection,
    table: str,
    expression: str,
) -> str | None:
    if not _table_exists(connection, table):
        return None
    row = connection.execute(f"SELECT MAX({expression}) FROM {table}").fetchone()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def _read_snapshot_metadata(path: Path) -> tuple[int, str | None, str | None]:
    with closing(sqlite3.connect(_read_only_uri(path), uri=True)) as connection:
        schema_row = connection.execute("PRAGMA user_version").fetchone()
        schema_version = int(schema_row[0]) if schema_row else 0
        latest_fill = _latest_value(
            connection,
            "virtual_fills",
            "substr(filled_at, 1, 10)",
        )
        latest_equity = _latest_value(connection, "virtual_equity_curve", "date")
    return schema_version, latest_fill, latest_equity


class DatabaseBackupManager:
    """Create, verify, rotate, and restore SQLite snapshots."""

    VALID_KINDS = {"daily", "weekly", "pre_cycle", "post_cycle"}

    def __init__(self, config: ConfigLike):
        self.config = config
        self.source_path = Path(config.database_path)
        self.settings = BackupSettings.from_config(config)

    def create_backup(
        self,
        *,
        kind: str = "daily",
        dry_run: bool = False,
        created_at: datetime | None = None,
    ) -> BackupResult:
        if kind not in self.VALID_KINDS:
            raise BackupError(f"backup kindが不正です: {kind}")
        if not self.source_path.is_file():
            raise BackupError(f"バックアップ元DBが見つかりません: {self.source_path}")

        quick_check(self.source_path)
        timestamp = (created_at or _utc_now()).astimezone(timezone.utc)
        stamp = timestamp.strftime("%Y%m%dT%H%M%S.%fZ")
        backup_name = f"{self.source_path.stem}-{stamp}-{kind}.sqlite3"
        backup_path = self.settings.directory / backup_name
        metadata_path = backup_path.with_suffix(backup_path.suffix + ".json")

        if dry_run:
            return BackupResult(
                dry_run=True,
                backup_path=str(backup_path),
                metadata_path=str(metadata_path),
                metadata=None,
            )

        self.settings.directory.mkdir(parents=True, exist_ok=True)
        if backup_path.exists() or metadata_path.exists():
            raise BackupError(f"バックアップ出力先が既に存在します: {backup_path}")

        token = uuid.uuid4().hex
        temp_backup = self.settings.directory / f".{backup_name}.{token}.tmp"
        temp_metadata = self.settings.directory / (
            f".{metadata_path.name}.{token}.tmp"
        )
        published_backup = False
        try:
            with closing(
                sqlite3.connect(_read_only_uri(self.source_path), uri=True)
            ) as source:
                with closing(sqlite3.connect(temp_backup)) as destination:
                    source.backup(destination, pages=256, sleep=0.05)
            if self.settings.verify_after_backup:
                quick_check(temp_backup)

            schema_version, latest_fill, latest_equity = _read_snapshot_metadata(
                temp_backup
            )
            metadata = BackupMetadata(
                created_at=timestamp.isoformat(),
                backup_kind=kind,
                source_database_path=str(self.source_path.resolve()),
                backup_path=str(backup_path.resolve()),
                schema_version=schema_version,
                file_size=temp_backup.stat().st_size,
                sha256=_sha256(temp_backup),
                latest_virtual_fill_date=latest_fill,
                latest_equity_curve_date=latest_equity,
            )
            temp_metadata.write_text(
                json.dumps(asdict(metadata), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temp_backup, backup_path)
            published_backup = True
            os.replace(temp_metadata, metadata_path)
        except Exception:
            temp_backup.unlink(missing_ok=True)
            temp_metadata.unlink(missing_ok=True)
            if published_backup:
                backup_path.unlink(missing_ok=True)
            raise

        pruned = self.prune(dry_run=False)
        return BackupResult(
            dry_run=False,
            backup_path=str(backup_path),
            metadata_path=str(metadata_path),
            metadata=metadata,
            pruned_files=tuple(str(path) for path in pruned),
        )

    def verify_backup(self, backup_path: Path) -> BackupMetadata | None:
        quick_check(backup_path)
        metadata_path = backup_path.with_suffix(backup_path.suffix + ".json")
        if not metadata_path.is_file():
            return None
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BackupVerificationError(
                f"バックアップメタデータを読み込めません: {metadata_path}: {error}"
            ) from error
        expected = payload.get("sha256")
        if not isinstance(expected, str) or not expected:
            raise BackupVerificationError(
                f"SHA-256メタデータが不正です: {metadata_path}"
            )
        actual = _sha256(backup_path)
        if actual != expected:
            raise BackupVerificationError(
                "バックアップSHA-256が一致しません: "
                f"expected={expected} actual={actual}"
            )
        try:
            return BackupMetadata(**payload)
        except TypeError as error:
            raise BackupVerificationError(
                f"バックアップメタデータの項目が不正です: {metadata_path}: {error}"
            ) from error

    def prune(self, *, dry_run: bool = False) -> list[Path]:
        directory = self.settings.directory
        if not directory.is_dir():
            return []
        deleted: list[Path] = []
        limits = {
            "daily": self.settings.retain_daily,
            "weekly": self.settings.retain_weekly,
            "pre_cycle": self.settings.retain_pre_cycle,
            "post_cycle": self.settings.retain_post_cycle,
        }
        for kind, limit in limits.items():
            candidates = sorted(
                directory.glob(f"{self.source_path.stem}-*-{kind}.sqlite3"),
                key=lambda path: path.name,
                reverse=True,
            )
            for path in candidates[limit:]:
                metadata_path = path.with_suffix(path.suffix + ".json")
                if metadata_path.exists():
                    deleted.extend([path, metadata_path])
                else:
                    deleted.append(path)
                if not dry_run:
                    path.unlink(missing_ok=True)
                    metadata_path.unlink(missing_ok=True)
        return deleted

    def restore_backup(
        self,
        backup_path: Path,
        destination_path: Path,
        *,
        portfolio_name: str | None = None,
        as_of_date: str | None = None,
        dry_run: bool = False,
        require_history: bool = False,
    ) -> RestoreResult:
        backup_path = backup_path.resolve()
        destination_path = destination_path.resolve()
        source_path = self.source_path.resolve()
        if portfolio_name is None:
            from .trading_identity import virtual_portfolio_name

            portfolio_name = virtual_portfolio_name(self.config)
        if destination_path == source_path:
            raise BackupError("稼働中DBと同じパスには復元できません")
        if destination_path.exists():
            raise BackupError(f"復元先が既に存在します: {destination_path}")

        if self.verify_backup(backup_path) is None:
            raise BackupVerificationError(
                "復元にはSHA-256を含むバックアップメタデータが必要です"
            )

        def run_integrity(candidate: Path) -> Any:
            from .virtual_trade_integrity import VirtualTradeIntegrityChecker

            checker = VirtualTradeIntegrityChecker(cast(Any, self.config))
            checker.db_path = candidate
            return checker.run(
                portfolio_name,
                as_of_date,
                require_history=require_history,
            )

        if dry_run:
            report = run_integrity(backup_path)
            if report.errors:
                raise BackupVerificationError(
                    "バックアップDBの仮想取引整合性検査に失敗しました: "
                    f"errors={len(report.errors)} warnings={len(report.warnings)}"
                )
            return RestoreResult(
                dry_run=True,
                backup_path=str(backup_path),
                destination_path=str(destination_path),
                quick_check="ok",
                integrity_exit_code=report.exit_code,
                integrity_errors=len(report.errors),
                integrity_warnings=len(report.warnings),
            )

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination_path.parent / (
            f".{destination_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with closing(
                sqlite3.connect(_read_only_uri(backup_path), uri=True)
            ) as source:
                with closing(sqlite3.connect(temp_path)) as destination:
                    source.backup(destination, pages=256, sleep=0.05)
            check_result = quick_check(temp_path)

            report = run_integrity(temp_path)
            if report.errors:
                raise BackupVerificationError(
                    "復元DBの仮想取引整合性検査に失敗しました: "
                    f"errors={len(report.errors)} warnings={len(report.warnings)}"
                )
            os.replace(temp_path, destination_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

        return RestoreResult(
            dry_run=False,
            backup_path=str(backup_path),
            destination_path=str(destination_path),
            quick_check=check_result,
            integrity_exit_code=report.exit_code,
            integrity_errors=len(report.errors),
            integrity_warnings=len(report.warnings),
        )
