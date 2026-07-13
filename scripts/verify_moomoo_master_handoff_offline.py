#!/usr/bin/env python3
"""Independently verify a moomoo v1.2.2 read-only discovery handoff.

The verifier performs no extraction and never executes package content. It accepts
either the inner handoff ZIP or the single-file GitHub Actions artifact wrapper.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

HANDOFF_VERSION = "1.2.2"
HANDOFF_FORMAT_VERSION = 1
OPERATOR_VERSION = "1.2.2"
OPERATOR_BUNDLE_NAME = "moomoo_production_discovery_operator_v4_v1.2.2.zip"
EXPECTED_REMOTE = "https://github.com/kaenozu/moomoo-jp-momentum.git"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MIB = 1024 * 1024
MAX_INPUT_BYTES = 10 * MIB
MAX_HANDOFF_BYTES = 5 * MIB
MAX_OPERATOR_BYTES = 5 * MIB
MAX_MEMBER_BYTES = 2 * MIB
MAX_TOTAL_UNCOMPRESSED_BYTES = 20 * MIB
MAX_COMPRESSION_RATIO = 100.0
AUTHORIZATION = {
    "production_readiness": "BLOCKED",
    "preflight_authorized": False,
    "production_drill_authorized": False,
    "cutover_authorized": False,
}
DISTRIBUTION_POLICY = {
    "authoritative_source": "github_actions_artifact_after_cross_shell_comparison",
    "production_use_requires_master_push_artifact": True,
    "pull_request_artifact_is_test_only": True,
}
HANDOFF_MEMBERS = {
    "EVIDENCE_REVIEW_CHECKLIST.md",
    "HANDOFF_MANIFEST.json",
    "HANDOFF_SHA256SUMS.txt",
    "LOCAL_AGENT_PROMPT.md",
    OPERATOR_BUNDLE_NAME,
    "OPERATOR_README_ORIGINAL.md",
    "README_FIRST.md",
    "run-readonly-discovery.ps1",
    "verify-handoff.ps1",
}
OPERATOR_MEMBERS = {
    "bundle-manifest.json",
    "moomoo_discovery_operator.py",
    "moomoo_discovery_v4_common.ps1",
    "moomoo_discovery_v4_gate.ps1",
    "moomoo_discovery_v4_runtime.ps1",
    "moomoo_discovery_v4_storage.ps1",
    "moomoo_operator_cli.py",
    "moomoo_operator_common.py",
    "moomoo_operator_review.py",
    "moomoo_production_readonly_discovery_v4.ps1",
    "README_moomoo_discovery_operator_ja.md",
    "run_moomoo_discovery_operator_tests.ps1",
    "SHA256SUMS.txt",
    "test_bundle_builder.py",
    "test_moomoo_discovery_operator.py",
    "test_moomoo_operator_common_errors.py",
    "validate_moomoo_discovery_operator.py",
}


class VerificationError(RuntimeError):
    """Raised when an artifact violates the frozen handoff contract."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_remote(value: str) -> str:
    normalized = value.strip().rstrip("/").casefold()
    return normalized[:-4] if normalized.endswith(".git") else normalized


def validate_expected_digest(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not HEX64.fullmatch(normalized):
        raise VerificationError(f"{label} must be an exact lowercase SHA-256")
    return normalized


def validate_zip_infos(
    infos: list[zipfile.ZipInfo],
    label: str,
    *,
    max_members: int,
    max_member_bytes: int,
    max_total_uncompressed_bytes: int = MAX_TOTAL_UNCOMPRESSED_BYTES,
    max_compression_ratio: float = MAX_COMPRESSION_RATIO,
) -> dict[str, zipfile.ZipInfo]:
    if len(infos) > max_members:
        raise VerificationError(
            f"{label} contains too many members: {len(infos)} > {max_members}"
        )

    result: dict[str, zipfile.ZipInfo] = {}
    seen_casefold: dict[str, str] = {}
    total_uncompressed = 0
    for info in infos:
        name = info.filename
        normalized = name.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        unsafe = (
            not name
            or "\x00" in name
            or info.is_dir()
            or normalized.startswith(("/", "//"))
            or bool(re.match(r"^[A-Za-z]:", normalized))
            or len(parts) != 1
            or parts[0] in {"", ".", ".."}
        )
        if unsafe:
            raise VerificationError(f"{label} contains unsafe entry: {name!r}")
        if info.file_size > max_member_bytes:
            raise VerificationError(
                f"{label} member is too large: {name!r} "
                f"{info.file_size} > {max_member_bytes}"
            )
        total_uncompressed += info.file_size
        if total_uncompressed > max_total_uncompressed_bytes:
            raise VerificationError(
                f"{label} uncompressed size exceeds limit: "
                f"{total_uncompressed} > {max_total_uncompressed_bytes}"
            )
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > max_compression_ratio:
            raise VerificationError(
                f"{label} compression ratio exceeds limit for {name!r}: "
                f"{ratio:.1f} > {max_compression_ratio:.1f}"
            )
        key = normalized.casefold()
        if key in seen_casefold:
            raise VerificationError(
                f"{label} contains duplicate or case-colliding entries: "
                f"{seen_casefold[key]!r}, {name!r}"
            )
        seen_casefold[key] = name
        result[name] = info
    return result


def open_verified_zip(
    data: bytes,
    label: str,
    *,
    max_archive_bytes: int,
    max_members: int,
    max_member_bytes: int,
) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    if len(data) > max_archive_bytes:
        raise VerificationError(
            f"{label} exceeds compressed size limit: {len(data)} > {max_archive_bytes}"
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
        infos = validate_zip_infos(
            archive.infolist(),
            label,
            max_members=max_members,
            max_member_bytes=max_member_bytes,
        )
        bad_member = archive.testzip()
        if bad_member is not None:
            archive.close()
            raise VerificationError(
                f"{label} compressed data is corrupt: {bad_member}"
            )
        return archive, infos
    except (OSError, zipfile.BadZipFile) as exc:
        raise VerificationError(f"{label} is not a readable ZIP: {exc}") from exc


def require_exact_members(
    actual: set[str], expected: set[str], label: str
) -> None:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise VerificationError(
            f"{label} member set mismatch; missing={missing}, unexpected={unexpected}"
        )


def parse_json(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"{label} must be a JSON object")
    return value


def parse_sha256sums(data: bytes, label: str) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError(f"{label} is not UTF-8") from exc
    rows: dict[str, str] = {}
    seen_casefold: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", line)
        if match is None:
            raise VerificationError(f"{label} line {line_number} is invalid")
        digest = match.group(1).lower()
        name = match.group(2)
        normalized = name.replace("\\", "/")
        if len(PurePosixPath(normalized).parts) != 1:
            raise VerificationError(f"{label} contains unsafe path: {name!r}")
        key = normalized.casefold()
        if key in seen_casefold:
            raise VerificationError(f"{label} contains duplicate entry: {name!r}")
        seen_casefold.add(key)
        rows[name] = digest
    return rows


def verify_checksum_coverage(
    archive: zipfile.ZipFile,
    sums_name: str,
    expected_names: set[str],
    label: str,
) -> None:
    sums = parse_sha256sums(archive.read(sums_name), f"{label} {sums_name}")
    require_exact_members(set(sums), expected_names, f"{label} checksum coverage")
    for name, expected in sorted(sums.items()):
        actual = sha256_bytes(archive.read(name))
        if actual != expected:
            raise VerificationError(
                f"{label} member hash mismatch for {name}: {actual} != {expected}"
            )


def verify_operator_bundle(
    data: bytes,
    expected_source_commit: str,
    require_master: bool,
) -> dict[str, Any]:
    archive, infos = open_verified_zip(
        data,
        "nested operator ZIP",
        max_archive_bytes=MAX_OPERATOR_BYTES,
        max_members=len(OPERATOR_MEMBERS),
        max_member_bytes=MAX_MEMBER_BYTES,
    )
    try:
        require_exact_members(set(infos), OPERATOR_MEMBERS, "nested operator ZIP")
        verify_checksum_coverage(
            archive,
            "SHA256SUMS.txt",
            OPERATOR_MEMBERS - {"SHA256SUMS.txt", "bundle-manifest.json"},
            "nested operator ZIP",
        )
        manifest = parse_json(
            archive.read("bundle-manifest.json"), "nested operator manifest"
        )
        if manifest.get("report_type") != "moomoo_discovery_operator_bundle_manifest":
            raise VerificationError("nested operator report_type mismatch")
        if manifest.get("operator_version") != OPERATOR_VERSION:
            raise VerificationError("nested operator version mismatch")
        source_commit = str(manifest.get("source_commit", "")).lower()
        if source_commit != expected_source_commit:
            raise VerificationError(
                "nested operator source_commit does not match handoff source_commit"
            )
        if require_master and manifest.get("source_ref") != "refs/heads/master":
            raise VerificationError("nested operator source_ref is not master")
        if manifest.get("authorization") != AUTHORIZATION:
            raise VerificationError("nested operator authorization is not fail-closed")
        files = manifest.get("files")
        expected_files = OPERATOR_MEMBERS - {"bundle-manifest.json"}
        if not isinstance(files, dict) or set(files) != expected_files:
            raise VerificationError("nested operator manifest files coverage mismatch")
        for name, expected in files.items():
            if not isinstance(expected, str) or not HEX64.fullmatch(expected.lower()):
                raise VerificationError(
                    f"nested operator manifest digest is invalid for {name}"
                )
            actual = sha256_bytes(archive.read(name))
            if actual != expected.lower():
                raise VerificationError(
                    f"nested operator manifest hash mismatch for {name}"
                )
        return {
            "sha256": sha256_bytes(data),
            "source_commit": source_commit,
            "source_ref": manifest.get("source_ref"),
            "member_count": len(infos),
        }
    finally:
        archive.close()


def unwrap_handoff(input_data: bytes) -> tuple[bytes, dict[str, Any]]:
    archive, infos = open_verified_zip(
        input_data,
        "input ZIP",
        max_archive_bytes=MAX_INPUT_BYTES,
        max_members=len(HANDOFF_MEMBERS),
        max_member_bytes=MAX_HANDOFF_BYTES,
    )
    try:
        names = set(infos)
        if names == HANDOFF_MEMBERS:
            return input_data, {"wrapper": False, "wrapper_member": None}
        matching = [
            name
            for name in names
            if re.fullmatch(r"moomoo-readonly-discovery-handoff-v1\.2\.2\.zip", name)
        ]
        if len(names) != 1 or len(matching) != 1:
            raise VerificationError(
                "input ZIP is neither the handoff nor a single-file Actions wrapper"
            )
        name = matching[0]
        return archive.read(name), {"wrapper": True, "wrapper_member": name}
    finally:
        archive.close()


def verify_handoff(
    input_path: Path,
    *,
    expected_input_sha256: str | None = None,
    expected_handoff_sha256: str | None = None,
    expected_source_commit: str | None = None,
    require_master: bool = True,
) -> dict[str, Any]:
    if not input_path.is_file():
        raise VerificationError(f"input file does not exist: {input_path}")
    input_size = input_path.stat().st_size
    if input_size > MAX_INPUT_BYTES:
        raise VerificationError(
            f"input file exceeds size limit: {input_size} > {MAX_INPUT_BYTES}"
        )
    expected_input_sha256 = validate_expected_digest(
        expected_input_sha256, "expected input SHA-256"
    )
    expected_handoff_sha256 = validate_expected_digest(
        expected_handoff_sha256, "expected handoff SHA-256"
    )
    if expected_source_commit is not None:
        expected_source_commit = expected_source_commit.strip().lower()
        if not HEX40.fullmatch(expected_source_commit):
            raise VerificationError("expected source commit must be a 40-character SHA")

    input_data = input_path.read_bytes()
    input_sha256 = sha256_bytes(input_data)
    if expected_input_sha256 and input_sha256 != expected_input_sha256:
        raise VerificationError(
            f"input SHA-256 mismatch: {input_sha256} != {expected_input_sha256}"
        )
    handoff_data, wrapper = unwrap_handoff(input_data)
    handoff_sha256 = sha256_bytes(handoff_data)
    if expected_handoff_sha256 and handoff_sha256 != expected_handoff_sha256:
        raise VerificationError(
            f"handoff SHA-256 mismatch: {handoff_sha256} != {expected_handoff_sha256}"
        )

    archive, infos = open_verified_zip(
        handoff_data,
        "handoff ZIP",
        max_archive_bytes=MAX_HANDOFF_BYTES,
        max_members=len(HANDOFF_MEMBERS),
        max_member_bytes=MAX_OPERATOR_BYTES,
    )
    try:
        require_exact_members(set(infos), HANDOFF_MEMBERS, "handoff ZIP")
        verify_checksum_coverage(
            archive,
            "HANDOFF_SHA256SUMS.txt",
            HANDOFF_MEMBERS - {"HANDOFF_SHA256SUMS.txt"},
            "handoff ZIP",
        )
        manifest = parse_json(
            archive.read("HANDOFF_MANIFEST.json"), "handoff manifest"
        )
        if manifest.get("report_type") != "moomoo_readonly_discovery_handoff_manifest":
            raise VerificationError("handoff report_type mismatch")
        if manifest.get("schema_version") != HANDOFF_FORMAT_VERSION:
            raise VerificationError("handoff schema_version mismatch")
        if manifest.get("handoff_format_version") != HANDOFF_FORMAT_VERSION:
            raise VerificationError("handoff format version mismatch")
        if manifest.get("handoff_package_version") != HANDOFF_VERSION:
            raise VerificationError("handoff package version mismatch")
        if manifest.get("handoff_version") != HANDOFF_VERSION:
            raise VerificationError("handoff version mismatch")
        if manifest.get("operator_version") != OPERATOR_VERSION:
            raise VerificationError("handoff operator version mismatch")
        source_commit = str(manifest.get("source_commit", "")).lower()
        checkout_head = str(manifest.get("expected_checkout_head", "")).lower()
        if not HEX40.fullmatch(source_commit) or source_commit != checkout_head:
            raise VerificationError(
                "handoff source_commit and expected_checkout_head are not one exact SHA"
            )
        if expected_source_commit and source_commit != expected_source_commit:
            raise VerificationError(
                f"handoff source commit mismatch: {source_commit} != {expected_source_commit}"
            )
        if require_master and manifest.get("source_ref") != "refs/heads/master":
            raise VerificationError("handoff source_ref is not refs/heads/master")
        if normalize_remote(str(manifest.get("expected_remote", ""))) != normalize_remote(
            EXPECTED_REMOTE
        ):
            raise VerificationError("handoff expected_remote mismatch")
        if manifest.get("authorization") != AUTHORIZATION:
            raise VerificationError("handoff authorization is not fail-closed")
        if manifest.get("distribution_policy") != DISTRIBUTION_POLICY:
            raise VerificationError("handoff distribution policy mismatch")

        payload_files = manifest.get("payload_files")
        expected_payload = HANDOFF_MEMBERS - {
            "HANDOFF_MANIFEST.json",
            "HANDOFF_SHA256SUMS.txt",
        }
        if not isinstance(payload_files, dict) or set(payload_files) != expected_payload:
            raise VerificationError("handoff manifest payload coverage mismatch")
        for name, expected in payload_files.items():
            if not isinstance(expected, str) or not HEX64.fullmatch(expected.lower()):
                raise VerificationError(f"handoff manifest digest is invalid for {name}")
            actual = sha256_bytes(archive.read(name))
            if actual != expected.lower():
                raise VerificationError(f"handoff manifest hash mismatch for {name}")

        operator = manifest.get("operator_bundle")
        if not isinstance(operator, dict):
            raise VerificationError("handoff operator_bundle is missing")
        if operator.get("name") != OPERATOR_BUNDLE_NAME:
            raise VerificationError("handoff operator bundle name mismatch")
        operator_data = archive.read(OPERATOR_BUNDLE_NAME)
        operator_sha256 = sha256_bytes(operator_data)
        if operator.get("sha256") != operator_sha256:
            raise VerificationError("handoff operator bundle SHA-256 mismatch")
        if str(operator.get("source_commit", "")).lower() != source_commit:
            raise VerificationError("handoff operator source_commit mismatch")
        if require_master and operator.get("source_ref") != "refs/heads/master":
            raise VerificationError("handoff operator source_ref is not master")
        operator_report = verify_operator_bundle(
            operator_data, source_commit, require_master=require_master
        )

        return {
            "report_type": "moomoo_master_handoff_offline_verification",
            "status": "PASS",
            "input": str(input_path),
            "input_sha256": input_sha256,
            "actions_wrapper": wrapper["wrapper"],
            "wrapper_member": wrapper["wrapper_member"],
            "handoff_sha256": handoff_sha256,
            "source_commit": source_commit,
            "source_ref": manifest.get("source_ref"),
            "expected_remote": manifest.get("expected_remote"),
            "handoff_member_count": len(infos),
            "operator": operator_report,
            "authorization": AUTHORIZATION,
            "resource_limits": {
                "max_input_bytes": MAX_INPUT_BYTES,
                "max_handoff_bytes": MAX_HANDOFF_BYTES,
                "max_operator_bytes": MAX_OPERATOR_BYTES,
                "max_member_bytes": MAX_MEMBER_BYTES,
                "max_total_uncompressed_bytes": MAX_TOTAL_UNCOMPRESSED_BYTES,
                "max_compression_ratio": MAX_COMPRESSION_RATIO,
            },
            "production_execution_performed": False,
        }
    finally:
        archive.close()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="inner handoff ZIP or Actions artifact wrapper ZIP")
    parser.add_argument("--expected-input-sha256")
    parser.add_argument("--expected-handoff-sha256")
    parser.add_argument("--expected-source-commit")
    parser.add_argument(
        "--allow-validation-ref",
        action="store_true",
        help="allow a pull-request or other validation ref instead of master",
    )
    parser.add_argument("--output", help="write the JSON report without overwriting")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        report = verify_handoff(
            Path(args.input).resolve(),
            expected_input_sha256=args.expected_input_sha256,
            expected_handoff_sha256=args.expected_handoff_sha256,
            expected_source_commit=args.expected_source_commit,
            require_master=not args.allow_validation_ref,
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = Path(args.output).resolve()
            if output.exists():
                raise VerificationError(f"output already exists: {output}")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8", newline="\n")
        print(rendered, end="")
        return 0
    except VerificationError as exc:
        print(
            json.dumps(
                {
                    "report_type": "moomoo_master_handoff_offline_verification",
                    "status": "FAIL",
                    "error": str(exc),
                    "production_execution_performed": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
