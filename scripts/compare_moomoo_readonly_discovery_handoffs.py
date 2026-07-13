#!/usr/bin/env python3
"""Validate and byte-compare two read-only discovery handoff ZIPs."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Sequence

HANDOFF_VERSION = "1.2.2"
OPERATOR_VERSION = "1.2.1"
AUTHORIZATION = {
    "production_readiness": "BLOCKED",
    "preflight_authorized": False,
    "production_drill_authorized": False,
    "cutover_authorized": False,
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def parse_sums(data: bytes) -> dict[str, str]:
    text = data.decode("utf-8")
    rows: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        try:
            digest, name = line.split("  ", 1)
        except ValueError as exc:
            raise ValueError(
                f"Invalid HANDOFF_SHA256SUMS line {line_number}: {line!r}"
            ) from exc
        digest = digest.lower()
        if len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ValueError(
                f"Invalid SHA-256 on line {line_number}"
            )
        if not name or name in rows:
            raise ValueError(
                f"Missing or duplicate filename on line {line_number}"
            )
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError(f"Unsafe filename on line {line_number}: {name}")
        rows[name] = digest
    return rows


def inspect_handoff(path: Path) -> dict[str, Any]:
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
        result["errors"].append("handoff does not exist")
        return result

    result["zip_sha256"] = sha256_file(path)
    members: dict[str, bytes] = {}
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

    required = {
        "HANDOFF_SHA256SUMS.txt",
        "HANDOFF_MANIFEST.json",
        "README_FIRST.md",
        "LOCAL_AGENT_PROMPT.md",
        "EVIDENCE_REVIEW_CHECKLIST.md",
        "OPERATOR_README_ORIGINAL.md",
        "run-readonly-discovery.ps1",
        "verify-handoff.ps1",
        "moomoo_production_discovery_operator_v4_v1.2.1.zip",
    }
    missing = sorted(required - set(members))
    if missing:
        result["errors"].append(f"missing required members: {missing}")

    try:
        sums = parse_sums(members["HANDOFF_SHA256SUMS.txt"])
    except (KeyError, UnicodeError, ValueError) as exc:
        result["errors"].append(f"handoff checksums invalid: {exc}")
        sums = {}

    expected_covered = set(members) - {"HANDOFF_SHA256SUMS.txt"}
    if set(sums) != expected_covered:
        result["errors"].append(
            "handoff checksum coverage does not exactly match package members"
        )
    hash_errors: list[str] = []
    for name, expected in sorted(sums.items()):
        data = members.get(name)
        if data is None:
            hash_errors.append(f"missing listed member: {name}")
            continue
        actual = sha256_bytes(data)
        if actual != expected:
            hash_errors.append(
                f"hash mismatch for {name}: {actual} != {expected}"
            )
    result["internal_hash_errors"] = hash_errors
    if hash_errors:
        result["errors"].append("internal SHA-256 validation failed")

    try:
        manifest = json.loads(
            members["HANDOFF_MANIFEST.json"].decode("utf-8")
        )
        result["manifest"] = manifest
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        result["errors"].append(f"manifest invalid: {exc}")
        manifest = None

    if isinstance(manifest, dict):
        if manifest.get("report_type") != (
            "moomoo_readonly_discovery_handoff_manifest"
        ):
            result["errors"].append("unexpected handoff report_type")
        if manifest.get("schema_version") != 1:
            result["errors"].append("unexpected handoff schema_version")
        if manifest.get("handoff_version") != HANDOFF_VERSION:
            result["errors"].append("unexpected handoff version")
        if manifest.get("operator_version") != OPERATOR_VERSION:
            result["errors"].append("unexpected operator version")
        expected_head = str(
            manifest.get("expected_checkout_head", "")
        ).lower()
        if len(expected_head) != 40 or any(
            char not in "0123456789abcdef" for char in expected_head
        ):
            result["errors"].append("invalid expected checkout HEAD")
        if str(manifest.get("source_commit", "")).lower() != expected_head:
            result["errors"].append(
                "source_commit and expected checkout HEAD differ"
            )
        if manifest.get("authorization") != AUTHORIZATION:
            result["errors"].append(
                "manifest authorization boundary is not fail-closed"
            )
        bundle = manifest.get("operator_bundle")
        if not isinstance(bundle, dict):
            result["errors"].append("operator_bundle manifest is missing")
        else:
            name = bundle.get("name")
            if name != (
                "moomoo_production_discovery_operator_v4_v1.2.1.zip"
            ):
                result["errors"].append("unexpected operator bundle name")
            bundle_data = members.get(str(name))
            if bundle_data is None:
                result["errors"].append("operator bundle member is missing")
            elif sha256_bytes(bundle_data) != bundle.get("sha256"):
                result["errors"].append(
                    "operator bundle SHA-256 does not match manifest"
                )
            if str(bundle.get("source_commit", "")).lower() != expected_head:
                result["errors"].append(
                    "operator bundle source_commit does not match expected HEAD"
                )
        payload_files = manifest.get("payload_files")
        if not isinstance(payload_files, dict):
            result["errors"].append("payload_files is missing")
        else:
            for name, expected in sorted(payload_files.items()):
                data = members.get(name)
                if data is None:
                    result["errors"].append(
                        f"payload_files lists missing member: {name}"
                    )
                elif sha256_bytes(data) != expected:
                    result["errors"].append(
                        f"payload_files hash mismatch: {name}"
                    )

    result["valid"] = not result["errors"]
    return result


def compare_handoffs(left: Path, right: Path) -> dict[str, Any]:
    left_result = inspect_handoff(left)
    right_result = inspect_handoff(right)
    left_names = set(left_result["member_names"])
    right_names = set(right_result["member_names"])
    left_members = left_result["members"]
    right_members = right_result["members"]
    differing_members = sorted(
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
        "differing_members": differing_members,
        "manifest_equal": (
            left_result["manifest"] == right_result["manifest"]
        ),
    }
    passed = (
        left_result["valid"]
        and right_result["valid"]
        and comparison["outer_sha256_equal"]
        and comparison["member_sets_equal"]
        and not differing_members
        and comparison["manifest_equal"]
    )
    return {
        "report_type": "moomoo_readonly_discovery_handoff_comparison",
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
    report = compare_handoffs(
        Path(args.left).resolve(),
        Path(args.right).resolve(),
    )
    rendered = json.dumps(
        report, ensure_ascii=False, indent=2
    ) + "\n"
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            rendered,
            encoding="utf-8",
            newline="\n",
        )
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
