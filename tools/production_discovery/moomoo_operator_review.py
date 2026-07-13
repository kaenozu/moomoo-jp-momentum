from __future__ import annotations

from dataclasses import asdict
from typing import Any, Sequence

from moomoo_operator_common import (
    EXPECTED_BUNDLE_FILE_SHA256,
    EXPECTED_DISCOVERY_SHA256,
    OPTIONAL_COMMANDS,
    REQUIRED_COMMANDS,
    VERSION,
    Finding,
    command_availability_map,
    derived_runtime_rows,
    existing_runtime_mappings,
    get_path,
    human_asserted_runtime_rows,
    is_error_object,
    machine_observed_runtime_rows,
    mapping_has_class,
    non_error_rows,
    parseable_configs,
    unique_existing_db_paths,
    utc_now,
)


def build_review(
    findings: Sequence[Finding],
    source_sha256: str,
    gate: dict[str, Any],
    discovery: dict[str, Any],
) -> dict[str, Any]:
    statuses = {finding.status for finding in findings}
    machine_failed = "FAIL" in statuses
    conflict = "CONFLICT" in statuses

    machine_validation_status = (
        "CORRECTION_REQUIRED" if machine_failed else "PASS"
    )
    human_validation_status = "PENDING"
    if machine_failed:
        operational_validation_status = "CORRECTION_REQUIRED"
        validation_status = "CORRECTION_REQUIRED"
        next_action = "correct_discovery_or_environment_then_rerun"
    elif conflict:
        operational_validation_status = "CONFLICT"
        validation_status = "CORRECTION_REQUIRED"
        next_action = "resolve_runtime_mapping_conflict_then_rerun"
    else:
        operational_validation_status = "INCONCLUSIVE"
        validation_status = "MACHINE_PASS_HUMAN_REVIEW_REQUIRED"
        next_action = "complete_human_runtime_and_writer_confirmation"

    machine_rows = machine_observed_runtime_rows(discovery)
    human_rows = human_asserted_runtime_rows(discovery)
    derived_rows = derived_runtime_rows(discovery)
    mappings = existing_runtime_mappings(discovery)
    machine_mappings = [
        row for row in mappings if mapping_has_class(row, "machine_observed")
    ]
    human_mappings = [
        row for row in mappings if mapping_has_class(row, "human_asserted")
    ]

    facts = {
        "captured_at": discovery.get("captured_at"),
        "schema_version": discovery.get("schema_version"),
        "powershell_version": get_path(discovery, "machine.powershell.version"),
        "powershell_edition": get_path(discovery, "machine.powershell.edition"),
        "repo_status": get_path(
            discovery, "repositories.preflight_candidate.status"
        ),
        "repo_head": get_path(
            discovery, "repositories.preflight_candidate.head"
        ),
        "repo_clean": get_path(
            discovery, "repositories.preflight_candidate.clean"
        ),
        "parseable_config_candidate_count": len(parseable_configs(discovery)),
        "machine_observed_runtime_candidate_count": len(machine_rows),
        "human_asserted_runtime_candidate_count": len(human_rows),
        "derived_runtime_candidate_count": len(derived_rows),
        "existing_runtime_mapping_count": len(mappings),
        "machine_observed_existing_mapping_count": len(machine_mappings),
        "human_asserted_existing_mapping_count": len(human_mappings),
        "unique_existing_database_path_count": len(
            unique_existing_db_paths(machine_mappings + human_mappings)
        ),
        "writer_process_count": len(
            non_error_rows(
                get_path(discovery, "runtime_writer_candidates.processes")
            )
        ),
        "scheduled_task_count": len(
            non_error_rows(
                get_path(discovery, "runtime_writer_candidates.scheduled_tasks")
            )
        ),
        "service_count": len(
            non_error_rows(
                get_path(discovery, "runtime_writer_candidates.services")
            )
        ),
        "startup_command_count": len(
            non_error_rows(
                get_path(
                    discovery, "runtime_writer_candidates.startup_commands"
                )
            )
        ),
        "database_file_candidate_count": len(
            non_error_rows(discovery.get("database_file_candidates"))
        ),
    }

    return {
        "report_type": "moomoo_discovery_operator_review",
        "operator_version": VERSION,
        "reviewed_at": utc_now(),
        "source_sha256": source_sha256,
        "validation_status": validation_status,
        "machine_validation_status": machine_validation_status,
        "human_validation_status": human_validation_status,
        "operational_validation_status": operational_validation_status,
        "production_readiness": "BLOCKED",
        "preflight_authorized": False,
        "production_drill_authorized": False,
        "cutover_authorized": False,
        "next_action": next_action,
        "facts": facts,
        "findings": [asdict(finding) for finding in findings],
        "operator_confirmations_required": [
            "Confirm the identified PC is the production host.",
            "Confirm the active launch source, exact production working directory, and active config.yaml.",
            "Confirm the selected runtime mapping resolves database.path to the actual live virtual-trading DB.",
            "Confirm all processes, Scheduled Tasks, services, startup entries, manual jobs, other user sessions, WSL, containers, and other hosts.",
            "Document writer stop/restart procedures and obtain a no-write window.",
            "Select secondary storage in a separate failure domain and confirm free space.",
            "Select new unused evidence, secondary, and restore paths outside the repository and live DB directories.",
            "Run -PreflightOnly only after every preceding item is directly confirmed.",
            "Do not run -ConfirmProductionExecution or cutover without separate explicit approval.",
        ],
        "gate_summary": {
            "gate_passed": gate.get("gate_passed"),
            "powershell_version": get_path(gate, "powershell.version"),
            "powershell_edition": get_path(gate, "powershell.edition"),
            "actual_sha256": gate.get("actual_sha256"),
        },
    }


def review_payload(payload: Any, source_sha256: str) -> dict[str, Any]:
    findings: list[Finding] = []

    def add(
        code: str,
        severity: str,
        status: str,
        message: str,
        evidence: str | None = None,
    ) -> None:
        findings.append(Finding(code, severity, status, message, evidence))

    if not isinstance(payload, dict):
        add(
            "INVALID_TOP_LEVEL",
            "critical",
            "FAIL",
            "Top-level JSON is not an object.",
        )
        return build_review(findings, source_sha256, {}, {})

    if payload.get("report_type") != "moomoo_discovery_v4_gated_result":
        add(
            "UNEXPECTED_REPORT_TYPE",
            "critical",
            "FAIL",
            f"Unexpected report_type: {payload.get('report_type')!r}",
            "report_type",
        )

    gate = payload.get("gate")
    discovery = payload.get("discovery")
    if not isinstance(gate, dict):
        add(
            "INVALID_GATE",
            "critical",
            "FAIL",
            "gate is missing or invalid.",
            "gate",
        )
        gate = {}
    else:
        if gate.get("gate_passed") is not True:
            add(
                "GATE_FAILED",
                "critical",
                "FAIL",
                "PowerShell parser/hash gate did not pass.",
                "gate.gate_passed",
            )
        if gate.get("parser_error_count") != 0:
            add(
                "PARSER_ERRORS",
                "critical",
                "FAIL",
                "PowerShell parser reported errors.",
                "gate.parser_errors",
            )
        if gate.get("hash_matches") is not True:
            add(
                "DISCOVERY_HASH_MISMATCH",
                "critical",
                "FAIL",
                "Discovery bundle hash validation failed.",
                "gate.file_checks",
            )
        checks = {
            str(row.get("name")): row
            for row in non_error_rows(gate.get("file_checks"))
            if row.get("name")
        }
        if set(checks) != set(EXPECTED_BUNDLE_FILE_SHA256):
            add(
                "DISCOVERY_BUNDLE_FILE_SET_MISMATCH",
                "critical",
                "FAIL",
                "Gate did not validate the exact frozen discovery file set.",
                "gate.file_checks",
            )
        for name, expected_hash in EXPECTED_BUNDLE_FILE_SHA256.items():
            row = checks.get(name)
            if (
                not row
                or str(row.get("actual_sha256") or "").lower()
                != expected_hash
            ):
                add(
                    "UNEXPECTED_DISCOVERY_FILE_HASH",
                    "critical",
                    "FAIL",
                    f"Gate did not validate the frozen hash for {name}.",
                    "gate.file_checks",
                )
        actual_hash = str(gate.get("actual_sha256") or "").lower()
        if actual_hash != EXPECTED_DISCOVERY_SHA256:
            add(
                "UNEXPECTED_DISCOVERY_HASH",
                "critical",
                "FAIL",
                "Gate used a wrapper hash not frozen in operator v1.2.1.",
                "gate.actual_sha256",
            )
        if gate.get("discovery_executed") is not True:
            add(
                "DISCOVERY_NOT_EXECUTED",
                "critical",
                "FAIL",
                "Gate did not execute discovery.",
                "gate.discovery_executed",
            )

    if payload.get("discovery_error"):
        add(
            "DISCOVERY_ERROR",
            "critical",
            "FAIL",
            f"Discovery error: {payload.get('discovery_error')}",
            "discovery_error",
        )

    if not isinstance(discovery, dict):
        add(
            "MISSING_DISCOVERY",
            "critical",
            "FAIL",
            "Discovery object is missing or invalid.",
            "discovery",
        )
        return build_review(findings, source_sha256, gate, {})

    if (
        discovery.get("report_type")
        != "moomoo_production_readonly_discovery"
        or discovery.get("schema_version") != 4
    ):
        add(
            "INVALID_DISCOVERY_SCHEMA",
            "critical",
            "FAIL",
            "Discovery report schema is not v4.",
            "discovery.schema_version",
        )

    if (
        str(get_path(discovery, "script_identity.sha256", "")).lower()
        != EXPECTED_DISCOVERY_SHA256
    ):
        add(
            "SELF_HASH_MISMATCH",
            "critical",
            "FAIL",
            "Discovery self-hash does not match operator v1.2.1.",
            "discovery.script_identity.sha256",
        )
    if (
        get_path(
            discovery,
            "script_identity.microsoft_parser.error_count",
        )
        != 0
    ):
        add(
            "SELF_PARSER_ERRORS",
            "critical",
            "FAIL",
            "Discovery self-parser reported errors.",
            "discovery.script_identity.microsoft_parser",
        )

    expected_safety = {
        "read_only_intent": True,
        "sqlite_connection_performed": False,
        "repository_module_import_performed": False,
        "opend_connection_performed": False,
        "process_or_task_state_changed": False,
        "git_mutation_performed": False,
        "output_file_created_by_script": False,
        "preflight_executed": False,
        "production_drill_executed": False,
        "cutover_executed": False,
    }
    safety = discovery.get("safety")
    if not isinstance(safety, dict):
        add(
            "MISSING_SAFETY",
            "critical",
            "FAIL",
            "Safety declaration is missing.",
            "discovery.safety",
        )
    else:
        mismatches = {
            key: {"expected": expected, "actual": safety.get(key)}
            for key, expected in expected_safety.items()
            if safety.get(key) is not expected
        }
        if mismatches:
            add(
                "SAFETY_MISMATCH",
                "critical",
                "FAIL",
                f"Read-only contract mismatch: {mismatches}",
                "discovery.safety",
            )

    authorization = discovery.get("authorization")
    if not isinstance(authorization, dict) or authorization != {
        "production_readiness": "BLOCKED",
        "preflight_authorized": False,
        "production_drill_authorized": False,
        "cutover_authorized": False,
    }:
        add(
            "AUTHORIZATION_BOUNDARY_MISMATCH",
            "critical",
            "FAIL",
            "Discovery authorization boundary is not fail-closed.",
            "discovery.authorization",
        )

    commands = command_availability_map(discovery)
    missing_required = sorted(
        name for name in REQUIRED_COMMANDS if not commands.get(name, False)
    )
    if missing_required:
        add(
            "MISSING_REQUIRED_COMMANDS",
            "high",
            "FAIL",
            "Required commands unavailable: " + ", ".join(missing_required),
            "discovery.command_availability",
        )
    missing_optional = sorted(
        name for name in OPTIONAL_COMMANDS if not commands.get(name, False)
    )
    if missing_optional:
        add(
            "OPTIONAL_EVIDENCE_UNAVAILABLE",
            "medium",
            "PENDING_HUMAN",
            "Optional external/session evidence unavailable: "
            + ", ".join(missing_optional),
            "discovery.command_availability",
        )

    repo = get_path(discovery, "repositories.preflight_candidate")
    if not isinstance(repo, dict) or is_error_object(repo):
        add(
            "REPO_SNAPSHOT_INVALID",
            "critical",
            "FAIL",
            "Verified checkout snapshot is unavailable.",
            "discovery.repositories.preflight_candidate",
        )
    else:
        if repo.get("status") != "PASS":
            add(
                "REPO_NOT_APPROVED",
                "critical",
                "FAIL",
                f"Verified checkout status is {repo.get('status')!r}.",
                "discovery.repositories.preflight_candidate",
            )
        if repo.get("drill_script_exists") is not True:
            add(
                "DRILL_SCRIPT_MISSING",
                "critical",
                "FAIL",
                "Recovery drill script is absent from the verified checkout.",
                "discovery.repositories.preflight_candidate.drill_script_exists",
            )

    configs = parseable_configs(discovery)
    if not configs:
        add(
            "NO_PARSEABLE_CONFIG",
            "critical",
            "FAIL",
            "No parseable config.yaml candidate was found.",
            "discovery.config_candidates",
        )
    elif len(configs) > 1:
        add(
            "MULTIPLE_CONFIG_CANDIDATES",
            "high",
            "PENDING_HUMAN",
            f"{len(configs)} parseable config candidates require operator selection.",
            "discovery.config_candidates",
        )
    else:
        add(
            "ONE_CONFIG_CANDIDATE",
            "medium",
            "PENDING_HUMAN",
            "One config candidate exists but still requires launch-source confirmation.",
            "discovery.config_candidates[0]",
        )

    machine_rows = machine_observed_runtime_rows(discovery)
    human_rows = human_asserted_runtime_rows(discovery)
    if not machine_rows and not human_rows:
        add(
            "NO_USABLE_RUNTIME_DIRECTORY",
            "critical",
            "FAIL",
            "No machine-observed or explicitly asserted runtime directory was captured.",
            "discovery.runtime_working_directory_candidates",
        )
    elif human_rows and not machine_rows:
        add(
            "ONLY_HUMAN_ASSERTED_RUNTIME",
            "high",
            "PENDING_HUMAN",
            "Runtime directory evidence is human-asserted only; it is not machine-observed.",
            "discovery.runtime_working_directory_candidates",
        )

    mappings = existing_runtime_mappings(discovery)
    machine_mappings = [
        row for row in mappings if mapping_has_class(row, "machine_observed")
    ]
    human_mappings = [
        row for row in mappings if mapping_has_class(row, "human_asserted")
    ]
    supported_mappings = machine_mappings + human_mappings
    db_paths = unique_existing_db_paths(supported_mappings)
    if not supported_mappings:
        add(
            "NO_EXISTING_SUPPORTED_DB_MAPPING",
            "critical",
            "FAIL",
            "No machine-observed or human-asserted runtime/config combination resolved to an existing DB file.",
            "discovery.runtime_path_evidence",
        )
    elif len(db_paths) > 1:
        add(
            "MULTIPLE_LIVE_DB_PATHS",
            "critical",
            "CONFLICT",
            f"Supported runtime evidence resolves to {len(db_paths)} existing DB paths.",
            "discovery.runtime_path_evidence",
        )
    elif machine_mappings:
        add(
            "SINGLE_MACHINE_OBSERVED_DB_MAPPING",
            "medium",
            "PENDING_HUMAN",
            "A single existing DB path has machine-observed runtime support, but the operator must still confirm it is production.",
            "discovery.runtime_path_evidence",
        )
    else:
        add(
            "SINGLE_HUMAN_ASSERTED_DB_MAPPING",
            "high",
            "PENDING_HUMAN",
            "A single existing DB path is based only on human-asserted runtime input; production identity remains inconclusive.",
            "discovery.runtime_path_evidence",
        )

    services = non_error_rows(
        get_path(discovery, "runtime_writer_candidates.services")
    )
    if services:
        add(
            "SERVICE_RUNTIME_CONTEXT_REQUIRES_REVIEW",
            "high",
            "PENDING_HUMAN",
            "Relevant Windows services exist; service PathName does not by itself prove process working directory.",
            "discovery.runtime_writer_candidates.services",
        )
    processes = non_error_rows(
        get_path(discovery, "runtime_writer_candidates.processes")
    )
    if processes:
        add(
            "PROCESS_RUNTIME_CONTEXT_REQUIRES_REVIEW",
            "high",
            "PENDING_HUMAN",
            "Relevant processes exist; Win32_Process does not expose current working directory.",
            "discovery.runtime_writer_candidates.processes",
        )

    external = get_path(
        discovery, "runtime_writer_candidates.external_runtime"
    )
    if not isinstance(external, dict) or is_error_object(external):
        add(
            "EXTERNAL_RUNTIME_EVIDENCE_INVALID",
            "medium",
            "PENDING_HUMAN",
            "WSL, Docker, or user-session evidence is unavailable.",
            "discovery.runtime_writer_candidates.external_runtime",
        )
    add(
        "REMOTE_HOST_NOT_PROVABLE",
        "high",
        "PENDING_HUMAN",
        "Local discovery cannot prove that no other PC writes the database.",
        "discovery.runtime_writer_candidates.external_runtime.remote_host_limitation",
    )

    return build_review(findings, source_sha256, gate, discovery)


def markdown_summary(review: dict[str, Any]) -> str:
    facts = review.get("facts", {})
    lines = [
        "# moomoo production discovery review",
        "",
        f"- Operator version: `{review.get('operator_version')}`",
        f"- Validation: **{review.get('validation_status')}**",
        f"- Machine validation: **{review.get('machine_validation_status')}**",
        f"- Human validation: **{review.get('human_validation_status')}**",
        f"- Operational validation: **{review.get('operational_validation_status')}**",
        f"- Production readiness: **{review.get('production_readiness')}**",
        f"- Preflight authorized: `{review.get('preflight_authorized')}`",
        f"- Production drill authorized: `{review.get('production_drill_authorized')}`",
        f"- Cutover authorized: `{review.get('cutover_authorized')}`",
        f"- Next action: `{review.get('next_action')}`",
        "",
        "## Evidence counts",
        "",
        f"- Repository HEAD: `{facts.get('repo_head')}`",
        f"- Repository clean: `{facts.get('repo_clean')}`",
        f"- Parseable config candidates: `{facts.get('parseable_config_candidate_count')}`",
        f"- Machine-observed runtime candidates: `{facts.get('machine_observed_runtime_candidate_count')}`",
        f"- Human-asserted runtime candidates: `{facts.get('human_asserted_runtime_candidate_count')}`",
        f"- Derived runtime candidates: `{facts.get('derived_runtime_candidate_count')}`",
        f"- Existing runtime mappings: `{facts.get('existing_runtime_mapping_count')}`",
        f"- Unique supported existing DB paths: `{facts.get('unique_existing_database_path_count')}`",
        f"- Writer processes: `{facts.get('writer_process_count')}`",
        f"- Scheduled Tasks: `{facts.get('scheduled_task_count')}`",
        f"- Services: `{facts.get('service_count')}`",
        "",
        "## Findings",
        "",
    ]
    for item in review.get("findings", []):
        lines.append(
            f"- **{item.get('status')} / {item.get('severity')} / {item.get('code')}**: "
            f"{item.get('message')}"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "A machine PASS is not an operational approval. This review never authorizes `-PreflightOnly`, production backup/restore, cutover, or Issue #27 closure. Human confirmation of the production host, launch source, working directory, active config, exact live DB, every writer, other hosts, storage, and maintenance window remains mandatory.",
            "",
        ]
    )
    return "\n".join(lines)
