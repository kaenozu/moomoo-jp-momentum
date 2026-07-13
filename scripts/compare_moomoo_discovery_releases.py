#!/usr/bin/env python3
"""Validate or byte-compare production-discovery release ZIPs."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Sequence

EXPECTED_AUTHORIZATION = {
    "production_readiness": "BLOCKED",
    "preflight_authorized": False,
    "production_drill_authorized": False,
    "cutover_authorized": False,
}
OPERATOR_VERSION = "1.2.2"
HANDOFF_VERSION = "1.2.2"
OPERATOR_ZIP = "moomoo_production_discovery_operator_v4_v1.2.2.zip"
HANDOFF_ZIP = "moomoo-readonly-discovery-handoff-v1.2.2.zip"
RELEASE_VERIFIER = "compare_moomoo_discovery_releases.py"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_OPERATOR_SOURCE_MEMBERS = {
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
    "test_moomoo_operator_common_errors.py",
    "test_bundle_builder.py",
    "run_moomoo_discovery_operator_tests.ps1",
    "validate_moomoo_discovery_operator.py",
    "README_moomoo_discovery_operator_ja.md",
}
EXPECTED_OPERATOR_MEMBERS = EXPECTED_OPERATOR_SOURCE_MEMBERS | {
    "SHA256SUMS.txt",
    "bundle-manifest.json",
}
EXPECTED_HANDOFF_MEMBERS = {
    "README_FIRST.md",
    "LOCAL_AGENT_PROMPT.md",
    "EVIDENCE_REVIEW_CHECKLIST.md",
    "OPERATOR_README_ORIGINAL.md",
    "run-readonly-discovery.ps1",
    "verify-handoff.ps1",
    OPERATOR_ZIP,
    "HANDOFF_MANIFEST.json",
    "HANDOFF_SHA256SUMS.txt",
}
EXPECTED_RELEASE_MEMBERS = {
    OPERATOR_ZIP,
    HANDOFF_ZIP,
    "human-validation.schema.json",
    "human-validation.template.json",
    "validate_moomoo_human_validation.py",
    "README_moomoo_human_validation_ja.md",
    RELEASE_VERIFIER,
    "release-manifest.json",
    "SHA256SUMS.txt",
}
EXPECTED_RELEASE_SUM_MEMBERS = EXPECTED_RELEASE_MEMBERS - {
    "SHA256SUMS.txt"
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_sums(data: bytes, label: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line_number, line in enumerate(
        data.decode("utf-8-sig").splitlines(), start=1
    ):
        if not line:
            continue
        try:
            digest, name = line.split("  ", 1)
        except ValueError as exc:
            raise ValueError(
                f"Invalid {label} line {line_number}: {line!r}"
            ) from exc
        digest = digest.lower()
        if HEX64.fullmatch(digest) is None:
            raise ValueError(
                f"Invalid SHA-256 in {label} line {line_number}"
            )
        if not name or name in rows:
            raise ValueError(
                f"Missing or duplicate filename in {label} line {line_number}"
            )
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError(
                f"Unsafe filename in {label} line {line_number}: {name}"
            )
        rows[name] = digest
    return rows


def read_zip_members(
    source: Path | io.BytesIO,
) -> tuple[list[str], dict[str, bytes], list[str], str | None]:
    with zipfile.ZipFile(source, "r") as archive:
        names = [
            info.filename
            for info in archive.infolist()
            if not info.is_dir()
        ]
        duplicates = sorted(
            {name for name in names if names.count(name) > 1}
        )
        bad_member = archive.testzip()
        members = {
            name: archive.read(name)
            for name in names
            if name not in duplicates
        }
    return names, members, duplicates, bad_member


def validate_hash_manifest(
    members: dict[str, bytes],
    sums_name: str,
    expected_names: set[str],
) -> list[str]:
    try:
        sums = parse_sums(members[sums_name], sums_name)
    except (KeyError, UnicodeError, ValueError) as exc:
        return [f"{sums_name} invalid: {exc}"]
    errors: list[str] = []
    if set(sums) != expected_names:
        errors.append(
            f"{sums_name} member set mismatch: "
            f"expected {sorted(expected_names)!r}, got {sorted(sums)!r}"
        )
    for name, expected in sorted(sums.items()):
        data = members.get(name)
        if data is None:
            errors.append(f"missing listed member: {name}")
        elif sha256_bytes(data) != expected:
            errors.append(f"hash mismatch for {name}")
    return errors


def inspect_operator_bundle(
    data: bytes,
    expected_commit: str,
    expected_ref: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sha256": sha256_bytes(data),
        "member_names": [],
        "duplicate_entries": [],
        "bad_compressed_member": None,
        "internal_hash_errors": [],
        "manifest": None,
        "valid": False,
        "errors": [],
    }
    try:
        names, members, duplicates, bad_member = read_zip_members(
            io.BytesIO(data)
        )
    except (OSError, zipfile.BadZipFile) as exc:
        result["errors"].append(
            f"nested operator ZIP read failed: {exc}"
        )
        return result
    result["member_names"] = sorted(names)
    result["duplicate_entries"] = duplicates
    result["bad_compressed_member"] = bad_member
    if duplicates:
        result["errors"].append(
            "nested operator ZIP has duplicate entries"
        )
    if bad_member:
        result["errors"].append(
            "nested operator compressed data test failed"
        )
    if set(names) != EXPECTED_OPERATOR_MEMBERS:
        result["errors"].append(
            "nested operator member set mismatch: "
            f"expected {sorted(EXPECTED_OPERATOR_MEMBERS)!r}, "
            f"got {sorted(set(names))!r}"
        )
    hash_errors = validate_hash_manifest(
        members,
        "SHA256SUMS.txt",
        EXPECTED_OPERATOR_SOURCE_MEMBERS,
    )
    result["internal_hash_errors"] = hash_errors
    result["errors"].extend(
        f"nested operator {error}" for error in hash_errors
    )
    try:
        manifest = json.loads(
            members["bundle-manifest.json"].decode("utf-8")
        )
        result["manifest"] = manifest
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        result["errors"].append(
            f"nested operator manifest invalid: {exc}"
        )
        manifest = None
    if isinstance(manifest, dict):
        if manifest.get("report_type") != (
            "moomoo_discovery_operator_bundle_manifest"
        ):
            result["errors"].append(
                "nested operator manifest report_type is invalid"
            )
        if manifest.get("operator_version") != OPERATOR_VERSION:
            result["errors"].append(
                f"nested operator version is not {OPERATOR_VERSION}"
            )
        if manifest.get("source_commit") != expected_commit:
            result["errors"].append(
                "nested operator source_commit differs from release"
            )
        if manifest.get("source_ref") != expected_ref:
            result["errors"].append(
                "nested operator source_ref differs from release"
            )
        if manifest.get("source_bytes") != "git_blob":
            result["errors"].append(
                "nested operator source_bytes is not git_blob"
            )
        if manifest.get("authorization") != EXPECTED_AUTHORIZATION:
            result["errors"].append(
                "nested operator authorization is not fail-closed"
            )
        files = manifest.get("files")
        expected_files = EXPECTED_OPERATOR_MEMBERS - {
            "bundle-manifest.json"
        }
        if not isinstance(files, dict) or set(files) != expected_files:
            result["errors"].append(
                "nested operator manifest files set is invalid"
            )
        elif any(
            files.get(name) != sha256_bytes(members[name])
            for name in expected_files
        ):
            result["errors"].append(
                "nested operator manifest file hash mismatch"
            )
    result["valid"] = not result["errors"]
    return result


def inspect_handoff_bundle(
    data: bytes,
    expected_commit: str,
    expected_ref: str | None,
    release_operator_data: bytes,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sha256": sha256_bytes(data),
        "member_names": [],
        "duplicate_entries": [],
        "bad_compressed_member": None,
        "internal_hash_errors": [],
        "manifest": None,
        "nested_operator_sha256": None,
        "valid": False,
        "errors": [],
    }
    try:
        names, members, duplicates, bad_member = read_zip_members(
            io.BytesIO(data)
        )
    except (OSError, zipfile.BadZipFile) as exc:
        result["errors"].append(
            f"nested handoff ZIP read failed: {exc}"
        )
        return result
    result["member_names"] = sorted(names)
    result["duplicate_entries"] = duplicates
    result["bad_compressed_member"] = bad_member
    if duplicates:
        result["errors"].append(
            "nested handoff ZIP has duplicate entries"
        )
    if bad_member:
        result["errors"].append(
            "nested handoff compressed data test failed"
        )
    if set(names) != EXPECTED_HANDOFF_MEMBERS:
        result["errors"].append(
            "nested handoff member set mismatch: "
            f"expected {sorted(EXPECTED_HANDOFF_MEMBERS)!r}, "
            f"got {sorted(set(names))!r}"
        )
    hash_errors = validate_hash_manifest(
        members,
        "HANDOFF_SHA256SUMS.txt",
        EXPECTED_HANDOFF_MEMBERS - {"HANDOFF_SHA256SUMS.txt"},
    )
    result["internal_hash_errors"] = hash_errors
    result["errors"].extend(
        f"nested handoff {error}" for error in hash_errors
    )
    try:
        manifest = json.loads(
            members["HANDOFF_MANIFEST.json"].decode("utf-8")
        )
        result["manifest"] = manifest
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        result["errors"].append(
            f"nested handoff manifest invalid: {exc}"
        )
        manifest = None
    operator_data = members.get(OPERATOR_ZIP)
    if operator_data is None:
        result["errors"].append(
            "nested handoff operator ZIP is missing"
        )
    else:
        operator_sha = sha256_bytes(operator_data)
        result["nested_operator_sha256"] = operator_sha
        if operator_data != release_operator_data:
            result["errors"].append(
                "nested handoff operator ZIP differs from release operator ZIP"
            )
    if isinstance(manifest, dict):
        if manifest.get("report_type") != (
            "moomoo_readonly_discovery_handoff_manifest"
        ):
            result["errors"].append(
                "nested handoff manifest report_type is invalid"
            )
        if manifest.get("schema_version") != 1:
            result["errors"].append(
                "nested handoff schema_version is invalid"
            )
        if manifest.get("handoff_version") != HANDOFF_VERSION:
            result["errors"].append(
                f"nested handoff version is not {HANDOFF_VERSION}"
            )
        if manifest.get("operator_version") != OPERATOR_VERSION:
            result["errors"].append(
                f"nested handoff operator version is not {OPERATOR_VERSION}"
            )
        if manifest.get("source_commit") != expected_commit:
            result["errors"].append(
                "nested handoff source_commit differs from release"
            )
        if manifest.get("source_ref") != expected_ref:
            result["errors"].append(
                "nested handoff source_ref differs from release"
            )
        if manifest.get("expected_checkout_head") != expected_commit:
            result["errors"].append(
                "nested handoff expected_checkout_head differs from release"
            )
        if manifest.get("source_bytes") != "git_blob":
            result["errors"].append(
                "nested handoff source_bytes is not git_blob"
            )
        if manifest.get("authorization") != EXPECTED_AUTHORIZATION:
            result["errors"].append(
                "nested handoff authorization is not fail-closed"
            )
        operator = manifest.get("operator_bundle")
        if not isinstance(operator, dict):
            result["errors"].append(
                "nested handoff operator metadata is missing"
            )
        elif operator_data is not None:
            if operator.get("name") != OPERATOR_ZIP:
                result["errors"].append(
                    "nested handoff operator filename is invalid"
                )
            if operator.get("sha256") != sha256_bytes(operator_data):
                result["errors"].append(
                    "nested handoff operator SHA-256 is invalid"
                )
            if operator.get("source_commit") != expected_commit:
                result["errors"].append(
                    "nested handoff operator source_commit differs"
                )
        payload_files = manifest.get("payload_files")
        expected_payload = EXPECTED_HANDOFF_MEMBERS - {
            "HANDOFF_SHA256SUMS.txt",
            "HANDOFF_MANIFEST.json",
        }
        if not isinstance(payload_files, dict) or set(payload_files) != expected_payload:
            result["errors"].append(
                "nested handoff payload_files set is invalid"
            )
        elif any(
            payload_files.get(name) != sha256_bytes(members[name])
            for name in expected_payload
        ):
            result["errors"].append(
                "nested handoff payload_files hash mismatch"
            )
        policy = manifest.get("distribution_policy")
        if not isinstance(policy, dict) or policy.get(
            "production_eligible_only_inside_canonical_release"
        ) is not True:
            result["errors"].append(
                "nested handoff distribution policy is not canonical-release-only"
            )
    result["valid"] = not result["errors"]
    return result


def inspect_release(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "zip_sha256": None,
        "duplicate_entries": [],
        "bad_compressed_member": None,
        "member_names": [],
        "internal_hash_errors": [],
        "manifest": None,
        "operator_bundle": None,
        "readonly_handoff": None,
        "members": {},
        "valid": False,
        "errors": [],
    }
    if not path.is_file():
        result["errors"].append("release ZIP does not exist")
        return result
    result["zip_sha256"] = sha256_file(path)
    try:
        names, members, duplicates, bad_member = read_zip_members(path)
    except (OSError, zipfile.BadZipFile) as exc:
        result["errors"].append(f"ZIP read failed: {exc}")
        return result
    result["duplicate_entries"] = duplicates
    result["member_names"] = sorted(names)
    result["bad_compressed_member"] = bad_member
    result["members"] = {
        name: sha256_bytes(data) for name, data in members.items()
    }
    if duplicates:
        result["errors"].append("duplicate ZIP entries")
    if bad_member:
        result["errors"].append("compressed data test failed")
    if set(names) != EXPECTED_RELEASE_MEMBERS:
        result["errors"].append(
            "release member set mismatch: "
            f"expected {sorted(EXPECTED_RELEASE_MEMBERS)!r}, "
            f"got {sorted(set(names))!r}"
        )
    hash_errors = validate_hash_manifest(
        members,
        "SHA256SUMS.txt",
        EXPECTED_RELEASE_SUM_MEMBERS,
    )
    result["internal_hash_errors"] = hash_errors
    result["errors"].extend(hash_errors)
    try:
        manifest = json.loads(
            members["release-manifest.json"].decode("utf-8")
        )
        result["manifest"] = manifest
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        result["errors"].append(
            f"release manifest invalid: {exc}"
        )
        manifest = None
    if isinstance(manifest, dict):
        if manifest.get("report_type") != (
            "moomoo_discovery_release_manifest"
        ):
            result["errors"].append(
                "unexpected release manifest report_type"
            )
        if manifest.get("release_format_version") != 1:
            result["errors"].append(
                "unexpected release format version"
            )
        if manifest.get("operator_version") != OPERATOR_VERSION:
            result["errors"].append("unexpected operator version")
        if manifest.get("handoff_version") != HANDOFF_VERSION:
            result["errors"].append("unexpected handoff version")
        if manifest.get("source_bytes") != "git_blob":
            result["errors"].append(
                "release source_bytes is not git_blob"
            )
        if manifest.get("authorization") != EXPECTED_AUTHORIZATION:
            result["errors"].append(
                "release authorization boundary is not fail-closed"
            )
        if manifest.get("separate_approval_required") is not True:
            result["errors"].append(
                "release does not require a separate approval"
            )
        commit = manifest.get("source_commit")
        source_ref = manifest.get("source_ref")
        if not isinstance(commit, str) or HEX40.fullmatch(commit) is None:
            result["errors"].append("invalid source_commit")
            commit = ""
        candidate = manifest.get("release_candidate") is True
        expected_status = (
            "MASTER_RELEASE_CANDIDATE"
            if candidate
            else "VALIDATION_ONLY"
        )
        if manifest.get("distribution_status") != expected_status:
            result["errors"].append(
                "distribution_status is inconsistent"
            )
        if candidate and (
            source_ref != "refs/heads/master"
            or manifest.get("source_event") != "push"
        ):
            result["errors"].append(
                "master release candidate has invalid source identity"
            )
        expected_human = {
            "schema": "human-validation.schema.json",
            "template": "human-validation.template.json",
            "validator": "validate_moomoo_human_validation.py",
            "readme": "README_moomoo_human_validation_ja.md",
            "release_verifier": RELEASE_VERIFIER,
            "outputs": [
                "06-human-validation.json",
                "07-preflight-eligibility.json",
            ],
        }
        if manifest.get("human_validation") != expected_human:
            result["errors"].append(
                "human-validation manifest metadata is invalid"
            )
        operator_meta = manifest.get("operator_bundle")
        operator_data = members.get(OPERATOR_ZIP)
        if not isinstance(operator_meta, dict):
            result["errors"].append(
                "operator bundle metadata is missing"
            )
        elif operator_data is None:
            result["errors"].append("operator ZIP is missing")
        else:
            if operator_meta.get("filename") != OPERATOR_ZIP:
                result["errors"].append(
                    "operator bundle filename is invalid"
                )
            if operator_meta.get("sha256") != sha256_bytes(operator_data):
                result["errors"].append(
                    "operator ZIP SHA-256 does not match manifest"
                )
            if operator_meta.get("manifest_source_commit") != commit:
                result["errors"].append(
                    "operator manifest_source_commit differs from release"
                )
            if operator_meta.get("manifest_source_ref") != source_ref:
                result["errors"].append(
                    "operator manifest_source_ref differs from release"
                )
            operator_result = inspect_operator_bundle(
                operator_data, commit, source_ref
            )
            result["operator_bundle"] = operator_result
            if not operator_result["valid"]:
                result["errors"].append(
                    "operator bundle validation failed"
                )
        handoff_meta = manifest.get("readonly_handoff")
        handoff_data = members.get(HANDOFF_ZIP)
        if not isinstance(handoff_meta, dict):
            result["errors"].append(
                "readonly handoff metadata is missing"
            )
        elif handoff_data is None:
            result["errors"].append("readonly handoff ZIP is missing")
        elif operator_data is not None:
            if handoff_meta.get("filename") != HANDOFF_ZIP:
                result["errors"].append(
                    "readonly handoff filename is invalid"
                )
            if handoff_meta.get("sha256") != sha256_bytes(handoff_data):
                result["errors"].append(
                    "readonly handoff SHA-256 does not match manifest"
                )
            if handoff_meta.get("manifest_source_commit") != commit:
                result["errors"].append(
                    "handoff manifest_source_commit differs from release"
                )
            if handoff_meta.get("manifest_source_ref") != source_ref:
                result["errors"].append(
                    "handoff manifest_source_ref differs from release"
                )
            if handoff_meta.get("nested_operator_sha256") != sha256_bytes(operator_data):
                result["errors"].append(
                    "handoff nested operator SHA-256 differs from release operator"
                )
            if handoff_meta.get(
                "production_eligible_only_inside_canonical_release"
            ) is not True:
                result["errors"].append(
                    "handoff is not restricted to canonical release"
                )
            handoff_result = inspect_handoff_bundle(
                handoff_data,
                commit,
                source_ref,
                operator_data,
            )
            result["readonly_handoff"] = handoff_result
            if not handoff_result["valid"]:
                result["errors"].append(
                    "readonly handoff validation failed"
                )
    result["valid"] = not result["errors"]
    return result


def public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key != "members"
    }


def compare_releases(left: Path, right: Path) -> dict[str, Any]:
    left_result = inspect_release(left)
    right_result = inspect_release(right)
    left_names = set(left_result["member_names"])
    right_names = set(right_result["member_names"])
    left_members = left_result["members"]
    right_members = right_result["members"]
    differing = sorted(
        name
        for name in left_names & right_names
        if left_members.get(name) != right_members.get(name)
    )
    comparison = {
        "outer_sha256_equal": (
            left_result["zip_sha256"]
            == right_result["zip_sha256"]
        ),
        "member_sets_equal": left_names == right_names,
        "left_only_members": sorted(left_names - right_names),
        "right_only_members": sorted(right_names - left_names),
        "differing_members": differing,
        "manifest_equal": (
            left_result["manifest"] == right_result["manifest"]
        ),
    }
    passed = (
        left_result["valid"]
        and right_result["valid"]
        and comparison["outer_sha256_equal"]
        and comparison["member_sets_equal"]
        and not differing
        and comparison["manifest_equal"]
    )
    return {
        "report_type": "moomoo_discovery_release_comparison",
        "passed": passed,
        "left": public_result(left_result),
        "right": public_result(right_result),
        "comparison": comparison,
    }


def verify_release(path: Path) -> dict[str, Any]:
    result = inspect_release(path)
    return {
        "report_type": "moomoo_discovery_release_verification",
        "passed": result["valid"],
        "release": public_result(result),
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", required=True)
    parser.add_argument("--right")
    parser.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    left = Path(args.left).resolve()
    report = (
        compare_releases(left, Path(args.right).resolve())
        if args.right
        else verify_release(left)
    )
    rendered = json.dumps(
        report, ensure_ascii=False, indent=2
    ) + "\n"
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            rendered, encoding="utf-8", newline="\n"
        )
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
