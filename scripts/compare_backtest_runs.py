#!/usr/bin/env python3
"""Compare normalized outputs from legacy and candidate backtest runs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.v2_validation import (
    compare_backtest_runs,
    write_json_report,
    write_markdown_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare orders, fills, positions, and equity curves from two "
            "backtest runs. IDs and creation timestamps are intentionally ignored."
        )
    )
    parser.add_argument("--legacy-db", required=True)
    parser.add_argument("--legacy-run-id", required=True, type=int)
    parser.add_argument("--candidate-db", required=True)
    parser.add_argument("--candidate-run-id", required=True, type=int)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument(
        "--allow-field",
        action="append",
        default=[],
        help=(
            "Expected difference in SECTION.FIELD form, for example "
            "equity.total_equity. May be repeated."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="reports/v2_validation",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = compare_backtest_runs(
        legacy_database=args.legacy_db,
        legacy_run_id=args.legacy_run_id,
        candidate_database=args.candidate_db,
        candidate_run_id=args.candidate_run_id,
        tolerance=args.tolerance,
        expected_difference_fields=args.allow_field,
    )
    output_dir = Path(args.output_dir)
    json_path = write_json_report(report, output_dir / "backtest-comparison.json")
    markdown_path = write_markdown_report(
        report,
        output_dir / "backtest-comparison.md",
    )

    print(f"status={report.status}")
    print(f"differences={len(report.differences)}")
    print(f"json_report={json_path}")
    print(f"markdown_report={markdown_path}")
    for difference in report.differences:
        marker = "expected" if difference.expected else "unexpected"
        print(
            f"{marker}: {difference.section}[{difference.key}].{difference.field}: "
            f"{difference.legacy_value!r} -> {difference.candidate_value!r}",
            file=sys.stderr,
        )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
