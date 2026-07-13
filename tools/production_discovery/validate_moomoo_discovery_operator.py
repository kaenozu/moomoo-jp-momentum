#!/usr/bin/env python3
"""Static fail-closed validation for the discovery operator source bundle."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PYTHON_FILES = [
    ROOT / "moomoo_discovery_operator.py",
    ROOT / "moomoo_operator_common.py",
    ROOT / "moomoo_operator_review.py",
    ROOT / "moomoo_operator_cli.py",
]
DISCOVERY_FILES = [
    ROOT / "moomoo_production_readonly_discovery_v4.ps1",
    ROOT / "moomoo_discovery_v4_common.ps1",
    ROOT / "moomoo_discovery_v4_runtime.ps1",
    ROOT / "moomoo_discovery_v4_storage.ps1",
]
GATE = ROOT / "moomoo_discovery_v4_gate.ps1"

FORBIDDEN_OPERATOR_IMPORTS = {"sqlite3", "requests", "httpx", "moomoo", "futu"}
FORBIDDEN_DISCOVERY_TOKENS = {
    "Stop-Process", "Stop-Service", "Start-Service", "Disable-ScheduledTask",
    "Enable-ScheduledTask", "Set-ScheduledTask", "Register-ScheduledTask",
    "Unregister-ScheduledTask", "Set-Content", "Out-File", "Add-Content",
    "Remove-Item", "New-Item", "sqlite3.connect", "System.Data.SQLite",
    "-PreflightOnly", "-ConfirmProductionExecution",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_operator() -> dict[str, Any]:
    forbidden_imports: set[str] = set()
    shell_true_lines: list[str] = []
    files: list[dict[str, Any]] = []
    for path in PYTHON_FILES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if (
                        keyword.arg == "shell"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        shell_true_lines.append(f"{path.name}:{node.lineno}")
        forbidden_imports.update(imports & FORBIDDEN_OPERATOR_IMPORTS)
        files.append({"path": str(path), "sha256": sha256(path), "syntax_ok": True})
    return {
        "files": files,
        "forbidden_imports": sorted(forbidden_imports),
        "shell_true_lines": shell_true_lines,
        "passed": not forbidden_imports and not shell_true_lines,
    }


def validate_discovery() -> dict[str, Any]:
    hits: list[str] = []
    files: list[dict[str, Any]] = []
    combined = ""
    for path in DISCOVERY_FILES:
        source = path.read_text(encoding="utf-8")
        combined += source
        file_hits = sorted(token for token in FORBIDDEN_DISCOVERY_TOKENS if token in source)
        hits.extend(f"{path.name}:{token}" for token in file_hits)
        files.append({"path": str(path), "sha256": sha256(path)})
    required_flags = {
        "sqlite_connection_performed = $false",
        "process_or_task_state_changed = $false",
        "git_mutation_performed = $false",
        "preflight_executed = $false",
        "production_drill_executed = $false",
        "cutover_executed = $false",
    }
    missing_flags = sorted(flag for flag in required_flags if flag not in combined)
    return {
        "files": files,
        "forbidden_token_hits": hits,
        "missing_safety_flags": missing_flags,
        "passed": not hits and not missing_flags,
    }


def validate_gate() -> dict[str, Any]:
    source = GATE.read_text(encoding="utf-8")
    required = [
        "System.Management.Automation.Language.Parser", "ExpectedFileHashesJson",
        "file_checks", "hash_matches", "gate_passed",
        "preflight_authorized = $false", "production_drill_authorized = $false",
        "cutover_authorized = $false",
    ]
    missing = [item for item in required if item not in source]
    return {
        "path": str(GATE),
        "sha256": sha256(GATE),
        "missing_contract_tokens": missing,
        "passed": not missing,
    }


def main() -> int:
    payload = {
        "report_type": "moomoo_discovery_operator_static_validation",
        "operator": validate_operator(),
        "discovery": validate_discovery(),
        "gate": validate_gate(),
    }
    payload["passed"] = all(payload[name]["passed"] for name in ("operator", "discovery", "gate"))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
