from __future__ import annotations

import hashlib
import json
import locale
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VERSION = "1.2.1"
DISCOVERY_FILENAME = "moomoo_production_readonly_discovery_v4.ps1"
GATE_FILENAME = "moomoo_discovery_v4_gate.ps1"
DEFAULT_EXPECTED_REMOTE = "https://github.com/kaenozu/moomoo-jp-momentum.git"
EXPECTED_BUNDLE_FILE_SHA256: dict[str, str] = {
    "moomoo_production_readonly_discovery_v4.ps1": "1cc5961b434c68edae9a072bade38d59b063141c31e99585b496589691975d29",
    "moomoo_discovery_v4_common.ps1": "39b22a9dd5505ed3de2a1e7f4e80c40a7a9ec5a22adca6c6f72bca64c2a1a9c5",
    "moomoo_discovery_v4_runtime.ps1": "2e816d597d4823aa67df97808273d647812e0028162f60a844fc7df317b517d8",
    "moomoo_discovery_v4_storage.ps1": "7d6146150565d858595cbd12aef6a2f13f40eebe1890fe738df281657f8af79d",
}
EXPECTED_DISCOVERY_SHA256 = EXPECTED_BUNDLE_FILE_SHA256[DISCOVERY_FILENAME]
EXPECTED_GATE_SHA256 = "7f0e70ebd74e9ecf309ca804fb2863a3d3a54058d26543a0437dc570073eb612"

REQUIRED_COMMANDS = {
    "git", "python", "Get-CimInstance", "Get-ScheduledTask", "Get-Disk",
    "Get-Partition", "Get-Volume", "Get-FileHash",
}
OPTIONAL_COMMANDS = {
    "Get-SmbMapping", "Get-SmbShare", "Get-SmbOpenFile", "wsl.exe",
    "docker.exe", "quser.exe",
}


SECRET_KEY_RE = re.compile(
    r"(?i)(?:^|_)(password|passwd|pwd|token|api[_-]?key|secret|private[_-]?key|"
    r"access[_-]?key|authorization|cookie)(?:$|_)"
)
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
WINDOWS_USER_PATH_RE = re.compile(r"(?i)([A-Z]:\\Users\\)[^\\\s\"']+")
FLAG_SECRET_RE = re.compile(
    r"(?i)(--?(?:password|passwd|pwd|token|api[-_]?key|secret|access[-_]?key)"
    r"(?:\s+|=))([^\s\"']+|\"[^\"]*\"|'[^']*')"
)
ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret|access[_-]?key)="
    r"([^\s;&]+)"
)
IDENTITY_KEY_RE = re.compile(
    r"(?i)^(computer_name|user_name|user_domain|clientcomputername|"
    r"clientusername|startname|principal_user_id|user)$"
)
UNC_PATH_RE = re.compile(r"(?i)\\\\[^\\\s]+\\[^\\\s]+")


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    status: str
    message: str
    evidence: str | None = None


class OperatorError(RuntimeError):
    """Expected fail-closed operator error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def load_json_bytes(data: bytes, source: str) -> Any:
    encodings: list[str] = ["utf-8-sig", "utf-16"]
    preferred = locale.getpreferredencoding(False)
    if preferred:
        encodings.append(preferred)
    encodings.extend(["cp932", "utf-16-le", "utf-16-be"])
    errors: list[str] = []
    seen: set[str] = set()
    for encoding in encodings:
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            text = data.decode(encoding)
        except UnicodeError as exc:
            errors.append(f"{encoding}: decode failed: {exc}")
            continue
        text = text.lstrip("\ufeff\x00 \t\r\n")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append(f"{encoding}: JSON failed: {exc}")
    raise OperatorError(
        f"Could not decode JSON from {source}. Attempts: " + " | ".join(errors)
    )


def load_json_file(path: Path) -> Any:
    return load_json_bytes(path.read_bytes(), str(path))


def get_path(payload: Any, dotted_path: str, default: Any = None) -> Any:
    current = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def is_error_object(value: Any) -> bool:
    return isinstance(value, dict) and (
        "error" in value or "error_type" in value or "invocation_error" in value
    )


def non_error_rows(value: Any) -> list[dict[str, Any]]:
    return [
        row
        for row in as_list(value)
        if isinstance(row, dict) and not is_error_object(row)
    ]


def redact_string(value: str) -> str:
    value = EMAIL_RE.sub("<REDACTED_EMAIL>", value)
    value = WINDOWS_USER_PATH_RE.sub(r"\1<REDACTED_USER>", value)
    value = UNC_PATH_RE.sub(r"\\<REDACTED_SERVER>\<REDACTED_SHARE>", value)
    value = FLAG_SECRET_RE.sub(r"\1<REDACTED>", value)
    value = ASSIGNMENT_SECRET_RE.sub(r"\1=<REDACTED>", value)
    return value


def redact_payload(value: Any, key: str | None = None) -> Any:
    if key and (SECRET_KEY_RE.search(key) or IDENTITY_KEY_RE.search(key)):
        return "<REDACTED>"
    if isinstance(value, dict):
        return {str(k): redact_payload(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        return redact_string(value)
    return value


def path_key(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def is_same_or_parent(candidate: Path, target: Path) -> bool:
    candidate_resolved = candidate.resolve(strict=False)
    target_resolved = target.resolve(strict=False)
    return candidate_resolved == target_resolved or candidate_resolved in target_resolved.parents


def validate_output_root(output_root: Path, bundle_dir: Path, repo_path: Path, protected_path: Path) -> None:
    if not output_root.exists() or not output_root.is_dir():
        raise OperatorError(f"Output root must already exist: {output_root}")
    for protected, label in (
        (bundle_dir, "bundle directory"),
        (repo_path, "verified checkout"),
        (protected_path, "protected checkout"),
    ):
        if is_same_or_parent(output_root, protected) or is_same_or_parent(protected, output_root):
            raise OperatorError(
                f"Output root must be separate from the {label}: {output_root} vs {protected}"
            )


def command_availability_map(discovery: dict[str, Any]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for row in non_error_rows(discovery.get("command_availability")):
        if isinstance(row.get("name"), str):
            result[row["name"]] = bool(row.get("available"))
    return result


def parseable_configs(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in non_error_rows(discovery.get("config_candidates")):
        selected = row.get("selected_values")
        if isinstance(selected, dict) and not selected.get("parse_error"):
            rows.append(row)
    return rows


def runtime_rows_by_class(discovery: dict[str, Any], evidence_class: str) -> list[dict[str, Any]]:
    flag_name = {
        "machine_observed": "machine_observed",
        "human_asserted": "human_asserted",
        "derived_candidate": "derived_candidate",
    }[evidence_class]
    return [
        row
        for row in non_error_rows(discovery.get("runtime_working_directory_candidates"))
        if row.get(flag_name) is True
        or evidence_class in as_list(row.get("evidence_classes"))
    ]


def machine_observed_runtime_rows(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    rows = runtime_rows_by_class(discovery, "machine_observed")
    if rows:
        return rows
    return [
        row
        for row in non_error_rows(discovery.get("runtime_working_directory_candidates"))
        if row.get("authoritative") is True
    ]


def human_asserted_runtime_rows(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    return runtime_rows_by_class(discovery, "human_asserted")


def derived_runtime_rows(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    return runtime_rows_by_class(discovery, "derived_candidate")


def authoritative_runtime_rows(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    """Backward-compatible alias for machine-observed runtime evidence."""
    return machine_observed_runtime_rows(discovery)


def existing_runtime_mappings(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in non_error_rows(discovery.get("runtime_path_evidence"))
        if row.get("database_exists") is True and not row.get("resolution_error")
    ]


def mapping_has_class(row: dict[str, Any], evidence_class: str) -> bool:
    flag_name = {
        "machine_observed": "runtime_machine_observed",
        "human_asserted": "runtime_human_asserted",
        "derived_candidate": "runtime_derived_candidate",
    }[evidence_class]
    if row.get(flag_name) is True:
        return True
    if evidence_class == "machine_observed" and row.get("runtime_authoritative") is True:
        return True
    return any(
        evidence.get("evidence_class") == evidence_class
        for evidence in non_error_rows(row.get("runtime_evidence"))
    )


def unique_existing_db_paths(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {
        path_key(str(row["resolved_database_path"]))
        for row in rows
        if row.get("resolved_database_path")
    }
