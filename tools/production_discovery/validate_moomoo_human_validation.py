#!/usr/bin/env python3
"""Validate production-discovery human evidence without authorizing execution."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REQUIRED_CHECKS = (
    "production_host_identity",
    "launch_source",
    "production_working_directory",
    "active_config_path",
    "resolved_live_database",
    "wal_shm_state",
    "writer_inventory",
    "other_host_writer_review",
    "writer_stop_procedure",
    "writer_start_procedure",
    "no_write_window",
    "secondary_storage_failure_domain",
    "secondary_storage_free_space",
    "no_real_order_api",
)
CHECK_STATUSES = {
    "CONFIRMED",
    "PENDING",
    "INCONCLUSIVE",
    "BLOCKED",
    "CORRECTION_REQUIRED",
}
EXPECTED_AUTHORIZATION = {
    "production_readiness": "BLOCKED",
    "preflight_authorized": False,
    "production_drill_authorized": False,
    "cutover_authorized": False,
}
EXPECTED_ACKNOWLEDGEMENT = {
    **EXPECTED_AUTHORIZATION,
    "separate_approval_required": True,
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")


class ValidationError(RuntimeError):
    """Raised for malformed inputs or unsafe output conditions."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Could not read JSON {path}: {exc}") from exc


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def authorization_is_fail_closed(payload: Any) -> bool:
    return isinstance(payload, dict) and all(
        payload.get(key) == expected
        for key, expected in EXPECTED_AUTHORIZATION.items()
    )


def normalize_evidence_path(value: str) -> str:
    normalized = value.strip().replace("/", "\\").rstrip("\\")
    parts = normalized.split("\\")
    for index, part in enumerate(parts[:-1]):
        if part.casefold() == "users":
            parts[index + 1] = "<REDACTED_USER>"
            break
    if normalized.startswith("\\\\") and len(parts) >= 4:
        parts[2] = "<REDACTED_SERVER>"
        parts[3] = "<REDACTED_SHARE>"
    return "\\".join(parts).casefold()


def confirmed_check_value(human: Any, name: str) -> str | None:
    if not isinstance(human, dict):
        return None
    checks = human.get("checks")
    if not isinstance(checks, dict):
        return None
    item = checks.get(name)
    if not isinstance(item, dict) or item.get("status") != "CONFIRMED":
        return None
    value = item.get("value")
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def validate_machine_human_consistency(
    human: Any,
    payload: Any,
) -> list[str]:
    runtime = confirmed_check_value(
        human, "production_working_directory"
    )
    config = confirmed_check_value(human, "active_config_path")
    database = confirmed_check_value(human, "resolved_live_database")
    if not runtime or not config or not database:
        return []
    if not isinstance(payload, dict):
        return ["machine/human path consistency payload is invalid"]
    discovery = payload.get("discovery")
    if not isinstance(discovery, dict):
        return ["machine/human path consistency discovery is missing"]
    rows = discovery.get("runtime_path_evidence")
    if not isinstance(rows, list):
        return ["runtime_path_evidence must be an array"]

    supported: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("database_exists") is not True:
            continue
        if row.get("resolution_error"):
            continue
        if not (
            row.get("runtime_machine_observed") is True
            or row.get("runtime_human_asserted") is True
        ):
            continue
        supported.append(row)

    target = (
        normalize_evidence_path(runtime),
        normalize_evidence_path(config),
        normalize_evidence_path(database),
    )
    matching = [
        row
        for row in supported
        if (
            normalize_evidence_path(
                str(row.get("production_working_directory") or "")
            ),
            normalize_evidence_path(
                str(row.get("config_path") or "")
            ),
            normalize_evidence_path(
                str(row.get("resolved_database_path") or "")
            ),
        )
        == target
    ]
    if not matching:
        return [
            "Confirmed production_working_directory, active_config_path, and "
            "resolved_live_database do not identify one supported existing "
            "runtime_path_evidence mapping."
        ]
    return []


def validate_human_payload(
    payload: Any,
) -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    counts = {status: 0 for status in CHECK_STATUSES}
    if not isinstance(payload, dict):
        return ["human validation must be a JSON object"], counts

    allowed = {
        "schema_version",
        "review_id",
        "reviewed_at",
        "reviewer",
        "checks",
        "authorization_acknowledgement",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        errors.append(f"unknown top-level fields: {', '.join(unknown)}")
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    review_id = payload.get("review_id")
    if not isinstance(review_id, str) or not review_id.strip():
        errors.append("review_id must be a non-empty string")
    reviewed_at = payload.get("reviewed_at")
    if not isinstance(reviewed_at, str) or not parse_datetime(reviewed_at):
        errors.append(
            "reviewed_at must be an RFC3339 timestamp with timezone"
        )

    reviewer = payload.get("reviewer")
    if not isinstance(reviewer, dict):
        errors.append("reviewer must be an object")
    else:
        if set(reviewer) - {"name", "role"}:
            errors.append("reviewer contains unknown fields")
        for field in ("name", "role"):
            value = reviewer.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    f"reviewer.{field} must be a non-empty string"
                )

    checks = payload.get("checks")
    if not isinstance(checks, dict):
        errors.append("checks must be an object")
        checks = {}
    missing = sorted(set(REQUIRED_CHECKS) - set(checks))
    unknown_checks = sorted(set(checks) - set(REQUIRED_CHECKS))
    if missing:
        errors.append(f"missing checks: {', '.join(missing)}")
    if unknown_checks:
        errors.append(f"unknown checks: {', '.join(unknown_checks)}")

    for name in REQUIRED_CHECKS:
        item = checks.get(name)
        if not isinstance(item, dict):
            if name in checks:
                errors.append(f"checks.{name} must be an object")
            continue
        extra = sorted(
            set(item) - {"status", "value", "evidence_refs", "notes"}
        )
        if extra:
            errors.append(
                f"checks.{name} contains unknown fields: "
                + ", ".join(extra)
            )
        status = item.get("status")
        if status not in CHECK_STATUSES:
            errors.append(f"checks.{name}.status is invalid")
            continue
        counts[str(status)] += 1
        value = item.get("value")
        refs = item.get("evidence_refs")
        notes = item.get("notes")
        if not isinstance(value, str):
            errors.append(f"checks.{name}.value must be a string")
        if not isinstance(notes, str):
            errors.append(f"checks.{name}.notes must be a string")
        if not isinstance(refs, list) or any(
            not isinstance(ref, str) or not ref.strip()
            for ref in refs
        ):
            errors.append(
                f"checks.{name}.evidence_refs must contain "
                "non-empty strings"
            )
            refs = []
        elif len(refs) != len(set(refs)):
            errors.append(
                f"checks.{name}.evidence_refs contains duplicates"
            )
        if status == "CONFIRMED":
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    f"checks.{name} is CONFIRMED but value is empty"
                )
            if not refs:
                errors.append(
                    f"checks.{name} is CONFIRMED but evidence_refs is empty"
                )

    if payload.get("authorization_acknowledgement") != (
        EXPECTED_ACKNOWLEDGEMENT
    ):
        errors.append(
            "authorization_acknowledgement must retain BLOCKED/false "
            "authorization values and require separate approval"
        )
    return errors, counts


def validate_operator_result(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["operator result must be a JSON object"]
    errors: list[str] = []
    expected = {
        "report_type": "moomoo_discovery_operator_result",
        "operator_version": "1.2.1",
        "status": "completed_readonly_discovery",
        "operator_exit_code": 0,
        "powershell_exit_code": 0,
        "validation_status": "MACHINE_PASS_HUMAN_REVIEW_REQUIRED",
        "machine_validation_status": "PASS",
        "human_validation_status": "PENDING",
        "operational_validation_status": "INCONCLUSIVE",
        "evidence_complete": True,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            errors.append(
                f"operator result {key} must be {value!r}, "
                f"got {payload.get(key)!r}"
            )
    if not authorization_is_fail_closed(payload):
        errors.append(
            "operator result authorization boundary is not fail-closed"
        )
    return errors


def validate_discovery(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["redacted discovery must be a JSON object"]
    errors: list[str] = []
    if payload.get("report_type") != "moomoo_discovery_v4_gated_result":
        errors.append("unexpected redacted discovery report_type")
    gate = payload.get("gate")
    if not isinstance(gate, dict):
        errors.append("redacted discovery gate is missing")
    else:
        if gate.get("gate_passed") is not True:
            errors.append(
                "redacted discovery gate_passed must be true"
            )
        if gate.get("discovery_executed") is not True:
            errors.append(
                "redacted discovery discovery_executed must be true"
            )
    discovery = payload.get("discovery")
    if not isinstance(discovery, dict):
        errors.append("redacted discovery payload is missing")
        return errors
    if discovery.get("schema_version") != 4:
        errors.append("redacted discovery schema_version must be 4")
    safety = discovery.get("safety")
    if not isinstance(safety, dict):
        errors.append("redacted discovery safety object is missing")
    elif safety.get("sqlite_connection_performed") is not False:
        errors.append("redacted discovery reports a SQLite connection")
    if not authorization_is_fail_closed(
        discovery.get("authorization")
    ):
        errors.append(
            "redacted discovery authorization is not fail-closed"
        )
    if not authorization_is_fail_closed(payload.get("authorization")):
        errors.append("gated result authorization is not fail-closed")
    return errors


def validate_release_manifest(
    payload: Any,
) -> tuple[list[str], bool]:
    if not isinstance(payload, dict):
        return ["release manifest must be a JSON object"], False
    errors: list[str] = []
    if payload.get("report_type") != "moomoo_discovery_release_manifest":
        errors.append("unexpected release manifest report_type")
    if payload.get("release_format_version") != 1:
        errors.append("release_format_version must be 1")
    if payload.get("operator_version") != "1.2.1":
        errors.append(
            "release manifest operator_version must be 1.2.1"
        )
    if payload.get("source_bytes") != "git_blob":
        errors.append("release manifest source_bytes must be git_blob")
    commit = payload.get("source_commit")
    source_ref = payload.get("source_ref")
    if not isinstance(commit, str) or HEX40.fullmatch(commit) is None:
        errors.append(
            "release manifest source_commit must be 40 lowercase hex"
        )
    if not authorization_is_fail_closed(payload.get("authorization")):
        errors.append(
            "release manifest authorization is not fail-closed"
        )
    if payload.get("separate_approval_required") is not True:
        errors.append(
            "release manifest must require separate approval"
        )

    human = payload.get("human_validation")
    expected_human = {
        "schema": "human-validation.schema.json",
        "template": "human-validation.template.json",
        "validator": "validate_moomoo_human_validation.py",
        "readme": "README_moomoo_human_validation_ja.md",
        "release_verifier": "compare_moomoo_discovery_releases.py",
        "outputs": [
            "06-human-validation.json",
            "07-preflight-eligibility.json",
        ],
    }
    if human != expected_human:
        errors.append(
            "release manifest human_validation metadata is invalid"
        )

    operator = payload.get("operator_bundle")
    if not isinstance(operator, dict):
        errors.append("release manifest operator_bundle is missing")
    else:
        if operator.get("filename") != (
            "moomoo_production_discovery_operator_v4_v1.2.1.zip"
        ):
            errors.append(
                "release manifest operator filename is invalid"
            )
        operator_sha = operator.get("sha256")
        if not isinstance(operator_sha, str) or re.fullmatch(
            r"[0-9a-f]{64}", operator_sha
        ) is None:
            errors.append(
                "release manifest operator SHA-256 is invalid"
            )
        if operator.get("manifest_source_commit") != commit:
            errors.append(
                "release manifest operator source_commit differs "
                "from release"
            )
        if operator.get("manifest_source_ref") != source_ref:
            errors.append(
                "release manifest operator source_ref differs "
                "from release"
            )

    candidate = payload.get("release_candidate") is True
    if candidate:
        if source_ref != "refs/heads/master":
            errors.append(
                "release_candidate=true requires source_ref "
                "refs/heads/master"
            )
        if payload.get("distribution_status") != (
            "MASTER_RELEASE_CANDIDATE"
        ):
            errors.append(
                "release_candidate=true requires "
                "MASTER_RELEASE_CANDIDATE"
            )
        if payload.get("source_event") != "push":
            errors.append(
                "release_candidate=true requires source_event push"
            )
    elif payload.get("distribution_status") != "VALIDATION_ONLY":
        errors.append(
            "release_candidate=false requires VALIDATION_ONLY status"
        )
    return errors, candidate


def determine_status(
    errors: list[str],
    counts: dict[str, int],
    release_candidate: bool,
) -> str:
    if errors or counts["CORRECTION_REQUIRED"]:
        return "CORRECTION_REQUIRED"
    if not release_candidate or counts["BLOCKED"]:
        return "BLOCKED"
    if counts["PENDING"] or counts["INCONCLUSIVE"]:
        return "INCONCLUSIVE"
    if counts["CONFIRMED"] != len(REQUIRED_CHECKS):
        return "INCONCLUSIVE"
    return "ELIGIBLE_FOR_SEPARATE_PREFLIGHT_APPROVAL"


def evaluate(
    human: Any,
    operator_result: Any,
    discovery: Any,
    release_manifest: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    human_errors, counts = validate_human_payload(human)
    release_errors, release_candidate = validate_release_manifest(
        release_manifest
    )
    errors = [
        *human_errors,
        *validate_operator_result(operator_result),
        *validate_discovery(discovery),
        *validate_machine_human_consistency(human, discovery),
        *release_errors,
    ]
    status = determine_status(errors, counts, release_candidate)
    normalized = copy.deepcopy(human)
    if not isinstance(normalized, dict):
        normalized = {"invalid_input": normalized}
    normalized.update(
        {
            "report_type": "moomoo_human_validation",
            "normalized_at": utc_now(),
            "validation_errors": errors,
            "check_status_counts": counts,
        }
    )
    result = {
        "report_type": "moomoo_preflight_eligibility",
        "evaluated_at": utc_now(),
        "eligibility_status": status,
        "machine_validation_status": (
            operator_result.get("machine_validation_status")
            if isinstance(operator_result, dict)
            else None
        ),
        "human_validation_status": (
            "PASS"
            if status == "ELIGIBLE_FOR_SEPARATE_PREFLIGHT_APPROVAL"
            else "PENDING"
        ),
        "operational_validation_status": "INCONCLUSIVE",
        "release_candidate": release_candidate,
        "check_status_counts": counts,
        "errors": errors,
        "next_action": {
            "ELIGIBLE_FOR_SEPARATE_PREFLIGHT_APPROVAL": (
                "Request a separate explicit approval before running "
                "-PreflightOnly."
            ),
            "INCONCLUSIVE": (
                "Complete missing human evidence and rerun this validator."
            ),
            "BLOCKED": (
                "Resolve blocked conditions or use a master-bound release "
                "candidate, then rerun this validator."
            ),
            "CORRECTION_REQUIRED": (
                "Correct malformed or contradictory evidence before "
                "retrying."
            ),
        }[status],
        "authorization": {
            **EXPECTED_AUTHORIZATION,
            "separate_approval_required": True,
        },
    }
    return normalized, result


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--human-validation", required=True)
    parser.add_argument("--operator-result", required=True)
    parser.add_argument("--discovery-redacted", required=True)
    parser.add_argument("--release-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    paths = {
        "human_validation": Path(
            args.human_validation
        ).resolve(),
        "operator_result": Path(args.operator_result).resolve(),
        "discovery_redacted": Path(
            args.discovery_redacted
        ).resolve(),
        "release_manifest": Path(args.release_manifest).resolve(),
    }
    output_dir = Path(args.output_dir).resolve()
    if not output_dir.is_dir():
        raise ValidationError(
            f"Output directory must already exist: {output_dir}"
        )
    targets = {
        "human": output_dir / "06-human-validation.json",
        "eligibility": output_dir / "07-preflight-eligibility.json",
    }
    existing = [
        str(path) for path in targets.values() if path.exists()
    ]
    if existing:
        raise ValidationError(
            "Refusing to overwrite existing validation evidence: "
            + ", ".join(existing)
        )

    loaded = {
        name: load_json(path) for name, path in paths.items()
    }
    normalized, result = evaluate(
        loaded["human_validation"],
        loaded["operator_result"],
        loaded["discovery_redacted"],
        loaded["release_manifest"],
    )
    normalized["source_files"] = {
        name: {
            "path": str(path),
            "sha256": sha256_file(path),
        }
        for name, path in paths.items()
    }
    result["source_sha256"] = {
        name: sha256_file(path)
        for name, path in paths.items()
    }
    write_json(targets["human"], normalized)
    write_json(targets["eligibility"], result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    status = result["eligibility_status"]
    if status == "ELIGIBLE_FOR_SEPARATE_PREFLIGHT_APPROVAL":
        return 0
    if status in {"BLOCKED", "INCONCLUSIVE"}:
        return 2
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
