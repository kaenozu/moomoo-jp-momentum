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
    return {
        "schema_version": 1,
        "review_id": "review-001",
        "reviewed_at": "2026-07-13T19:00:00+09:00",
        "reviewer": {"name": "operator", "role": "production reviewer"},
        "checks": {
            name: {
                "status": "CONFIRMED",
                "value": f"confirmed {name}",
                "evidence_refs": [f"evidence/{name}.txt"],
                "notes": "",
            }
            for name in validator.REQUIRED_CHECKS
        },
        "authorization_acknowledgement": {
            **validator.EXPECTED_AUTHORIZATION,
            "separate_approval_required": True,
        },
    }


def operator_result() -> dict[str, Any]:
    return {
        "report_type": "moomoo_discovery_operator_result",
        "operator_version": "1.2.1",
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
        "gate": {"gate_passed": True, "discovery_executed": True},
        "discovery": {
            "schema_version": 4,
            "safety": {"sqlite_connection_performed": False},
            "authorization": authorization,
        },
        "authorization": authorization,
    }


def release_manifest(candidate: bool = True) -> dict[str, Any]:
    return {
        "report_type": "moomoo_discovery_release_manifest",
        "release_format_version": 1,
        "operator_version": "1.2.1",
        "source_commit": "a" * 40,
        "source_ref": "refs/heads/master" if candidate else "refs/pull/32/merge",
        "source_event": "push" if candidate else "pull_request",
        "release_candidate": candidate,
        "distribution_status": (
            "MASTER_RELEASE_CANDIDATE" if candidate else "VALIDATION_ONLY"
        ),
        "authorization": dict(validator.EXPECTED_AUTHORIZATION),
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
    human["authorization_acknowledgement"]["preflight_authorized"] = True
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


def test_schema_and_template_are_valid_json() -> None:
    source = VALIDATOR_PATH.parent
    schema = json.loads(
        (source / "human-validation.schema.json").read_text(encoding="utf-8")
    )
    template = json.loads(
        (source / "human-validation.template.json").read_text(encoding="utf-8")
    )
    assert schema["properties"]["schema_version"]["const"] == 1
    errors, counts = validator.validate_human_payload(template)
    assert not errors
    assert counts["PENDING"] == len(validator.REQUIRED_CHECKS)
