from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools/production_discovery/run_moomoo_discovery_operator_tests.ps1"

text = TARGET.read_text(encoding="utf-8")
old = '''    if ($OperatorVersion -ne "1.2.1") {
        throw "Operator version check failed: expected 1.2.1, got $OperatorVersion"
    }'''
new = '''    if ($OperatorVersion -ne "1.2.2") {
        throw "Operator version check failed: expected 1.2.2, got $OperatorVersion"
    }'''
if text.count(old) != 1:
    raise RuntimeError("Expected exactly one operator version assertion")
TARGET.write_text(text.replace(old, new), encoding="utf-8", newline="\n")

(ROOT / "scripts/_patch_operator_test_version.py").unlink()
(ROOT / ".github/workflows/_patch-operator-test-version.yml").unlink()

subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)
subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
subprocess.run(["git", "commit", "-m", "test: expect operator v1.2.2 in Windows harness"], cwd=ROOT, check=True)
subprocess.run(["git", "push", "origin", "HEAD:agent/operator-v1.2.2-windows-hardening"], cwd=ROOT, check=True)
