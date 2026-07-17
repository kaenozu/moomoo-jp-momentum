#!/usr/bin/env python3
"""Validate V2 database migration against a non-destructive SQLite backup."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.v2_validation import (
    validate_database_migration,
    write_json_report,
    write_markdown_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create an online backup of the source SQLite database, apply the "
            "current DataStore migration only to the copy, and validate data "
            "preservation, integrity, foreign keys, metadata columns, and idempotency."
        )
    )
    parser.add_argument("--config", default="config.yaml", help="YAML config path")
    parser.add_argument(
        "--database",
        default=None,
        help="Source SQLite database. Defaults to database.path from --config.",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/v2_validation",
        help="Directory for copied DB and reports",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    if args.database:
        database_path = Path(args.database)
    else:
        from src.config import Config

        database_path = Path(Config(str(config_path)).database_path)

    output_dir = Path(args.output_dir)
    report = validate_database_migration(
        source_database=database_path,
        config_path=config_path,
        output_directory=output_dir,
    )
    json_path = write_json_report(report, output_dir / "migration-report.json")
    markdown_path = write_markdown_report(report, output_dir / "migration-report.md")

    print(f"status={report.status}")
    print(f"source_unchanged={report.source_unchanged}")
    print(f"integrity_check={report.integrity_check}")
    print(f"foreign_key_violations={len(report.foreign_key_violations)}")
    print(f"required_columns_present={report.required_columns_present}")
    print(f"idempotent={report.idempotent}")
    print(f"json_report={json_path}")
    print(f"markdown_report={markdown_path}")
    for error in report.errors:
        print(f"error={error}", file=sys.stderr)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
