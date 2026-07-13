#!/usr/bin/env python3
"""Build a deterministic source bundle for the read-only discovery operator."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "production_discovery"
DIST = ROOT / "dist"
FILES = [
    "moomoo_production_readonly_discovery_v4.ps1",
    "moomoo_discovery_v4_common.ps1",
    "moomoo_discovery_v4_runtime.ps1",
    "moomoo_discovery_v4_storage.ps1",
    "moomoo_discovery_v4_gate.ps1",
    "moomoo_discovery_operator.py",
    "moomoo_operator_common.py",
    "moomoo_operator_review.py",
    "moomoo_operator_cli.py",
    "test_moomoo_discovery_operator.py",
    "test_bundle_builder.py",
    "run_moomoo_discovery_operator_tests.ps1",
    "validate_moomoo_discovery_operator.py",
    "README_moomoo_discovery_operator_ja.md",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def normalize_text_line_endings(data: bytes) -> bytes:
    """Normalize checkout-only CRLF/CR differences without changing other bytes."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def checkout_matches_tracked_blob(checkout_bytes: bytes, blob_bytes: bytes) -> bool:
    """Allow only line-ending differences between a checkout and its Git blob."""
    return normalize_text_line_endings(checkout_bytes) == normalize_text_line_endings(
        blob_bytes
    )


def read_tracked_blob(path: Path) -> bytes:
    """Read exact committed bytes so bundle output is independent of checkout EOLs."""
    try:
        relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError(f"Bundle source is outside repository root: {path}") from exc

    completed = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"HEAD:{relative}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Could not read tracked Git blob for {relative}: {diagnostic}"
        )
    return completed.stdout


def verified_tracked_bytes(path: Path) -> bytes:
    """Return exact Git bytes after rejecting substantive checkout modifications."""
    if not path.is_file():
        raise RuntimeError(f"Bundle source file does not exist: {path}")
    checkout_bytes = path.read_bytes()
    blob_bytes = read_tracked_blob(path)
    if not checkout_matches_tracked_blob(checkout_bytes, blob_bytes):
        raise RuntimeError(
            "Working-tree bundle source differs from the committed Git blob beyond "
            f"line endings: {path}"
        )
    return blob_bytes


def load_operator_module():
    path = SOURCE / "moomoo_discovery_operator.py"
    spec = importlib.util.spec_from_file_location("bundle_operator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load operator module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def deterministic_built_at() -> str:
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH", "1783900800")
    try:
        timestamp = int(source_date_epoch)
    except ValueError as exc:
        raise RuntimeError("SOURCE_DATE_EPOCH must be an integer") from exc
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def main() -> int:
    operator = load_operator_module()
    source_bytes = {name: verified_tracked_bytes(SOURCE / name) for name in FILES}

    expected_files = dict(operator.EXPECTED_BUNDLE_FILE_SHA256)
    expected_files[operator.GATE_FILENAME] = operator.EXPECTED_GATE_SHA256
    for name, expected_hash in expected_files.items():
        actual_hash = sha256_bytes(source_bytes[name])
        if actual_hash != expected_hash:
            raise RuntimeError(
                f"Frozen bundle hash mismatch for {name}: "
                f"{actual_hash} != {expected_hash}"
            )
    actual_discovery_hash = expected_files[operator.DISCOVERY_FILENAME]

    subprocess.run(
        [sys.executable, str(SOURCE / "validate_moomoo_discovery_operator.py")],
        check=True,
        cwd=SOURCE,
    )
    subprocess.run(
        [sys.executable, "-m", "unittest", "-q"],
        check=True,
        cwd=SOURCE,
    )

    DIST.mkdir(exist_ok=True)
    stage = DIST / f"moomoo_production_discovery_operator_v4_v{operator.VERSION}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()

    for name in FILES:
        (stage / name).write_bytes(source_bytes[name])

    sums = []
    for path in sorted(stage.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file():
            sums.append(f"{sha256(path)}  {path.name}")
    (stage / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")

    manifest = {
        "report_type": "moomoo_discovery_operator_bundle_manifest",
        "operator_version": operator.VERSION,
        "discovery_version": "4.0.0",
        "built_at": deterministic_built_at(),
        "discovery_sha256": actual_discovery_hash,
        "source_bytes": "git_blob",
        "checkout_line_endings_ignored": True,
        "files": {
            path.name: sha256(path)
            for path in sorted(stage.iterdir(), key=lambda item: item.name.lower())
            if path.is_file()
        },
        "authorization": {
            "production_readiness": "BLOCKED",
            "preflight_authorized": False,
            "production_drill_authorized": False,
            "cutover_authorized": False,
        },
    }
    (stage / "bundle-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    zip_path = DIST / f"{stage.name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    fixed_time = (2026, 7, 13, 0, 0, 0)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.name, date_time=fixed_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())

    print(
        json.dumps(
            {
                "zip": str(zip_path),
                "zip_sha256": sha256(zip_path),
                "stage": str(stage),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
