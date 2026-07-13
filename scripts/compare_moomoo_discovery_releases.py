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
OPERATOR_ZIP = "moomoo_production_discovery_operator_v4_v1.2.1.zip"
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
    "test_bundle_builder.py",
    "run_moomoo_discovery_operator_tests.ps1",
    "validate_moomoo_discovery_operator.py",
    "README_moomoo_discovery_operator_ja.md",
}
EXPECTED_OPERATOR_MEMBERS = EXPECTED_OPERATOR_SOURCE_MEMBERS | {
    "SHA256SUMS.txt",
    "bundle-manifest.json",
}
EXPECTED_RELEASE_MEMBERS = {
    OPERATOR_ZIP,
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
    return sha256_bytes(path.read_bytes())


def parse_sums(data: bytes) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line_number, line in enumerate(
        data.decode("utf-8").splitlines(), start=1
    ):
        if not line:
            continue
        try:
            digest, name = line.split("  ", 1)
        except ValueError as exc:
            raise ValueError(
                f"Invalid SHA256SUMS line {line_number}: {line!r}"
            ) from exc
        if HEX64.fullmatch(digest.lower()) is None:
            raise ValueError(f"Invalid SHA-256 on line {line_number}")
        if not name or name in rows:
            raise ValueError(
                f"Missing or duplicate filename on line {line_number}"
            )
        rows[name] = digest.lower()
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
    expected_names: set[str],
) -> list[str]:
    errors: list[str] = []
    try:
        sums = parse_sums(members["SHA256SUMS.txt"])
    except (KeyError, UnicodeError, ValueError) as exc:
        return [f"SHA256SUMS invalid: {exc}"]
    if set(sums) != expected_names:
        errors.append(
            "SHA256SUMS member set mismatch: "
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
        members, EXPECTED_OPERATOR_SOURCE_MEMBERS
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
        if manifest.get("operator_version") != "1.2.1":
            result["errors"].append(
                "nested operator version is not 1.2.1"
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
        expected_file_names = EXPECTED_OPERATOR_MEMBERS - {
            "bundle-manifest.json"
        }
        if not isinstance(files, dict) or set(files) != expected_file_names:
            result["errors"].append(
                "nested operator manifest files set is invalid"
            )
        elif any(
            files.get(name) != sha256_bytes(members[name])
            for name in expected_file_names
        ):
            result["errors"].append(
                "nested operator manifest file hash mismatch"
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
        members, EXPECTED_RELEASE_SUM_MEMBERS
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
        if manifest.get("operator_version") != "1.2.1":
            result["errors"].append("unexpected operator version")
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

        human = manifest.get("human_validation")
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
        if human != expected_human:
            result["errors"].append(
                "human-validation manifest metadata is invalid"
            )

        operator = manifest.get("operator_bundle")
        operator_data = members.get(OPERATOR_ZIP)
        if not isinstance(operator, dict):
            result["errors"].append(
                "operator bundle metadata is missing"
            )
        elif operator_data is None:
            result["errors"].append("nested operator ZIP is missing")
        else:
            if operator.get("filename") != OPERATOR_ZIP:
                result["errors"].append(
                    "nested operator filename is invalid"
                )
            actual_operator_sha = sha256_bytes(operator_data)
            if operator.get("sha256") != actual_operator_sha:
                result["errors"].append(
                    "nested operator ZIP SHA-256 does not match manifest"
                )
            if operator.get("manifest_source_commit") != commit:
                result["errors"].append(
                    "operator manifest_source_commit differs from release"
                )
            if operator.get("manifest_source_ref") != source_ref:
                result["errors"].append(
                    "operator manifest_source_ref differs from release"
                )
            operator_result = inspect_operator_bundle(
                operator_data, commit, source_ref
            )
            result["operator_bundle"] = operator_result
            if not operator_result["valid"]:
                result["errors"].append(
                    "nested operator bundle validation failed"
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
