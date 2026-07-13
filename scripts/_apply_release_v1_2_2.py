from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "scripts/build_moomoo_discovery_release.py",
    "scripts/compare_moomoo_discovery_releases.py",
    "tests/test_moomoo_discovery_release.py",
    "tests/test_moomoo_human_validation.py",
    ".github/workflows/moomoo-operator-release.yml",
    "tools/production_discovery/README_moomoo_human_validation_ja.md",
    "tools/production_discovery/validate_moomoo_human_validation.py",
)


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def run_without_github_event_identity(*args: str) -> None:
    env = os.environ.copy()
    for name in ("GITHUB_SHA", "GITHUB_REF", "GITHUB_EVENT_NAME"):
        env.pop(name, None)
    subprocess.run(args, cwd=ROOT, check=True, env=env)


def main() -> None:
    changed: list[str] = []
    for relative in TARGETS:
        path = ROOT / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        updated = text.replace("1.2.1", "1.2.2")
        if updated != text:
            path.write_text(updated, encoding="utf-8", newline="\n")
            changed.append(relative)

    comparer_path = ROOT / "scripts/compare_moomoo_discovery_releases.py"
    comparer = comparer_path.read_text(encoding="utf-8")
    member = '    "test_moomoo_operator_common_errors.py",\n'
    marker = '    "test_moomoo_discovery_operator.py",\n'
    if member not in comparer:
        if comparer.count(marker) != 1:
            raise RuntimeError("Could not locate operator member insertion point")
        comparer = comparer.replace(marker, marker + member, 1)
        comparer_path.write_text(comparer, encoding="utf-8", newline="\n")

    required = {
        "scripts/build_moomoo_discovery_release.py",
        "scripts/compare_moomoo_discovery_releases.py",
        "tests/test_moomoo_discovery_release.py",
        ".github/workflows/moomoo-operator-release.yml",
        "tools/production_discovery/README_moomoo_human_validation_ja.md",
        "tools/production_discovery/validate_moomoo_human_validation.py",
    }
    missing = sorted(required - set(changed))
    if missing:
        raise RuntimeError(f"Expected release version updates were absent: {missing}")

    critical = [ROOT / item for item in required]
    stale = [
        str(path.relative_to(ROOT))
        for path in critical
        if "1.2.1" in path.read_text(encoding="utf-8")
    ]
    if stale:
        raise RuntimeError(f"Stale release v1.2.1 references remain: {stale}")
    if member.strip() not in comparer_path.read_text(encoding="utf-8"):
        raise RuntimeError("Release verifier lacks operator v1.2.2 member")

    (ROOT / "scripts/_apply_release_v1_2_2.py").unlink()
    (ROOT / ".github/workflows/tests.yml").write_bytes(
        subprocess.run(
            ["git", "show", "origin/master:.github/workflows/tests.yml"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
    )

    run("git", "config", "user.name", "github-actions[bot]")
    run(
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    run("git", "add", "-A")
    run("git", "commit", "-m", "fix: align discovery release with operator v1.2.2")

    run(
        sys.executable,
        "-m",
        "py_compile",
        "scripts/build_moomoo_discovery_release.py",
        "scripts/compare_moomoo_discovery_releases.py",
        "tools/production_discovery/validate_moomoo_human_validation.py",
    )
    run(
        sys.executable,
        "-m",
        "pytest",
        "tests/test_moomoo_discovery_release.py",
        "tests/test_moomoo_human_validation.py",
        "-q",
    )
    with tempfile.TemporaryDirectory() as tmp:
        left = Path(tmp) / "left"
        right = Path(tmp) / "right"
        report = Path(tmp) / "comparison.json"
        run_without_github_event_identity(
            sys.executable,
            "scripts/build_moomoo_discovery_release.py",
            "--output-dir",
            str(left),
        )
        run_without_github_event_identity(
            sys.executable,
            "scripts/build_moomoo_discovery_release.py",
            "--output-dir",
            str(right),
        )
        run_without_github_event_identity(
            sys.executable,
            "scripts/compare_moomoo_discovery_releases.py",
            "--left",
            str(left / "moomoo_production_discovery_release_v1.2.2.zip"),
            "--right",
            str(right / "moomoo_production_discovery_release_v1.2.2.zip"),
            "--output",
            str(report),
        )

    run("git", "push", "origin", "HEAD:agent/release-v1.2.2")


if __name__ == "__main__":
    main()
