#!/usr/bin/env python3
"""Test-only Python shim for handoff runner negative-path validation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def delegate(arguments: list[str]) -> int:
    executable = os.environ["MOOMOO_HANDOFF_REAL_PYTHON"]
    completed = subprocess.run([executable, *arguments], check=False)
    return completed.returncode


def option(arguments: list[str], name: str) -> str:
    index = arguments.index(name)
    return arguments[index + 1]


def fake_operator(arguments: list[str], mode: str) -> int:
    output_root = Path(option(arguments, "--output-root"))
    evidence = output_root / "moomoo-discovery-shim"
    evidence.mkdir(parents=False, exist_ok=False)
    result = {
        "report_type": "moomoo_discovery_operator_result",
        "operator_version": "1.2.2",
        "status": "completed_readonly_discovery",
        "operator_exit_code": 1 if mode == "exit_mismatch" else 0,
        "powershell_exit_code": 0,
        "validation_status": "MACHINE_PASS_HUMAN_REVIEW_REQUIRED",
        "machine_validation_status": "PASS",
        "human_validation_status": "PENDING",
        "operational_validation_status": "INCONCLUSIVE",
        "production_readiness": "BLOCKED",
        "preflight_authorized": False,
        "production_drill_authorized": False,
        "cutover_authorized": False,
        "evidence_complete": mode != "missing_shareable",
    }
    (evidence / "05-operator-result.json").write_text(
        json.dumps(result, indent=2) + "\n",
        encoding="utf-8",
    )
    if mode != "missing_shareable":
        (evidence / "03-discovery-redacted.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (evidence / "04-discovery-summary.md").write_text(
            "# synthetic\n", encoding="utf-8"
        )
    return 0


def main() -> int:
    arguments = sys.argv[1:]
    mode = os.environ.get("MOOMOO_HANDOFF_SHIM_MODE", "delegate")
    if (
        mode in {"missing_shareable", "exit_mismatch"}
        and len(arguments) >= 2
        and Path(arguments[0]).name == "moomoo_discovery_operator.py"
        and arguments[1] == "run"
    ):
        return fake_operator(arguments, mode)
    return delegate(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
