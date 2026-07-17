from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "script",
    [
        "scripts/validate_v2_migration.py",
        "scripts/compare_backtest_runs.py",
    ],
)
def test_v2_validation_cli_help(script: str) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, script, "--help"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
