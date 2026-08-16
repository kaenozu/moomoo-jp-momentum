"""Deterministic, validation-only source manifest generation.

This module only reads the checked-out source tree.  It deliberately excludes
runtime data and operational configuration so it cannot package live state.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
ARTIFACT_TYPE = "MOOMOO_SOURCE_VALIDATION_MANIFEST"

_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
_EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "backups",
    "dist",
    "logs",
    "reports",
}
_EXCLUDED_EXACT = {"config.yaml", "config.yml"}
_ALLOWED_DATA = {"data/symbols.json"}
_SENSITIVE_SUFFIXES = {
    ".db",
    ".der",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
}


class ManifestError(ValueError):
    """Raised when source manifest input or validation is unsafe."""


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_manifest_path(path: str) -> bool:
    """Return whether a relative path is safe to inspect as source."""

    pure = PurePosixPath(path)
    if not path or pure.as_posix() != path or path.startswith("/"):
        return False
    if any(part in {"", ".", ".."} for part in path.split("/")):
        return False
    if path in _EXCLUDED_EXACT or path.startswith(".env"):
        return False
    if path.startswith("data/") and path not in _ALLOWED_DATA:
        return False
    if any(part in _EXCLUDED_DIRS for part in pure.parts):
        return False
    return not path.casefold().endswith(tuple(_SENSITIVE_SUFFIXES))


def _source_files(root: Path) -> list[tuple[str, Path]]:
    if not root.is_dir():
        raise ManifestError(f"source root is not a directory: {root}")
    files: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if is_manifest_path(relative):
            files.append((relative, path))
    return files


def build_manifest(
    root: Path,
    *,
    source_commit: str,
    source_ref: str = "local",
    source_event: str = "local",
    source_dirty: bool = False,
) -> dict[str, Any]:
    """Build a stable manifest without invoking any trading or runtime path."""

    if not _COMMIT_RE.fullmatch(source_commit):
        raise ManifestError("source_commit must be a lowercase Git object ID")
    members: list[dict[str, Any]] = []
    for relative, path in _source_files(root):
        members.append(
            {
                "path": relative,
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            }
        )
    if not members:
        raise ManifestError("source tree contains no releasable files")
    return {
        "artifact_type": ARTIFACT_TYPE,
        "cutover_authorized": False,
        "member_count": len(members),
        "members": members,
        "production_authority": False,
        "real_order_authorized": False,
        "schema_version": SCHEMA_VERSION,
        "source_commit": source_commit,
        "source_dirty": source_dirty,
        "source_event": source_event,
        "source_ref": source_ref,
        "total_source_bytes": sum(member["size"] for member in members),
    }


def validate_manifest(root: Path, manifest: dict[str, Any]) -> None:
    """Recompute all source members and fail closed on any mismatch."""

    expected = build_manifest(
        root,
        source_commit=manifest.get("source_commit", ""),
        source_ref=manifest.get("source_ref", "local"),
        source_event=manifest.get("source_event", "local"),
        source_dirty=manifest.get("source_dirty", False),
    )
    if manifest != expected:
        expected_by_path = {item["path"]: item for item in expected["members"]}
        for member in manifest.get("members", []):
            path = member.get("path") if isinstance(member, dict) else None
            if path in expected_by_path and member.get("sha256") != expected_by_path[path]["sha256"]:
                raise ManifestError(f"checksum mismatch: {path}")
        raise ManifestError("manifest does not match the current source tree")


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the canonical UTF-8 representation used for checksums."""

    return _canonical_json(manifest)
