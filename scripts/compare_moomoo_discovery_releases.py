#!/usr/bin/env python3
"""Validate and byte-compare two production-discovery release ZIPs."""

from __future__ import annotations

import argparse
import hashlib
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
HEX40 = re.compile(r"^[0-9a-f]{40}$")


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
        if len(digest) != 64 or any(
            char not in "0123456789abcdefABCDEF" for char in digest
        ):
            raise ValueError(f"Invalid SHA-256 on line {line_number}")
        if not name or name in rows:
            raise ValueError(
                f"Missing or duplicate filename on line {line_number}"
            )
        rows[name] = digest.lower()
    return rows


def inspect_release(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "zip_sha256": None,
        "duplicate_entries": [],
        "bad_compressed_member": None,
        "member_names": [],
        "internal_hash_errors": [],
        "manifest": None,
        "members": {},
        "valid": False,
        "errors": [],
    }
    if not path.is_file():
        result["errors"].append("release ZIP does not exist")
        return result
    result["zip_sha256"] = sha256_file(path)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = [
                info.filename
                for info in archive.infolist()
                if not info.is_dir()
            ]
            duplicates = sorted(
                {name for name in names if names.count(name) > 1}
            )
            result["duplicate_entries"] = duplicates
            result["member_names"] = sorted(names)
            result["bad_compressed_member"] = archive.testzip()
            members = {
                name: archive.read(name)
                for name in names
                if name not in duplicates
            }
            result["members"] = {
                name: sha256_bytes(data)
                for name, data in members.items()
            }
    except (OSError, zipfile.BadZipFile) as exc:
        result["errors"].append(f"ZIP read failed: {exc}")
        return result

    if result["duplicate_entries"]:
        result["errors"].append("duplicate ZIP entries")
    if result["bad_compressed_member"]:
        result["errors"].append("compressed data test failed")
    try:
        sums = parse_sums(members["SHA256SUMS.txt"])
    except (KeyError, UnicodeError, ValueError) as exc:
        result["errors"].append(f"SHA256SUMS invalid: {exc}")
        sums = {}
    hash_errors: list[str] = []
    for name, expected in sorted(sums.items()):
        data = members.get(name)
        if data is None:
            hash_errors.append(f"missing listed member: {name}")
        elif sha256_bytes(data) != expected:
            hash_errors.append(f"hash mismatch for {name}")
    result["internal_hash_errors"] = hash_errors
    if hash_errors:
        result["errors"].append("internal SHA-256 validation failed")

    try:
        manifest = json.loads(
            members["release-manifest.json"].decode("utf-8")
        )
        result["manifest"] = manifest
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        result["errors"].append(f"release manifest invalid: {exc}")
        manifest = None

    required = {
        OPERATOR_ZIP,
        "human-validation.schema.json",
        "human-validation.template.json",
        "validate_moomoo_human_validation.py",
        "README_moomoo_human_validation_ja.md",
        "release-manifest.json",
        "SHA256SUMS.txt",
    }
    missing = sorted(required - set(result["member_names"]))
    if missing:
        result["errors"].append(
            "missing required release members: " + ", ".join(missing)
        )

    if isinstance(manifest, dict):
        if manifest.get("report_type") != "moomoo_discovery_release_manifest":
            result["errors"].append("unexpected release manifest report_type")
        if manifest.get("release_format_version") != 1:
            result["errors"].append("unexpected release format version")
        if manifest.get("operator_version") != "1.2.1":
            result["errors"].append("unexpected operator version")
        if manifest.get("authorization") != EXPECTED_AUTHORIZATION:
            result["errors"].append(
                "release authorization boundary is not fail-closed"
            )
        commit = manifest.get("source_commit")
        if not isinstance(commit, str) or HEX40.fullmatch(commit) is None:
            result["errors"].append("invalid source_commit")
        candidate = manifest.get("release_candidate") is True
        expected_status = (
            "MASTER_RELEASE_CANDIDATE"
            if candidate
            else "VALIDATION_ONLY"
        )
        if manifest.get("distribution_status") != expected_status:
            result["errors"].append("distribution_status is inconsistent")
        if candidate and (
            manifest.get("source_ref") != "refs/heads/master"
            or manifest.get("source_event") != "push"
        ):
            result["errors"].append(
                "master release candidate has invalid source identity"
            )
        operator = manifest.get("operator_bundle")
        if not isinstance(operator, dict):
            result["errors"].append("operator bundle metadata is missing")
        else:
            operator_data = members.get(OPERATOR_ZIP)
            if operator_data is None:
                result["errors"].append("nested operator ZIP is missing")
            elif operator.get("sha256") != sha256_bytes(operator_data):
                result["errors"].append(
                    "nested operator ZIP SHA-256 does not match manifest"
                )

    result["valid"] = not result["errors"]
    return result


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
            left_result["zip_sha256"] == right_result["zip_sha256"]
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
        "left": {
            key: value
            for key, value in left_result.items()
            if key != "members"
        },
        "right": {
            key: value
            for key, value in right_result.items()
            if key != "members"
        },
        "comparison": comparison,
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    report = compare_releases(
        Path(args.left).resolve(),
        Path(args.right).resolve(),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
