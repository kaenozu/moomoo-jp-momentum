#!/usr/bin/env python3
"""Build a deterministic, master-bound read-only discovery handoff ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "production_discovery" / "handoff"
DEFAULT_DIST = ROOT / "dist"
HANDOFF_VERSION = "1.2.2"
HANDOFF_FORMAT_VERSION = 1
OPERATOR_VERSION = "1.2.2"
OPERATOR_BUNDLE_NAME = (
    "moomoo_production_discovery_operator_v4_v1.2.2.zip"
)
OPERATOR_REQUIRED_MEMBERS = {
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
    "SHA256SUMS.txt",
    "bundle-manifest.json",
}
HANDOFF_BASENAME = f"moomoo-readonly-discovery-handoff-v{HANDOFF_VERSION}"
EXPECTED_REMOTE = "https://github.com/kaenozu/moomoo-jp-momentum.git"
SOURCE_FILES = [
    "README_FIRST.md",
    "LOCAL_AGENT_PROMPT.md",
    "EVIDENCE_REVIEW_CHECKLIST.md",
    "run-readonly-discovery.ps1",
    "verify-handoff.ps1",
]
AUTHORIZATION = {
    "production_readiness": "BLOCKED",
    "preflight_authorized": False,
    "production_drill_authorized": False,
    "cutover_authorized": False,
}
FIXED_ZIP_TIME = (2026, 7, 13, 0, 0, 0)


class BuildError(RuntimeError):
    """Raised when a handoff cannot be built safely."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def remove_tree(path: Path) -> None:
    if not path.exists():
        return

    def clear_readonly_and_retry(function, target, _exc_info) -> None:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        function(target)

    shutil.rmtree(path, onerror=clear_readonly_and_retry)


def validate_top_level_zip_infos(
    infos: list[zipfile.ZipInfo],
    label: str,
) -> list[zipfile.ZipInfo]:
    files: list[zipfile.ZipInfo] = []
    seen: dict[str, str] = {}
    for info in infos:
        name = info.filename
        normalized = name.replace("\\", "/")
        if (
            not name
            or "\x00" in name
            or info.is_dir()
            or normalized.startswith("/")
            or normalized.startswith("//")
            or re.match(r"^[A-Za-z]:", normalized)
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
            or "/" in normalized
        ):
            raise BuildError(f"{label} contains unsafe ZIP entry: {name!r}")
        key = normalized.casefold()
        if key in seen:
            raise BuildError(
                f"{label} contains duplicate or case-colliding entries: "
                f"{seen[key]!r}, {name!r}"
            )
        seen[key] = name
        files.append(info)
    return files


def normalize_text_line_endings(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def checkout_matches_tracked_blob(
    checkout_bytes: bytes,
    blob_bytes: bytes,
) -> bool:
    return normalize_text_line_endings(
        checkout_bytes
    ) == normalize_text_line_endings(blob_bytes)


def git_bytes(*arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode(
            "utf-8", errors="replace"
        ).strip()
        raise BuildError(
            f"git {' '.join(arguments)} failed: {diagnostic}"
        )
    return completed.stdout


def git_text(*arguments: str) -> str:
    return git_bytes(*arguments).decode("utf-8").strip()


def read_tracked_blob(path: Path) -> bytes:
    try:
        relative = path.resolve().relative_to(
            ROOT.resolve()
        ).as_posix()
    except ValueError as exc:
        raise BuildError(
            f"Handoff source is outside repository root: {path}"
        ) from exc
    return git_bytes("show", f"HEAD:{relative}")


def verified_tracked_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise BuildError(f"Handoff source file is missing: {path}")
    checkout_bytes = path.read_bytes()
    blob_bytes = read_tracked_blob(path)
    if not checkout_matches_tracked_blob(checkout_bytes, blob_bytes):
        raise BuildError(
            "Working-tree handoff source differs from the committed Git blob "
            f"beyond line endings: {path}"
        )
    return blob_bytes


def deterministic_built_at() -> str:
    raw = os.environ.get("SOURCE_DATE_EPOCH", "1783900800")
    try:
        timestamp = int(raw)
    except ValueError as exc:
        raise BuildError("SOURCE_DATE_EPOCH must be an integer") from exc
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def source_identity() -> tuple[str, str | None]:
    head = git_text("rev-parse", "HEAD").lower()
    source_commit = os.environ.get("GITHUB_SHA", head).lower()
    if source_commit != head:
        raise BuildError(
            "GITHUB_SHA does not match the checked-out HEAD: "
            f"{source_commit} != {head}"
        )
    if len(source_commit) != 40 or any(
        char not in "0123456789abcdef" for char in source_commit
    ):
        raise BuildError(f"Invalid source commit: {source_commit}")
    return source_commit, os.environ.get("GITHUB_REF")


def parse_sha256sums(data: bytes, label: str) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"{label} is not UTF-8") from exc
    rows: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        try:
            digest, name = line.split("  ", 1)
        except ValueError as exc:
            raise BuildError(
                f"Invalid {label} line {line_number}: {line!r}"
            ) from exc
        digest = digest.lower()
        if len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise BuildError(
                f"Invalid SHA-256 in {label} line {line_number}"
            )
        if not name or name in rows:
            raise BuildError(
                f"Missing or duplicate filename in {label} line {line_number}"
            )
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise BuildError(f"Unsafe path in {label}: {name}")
        rows[name] = digest
    return rows


def inspect_operator_bundle(
    path: Path,
    expected_source_commit: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise BuildError(f"Operator bundle does not exist: {path}")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = validate_top_level_zip_infos(
                archive.infolist(), "Operator bundle"
            )
            names = [info.filename for info in infos]
            bad_member = archive.testzip()
            if bad_member:
                raise BuildError(
                    f"Operator bundle compressed data is corrupt: {bad_member}"
                )
            members = {name: archive.read(name) for name in names}
    except (OSError, zipfile.BadZipFile) as exc:
        raise BuildError(f"Operator bundle cannot be read: {exc}") from exc

    missing = sorted(OPERATOR_REQUIRED_MEMBERS - set(members))
    if missing:
        raise BuildError(
            f"Operator bundle is missing required members: {missing}"
        )

    sums = parse_sha256sums(
        members["SHA256SUMS.txt"], "operator SHA256SUMS.txt"
    )
    expected_sum_members = set(members) - {
        "SHA256SUMS.txt",
        "bundle-manifest.json",
    }
    if set(sums) != expected_sum_members:
        raise BuildError(
            "Operator SHA256SUMS coverage does not exactly match source members"
        )
    for name, expected in sorted(sums.items()):
        data = members.get(name)
        if data is None:
            raise BuildError(
                f"Operator SHA256SUMS lists a missing member: {name}"
            )
        actual = sha256_bytes(data)
        if actual != expected:
            raise BuildError(
                f"Operator member hash mismatch for {name}: "
                f"{actual} != {expected}"
            )

    try:
        manifest = json.loads(
            members["bundle-manifest.json"].decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"Operator manifest is invalid: {exc}") from exc
    if not isinstance(manifest, dict):
        raise BuildError("Operator manifest is not an object")
    if manifest.get("operator_version") != OPERATOR_VERSION:
        raise BuildError(
            "Operator version mismatch: "
            f"{manifest.get('operator_version')!r}"
        )
    if str(manifest.get("source_commit", "")).lower() != (
        expected_source_commit
    ):
        raise BuildError(
            "Operator bundle source_commit does not match handoff source: "
            f"{manifest.get('source_commit')} != {expected_source_commit}"
        )
    if manifest.get("authorization") != AUTHORIZATION:
        raise BuildError(
            "Operator manifest authorization boundary is not fail-closed"
        )

    return {
        "zip_sha256": sha256_file(path),
        "manifest": manifest,
        "readme_bytes": members[
            "README_moomoo_discovery_operator_ja.md"
        ],
        "member_names": sorted(members),
    }


def write_deterministic_zip(stage: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for path in sorted(
            stage.iterdir(), key=lambda item: item.name.lower()
        ):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def build_handoff(
    output_dir: Path,
    operator_bundle: Path,
) -> dict[str, Any]:
    source_commit, source_ref = source_identity()
    source_bytes = {
        name: verified_tracked_bytes(SOURCE / name)
        for name in SOURCE_FILES
    }
    operator = inspect_operator_bundle(
        operator_bundle.resolve(), source_commit
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    stage = output_dir / HANDOFF_BASENAME
    if stage.exists():
        remove_tree(stage)
    stage.mkdir()

    for name, data in source_bytes.items():
        (stage / name).write_bytes(data)
    shutil.copyfile(operator_bundle, stage / OPERATOR_BUNDLE_NAME)
    (stage / "OPERATOR_README_ORIGINAL.md").write_bytes(
        operator["readme_bytes"]
    )

    payload_files = {
        path.name: sha256_file(path)
        for path in sorted(
            stage.iterdir(), key=lambda item: item.name.lower()
        )
        if path.is_file()
    }
    manifest = {
        "report_type": "moomoo_readonly_discovery_handoff_manifest",
        "schema_version": HANDOFF_FORMAT_VERSION,
        "handoff_format_version": HANDOFF_FORMAT_VERSION,
        "handoff_package_version": HANDOFF_VERSION,
        "handoff_version": HANDOFF_VERSION,
        "operator_version": OPERATOR_VERSION,
        "built_at": deterministic_built_at(),
        "source_commit": source_commit,
        "source_ref": source_ref,
        "source_bytes": "git_blob",
        "checkout_line_endings_ignored": True,
        "expected_checkout_head": source_commit,
        "expected_remote": EXPECTED_REMOTE,
        "operator_bundle": {
            "name": OPERATOR_BUNDLE_NAME,
            "sha256": operator["zip_sha256"],
            "source_commit": str(
                operator["manifest"].get("source_commit")
            ).lower(),
            "source_ref": operator["manifest"].get("source_ref"),
        },
        "payload_files": payload_files,
        "authorization": AUTHORIZATION,
        "distribution_policy": {
            "authoritative_source": "github_actions_artifact_after_cross_shell_comparison",
            "production_use_requires_master_push_artifact": True,
            "pull_request_artifact_is_test_only": True,
        },
    }
    manifest_path = stage / "HANDOFF_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    checksum_targets = [
        path
        for path in sorted(
            stage.iterdir(), key=lambda item: item.name.lower()
        )
        if path.is_file()
    ]
    checksum_path = stage / "HANDOFF_SHA256SUMS.txt"
    checksum_path.write_text(
        "".join(
            f"{sha256_file(path)}  {path.name}\n"
            for path in checksum_targets
        ),
        encoding="utf-8",
        newline="\n",
    )

    zip_path = output_dir / f"{HANDOFF_BASENAME}.zip"
    write_deterministic_zip(stage, zip_path)
    return {
        "report_type": "moomoo_readonly_discovery_handoff_build",
        "handoff_version": HANDOFF_VERSION,
        "operator_version": OPERATOR_VERSION,
        "source_commit": source_commit,
        "source_ref": source_ref,
        "expected_checkout_head": source_commit,
        "operator_bundle_sha256": operator["zip_sha256"],
        "zip": str(zip_path),
        "zip_sha256": sha256_file(zip_path),
        "stage": str(stage),
        "member_count": len(
            [path for path in stage.iterdir() if path.is_file()]
        ),
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-bundle", required=True)
    parser.add_argument("--output-dir", default=str(DEFAULT_DIST))
    parser.add_argument("--json-output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    result = build_handoff(
        Path(args.output_dir).resolve(),
        Path(args.operator_bundle).resolve(),
    )
    rendered = json.dumps(
        result, ensure_ascii=False, indent=2
    ) + "\n"
    if args.json_output:
        output = Path(args.json_output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            rendered,
            encoding="utf-8",
            newline="\n",
        )
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
