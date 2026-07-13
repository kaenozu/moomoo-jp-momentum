from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = (
    ROOT
    / "tools"
    / "production_discovery"
    / "validate_moomoo_human_validation.py"
)


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "moomoo_human_validator", VALIDATOR_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = load_validator()


def confirmed_human_payload() -> dict[str, Any]:
    checks = {
        name: {
            "status": "CONFIRMED",
            "value": f"confirmed {name}",
            "evidence_refs": [f"evidence/{name}.txt"],
            "notes": "",
        }
        for name in validator.REQUIRED_CHECKS
    }
    checks["production_working_directory"]["value"] = (
        r"C:\production-runtime"
    )
    checks["active_config_path"]["value"] = (
        r"C:\production-runtime\config.yaml"
    )
    checks["resolved_live_database"]["value"] = (
        r"C:\production-runtime\data\moomoo.db"
    )
    return {
        "schema_version": 1,
        "review_id": "review-001",
        "reviewed_at": "2026-07-13T19:00:00+09:00",
        "reviewer": {
            "name": "operator",
            "role": "production reviewer",
        },
        "checks": checks,
        "authorization_acknowledgement": {
            **validator.EXPECTED_AUTHORIZATION,
            "separate_approval_required": True,
        },
    }


def operator_result() -> dict[str, Any]:
    return {
        "report_type": "moomoo_discovery_operator_result",
        "operator_version": "1.2.2",
        "status": "completed_readonly_discovery",
        "operator_exit_code": 0,
        "powershell_exit_code": 0,
        "validation_status": "MACHINE_PASS_HUMAN_REVIEW_REQUIRED",
        "machine_validation_status": "PASS",
        "human_validation_status": "PENDING",
        "operational_validation_status": "INCONCLUSIVE",
        "production_readiness": "BLOCKED",
        "preflight_authorized": False,
        "production_drill_authorized": False,
        "cutover_authorized": False,
        "evidence_complete": True,
    }


def discovery_result() -> dict[str, Any]:
    authorization = dict(validator.EXPECTED_AUTHORIZATION)
    return {
        "report_type": "moomoo_discovery_v4_gated_result",
        "gate": {
            "gate_passed": True,
            "discovery_executed": True,
        },
        "discovery": {
            "schema_version": 4,
            "safety": {"sqlite_connection_performed": False},
            "authorization": authorization,
            "runtime_path_evidence": [
                {
                    "config_path": (
                        r"C:\production-runtime\config.yaml"
                    ),
                    "production_working_directory": (
                        r"C:\production-runtime"
                    ),
                    "resolved_database_path": (
                        r"C:\production-runtime\data\moomoo.db"
                    ),
                    "database_exists": True,
                    "runtime_machine_observed": True,
                    "runtime_human_asserted": False,
                    "resolution_error": None,
                }
            ],
        },
        "authorization": authorization,
    }


def release_manifest(candidate: bool = True) -> dict[str, Any]:
    source_ref = (
        "refs/heads/master" if candidate else "refs/pull/32/merge"
    )
    return {
        "report_type": "moomoo_discovery_release_manifest",
        "release_format_version": 1,
        "operator_version": "1.2.2",
        "source_commit": "a" * 40,
        "source_ref": source_ref,
        "source_event": "push" if candidate else "pull_request",
        "source_bytes": "git_blob",
        "release_candidate": candidate,
        "distribution_status": (
            "MASTER_RELEASE_CANDIDATE"
            if candidate
            else "VALIDATION_ONLY"
        ),
        "operator_bundle": {
            "filename": (
                "moomoo_production_discovery_operator_v4_v1.2.2.zip"
            ),
            "sha256": "b" * 64,
            "manifest_source_commit": "a" * 40,
            "manifest_source_ref": source_ref,
        },
        "human_validation": {
            "schema": "human-validation.schema.json",
            "template": "human-validation.template.json",
            "validator": "validate_moomoo_human_validation.py",
            "readme": "README_moomoo_human_validation_ja.md",
            "release_verifier": (
                "compare_moomoo_discovery_releases.py"
            ),
            "outputs": [
                "06-human-validation.json",
                "07-preflight-eligibility.json",
            ],
        },
        "authorization": dict(validator.EXPECTED_AUTHORIZATION),
        "separate_approval_required": True,
    }


def test_all_confirmed_master_candidate_is_eligible() -> None:
    _, result = validator.evaluate(
        confirmed_human_payload(),
        operator_result(),
        discovery_result(),
        release_manifest(),
    )
    assert (
        result["eligibility_status"]
        == "ELIGIBLE_FOR_SEPARATE_PREFLIGHT_APPROVAL"
    )
    assert result["authorization"]["preflight_authorized"] is False


def test_pending_check_is_inconclusive() -> None:
    human = confirmed_human_payload()
    human["checks"]["writer_inventory"] = {
        "status": "PENDING",
        "value": "",
        "evidence_refs": [],
        "notes": "inventory not complete",
    }
    _, result = validator.evaluate(
        human,
        operator_result(),
        discovery_result(),
        release_manifest(),
    )
    assert result["eligibility_status"] == "INCONCLUSIVE"


def test_validation_only_release_is_blocked() -> None:
    _, result = validator.evaluate(
        confirmed_human_payload(),
        operator_result(),
        discovery_result(),
        release_manifest(candidate=False),
    )
    assert result["eligibility_status"] == "BLOCKED"


def test_true_authorization_is_correction_required() -> None:
    human = confirmed_human_payload()
    human["authorization_acknowledgement"][
        "preflight_authorized"
    ] = True
    _, result = validator.evaluate(
        human,
        operator_result(),
        discovery_result(),
        release_manifest(),
    )
    assert result["eligibility_status"] == "CORRECTION_REQUIRED"
    assert result["authorization"]["preflight_authorized"] is False


def test_confirmed_without_evidence_is_correction_required() -> None:
    human = confirmed_human_payload()
    human["checks"]["launch_source"]["evidence_refs"] = []
    _, result = validator.evaluate(
        human,
        operator_result(),
        discovery_result(),
        release_manifest(),
    )
    assert result["eligibility_status"] == "CORRECTION_REQUIRED"


def test_extra_authorization_key_is_correction_required() -> None:
    operator = operator_result()
    operator["emergency_authorized"] = False
    _, result = validator.evaluate(
        confirmed_human_payload(),
        operator,
        discovery_result(),
        release_manifest(),
    )
    assert result["eligibility_status"] == "CORRECTION_REQUIRED"


def test_inconsistent_runtime_mapping_is_correction_required() -> None:
    human = confirmed_human_payload()
    human["checks"]["resolved_live_database"]["value"] = (
        r"C:\wrong\data\moomoo.db"
    )
    _, result = validator.evaluate(
        human,
        operator_result(),
        discovery_result(),
        release_manifest(),
    )
    assert result["eligibility_status"] == "CORRECTION_REQUIRED"
    assert any(
        "do not identify one supported existing" in error
        for error in result["errors"]
    )


def test_redacted_user_path_can_match_local_human_value() -> None:
    human = confirmed_human_payload()
    human["checks"]["production_working_directory"]["value"] = (
        r"C:\Users\actual-user\production-runtime"
    )
    human["checks"]["active_config_path"]["value"] = (
        r"C:\Users\actual-user\production-runtime\config.yaml"
    )
    human["checks"]["resolved_live_database"]["value"] = (
        r"C:\Users\actual-user\production-runtime\data\moomoo.db"
    )
    discovery = discovery_result()
    row = discovery["discovery"]["runtime_path_evidence"][0]
    row["production_working_directory"] = (
        r"C:\Users\<REDACTED_USER>\production-runtime"
    )
    row["config_path"] = (
        r"C:\Users\<REDACTED_USER>\production-runtime\config.yaml"
    )
    row["resolved_database_path"] = (
        r"C:\Users\<REDACTED_USER>\production-runtime\data\moomoo.db"
    )
    _, result = validator.evaluate(
        human,
        operator_result(),
        discovery,
        release_manifest(),
    )
    assert (
        result["eligibility_status"]
        == "ELIGIBLE_FOR_SEPARATE_PREFLIGHT_APPROVAL"
    )


def test_schema_and_template_are_valid_json() -> None:
    source = VALIDATOR_PATH.parent
    schema = json.loads(
        (source / "human-validation.schema.json").read_text(
            encoding="utf-8"
        )
    )
    template = json.loads(
        (source / "human-validation.template.json").read_text(
            encoding="utf-8"
        )
    )
    assert schema["properties"]["schema_version"]["const"] == 1
    errors, counts = validator.validate_human_payload(template)
    assert not errors
    assert counts["PENDING"] == len(validator.REQUIRED_CHECKS)
