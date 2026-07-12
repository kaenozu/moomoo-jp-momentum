"""Explicit CLI for verified SQLite backup and recovery."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Sequence, cast

from src.config import Config
from src.database_backup import BackupError, DatabaseBackupManager


def _emit(value: object) -> None:
    payload: object
    if is_dataclass(value) and not isinstance(value, type):
        payload = asdict(cast(Any, value))
    else:
        payload = value
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="検証付きSQLiteバックアップ・復元")
    parser.add_argument("--config", default="config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="SQLiteスナップショットを作成")
    backup.add_argument(
        "--kind",
        choices=("daily", "weekly", "pre_cycle", "post_cycle"),
        default="daily",
    )
    backup.add_argument("--dry-run", action="store_true")

    verify = subparsers.add_parser("verify", help="バックアップを検証")
    verify.add_argument("backup_path", type=Path)

    restore = subparsers.add_parser("restore", help="別パスへ復元して検証")
    restore.add_argument("backup_path", type=Path)
    restore.add_argument("destination_path", type=Path)
    restore.add_argument("--strategy", default="momentum")
    restore.add_argument("--as-of", default=None, dest="as_of_date")
    restore.add_argument("--dry-run", action="store_true")

    prune = subparsers.add_parser(
        "prune",
        help="保持世代を超えたバックアップを削除",
    )
    prune.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manager = DatabaseBackupManager(Config(args.config))
        if args.command == "backup":
            _emit(manager.create_backup(kind=args.kind, dry_run=args.dry_run))
        elif args.command == "verify":
            metadata = manager.verify_backup(args.backup_path)
            _emit(
                {
                    "quick_check": "ok",
                    "metadata": asdict(metadata) if metadata else None,
                }
            )
        elif args.command == "restore":
            _emit(
                manager.restore_backup(
                    args.backup_path,
                    args.destination_path,
                    strategy_name=args.strategy,
                    as_of_date=args.as_of_date,
                    dry_run=args.dry_run,
                )
            )
        elif args.command == "prune":
            deleted = manager.prune(dry_run=args.dry_run)
            _emit(
                {
                    "dry_run": args.dry_run,
                    "files": [str(path) for path in deleted],
                }
            )
        else:
            raise AssertionError(f"unknown command: {args.command}")
    except (BackupError, FileNotFoundError, ValueError) as error:
        print(str(error))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
