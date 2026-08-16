from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.source_manifest import (
    ManifestError,
    build_manifest,
    validate_manifest,
)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_manifest_is_deterministic_and_contains_checksums(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    _write(tmp_path, "README.md", "# app\n")

    first = build_manifest(tmp_path, source_commit="a" * 40)
    second = build_manifest(tmp_path, source_commit="a" * 40)

    assert first == second
    assert [member["path"] for member in first["members"]] == [
        "README.md",
        "src/app.py",
    ]
    assert all(len(member["sha256"]) == 64 for member in first["members"])


def test_manifest_excludes_runtime_and_sensitive_files(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "ok\n")
    _write(tmp_path, "config.yaml", "secrets: not included\n")
    _write(tmp_path, "data/moomoo.db", "not a real database\n")
    _write(tmp_path, "data/symbols.json", "[]\n")
    _write(tmp_path, ".env.test", "TOKEN=do-not-read\n")
    _write(tmp_path, "__pycache__/module.pyc", "runtime cache\n")

    manifest = build_manifest(tmp_path, source_commit="b" * 40)

    assert [member["path"] for member in manifest["members"]] == [
        "data/symbols.json",
        "src/app.py",
    ]


def test_validate_manifest_rejects_checksum_tampering(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    manifest = build_manifest(tmp_path, source_commit="c" * 40)
    manifest["members"][0]["sha256"] = "0" * 64

    with pytest.raises(ManifestError, match="checksum mismatch"):
        validate_manifest(tmp_path, manifest)


def test_manifest_serialization_has_stable_key_order(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    manifest = build_manifest(tmp_path, source_commit="d" * 40)

    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    assert encoded == json.dumps(
        json.loads(encoded), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
