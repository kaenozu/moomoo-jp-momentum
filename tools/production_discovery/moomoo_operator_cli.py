from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from moomoo_operator_common import (
    DEFAULT_EXPECTED_REMOTE,
    DISCOVERY_FILENAME,
    EXPECTED_BUNDLE_FILE_SHA256,
    EXPECTED_DISCOVERY_SHA256,
    EXPECTED_GATE_SHA256,
    GATE_FILENAME,
    VERSION,
    OperatorError,
    load_json_bytes,
    load_json_file,
    redact_payload,
    sha256_file,
    utc_now,
    validate_output_root,
    write_json,
)
from moomoo_operator_review import markdown_summary, review_payload


def run_process(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(args),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
    )


def make_evidence_dir(output_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    evidence_dir = output_root / f"moomoo-discovery-{stamp}"
    evidence_dir.mkdir(parents=False, exist_ok=False)
    return evidence_dir


def manifest_payload(args: argparse.Namespace, evidence_dir: Path, bundle_dir: Path) -> dict[str, Any]:
    return {
        "report_type": "moomoo_discovery_operator_manifest",
        "operator_version": VERSION,
        "created_at": utc_now(),
        "bundle_dir": str(bundle_dir),
        "evidence_dir": str(evidence_dir),
        "repo_path": args.repo_path,
        "protected_checkout_path": args.protected_checkout_path,
        "expected_head": args.expected_head,
        "expected_remote": args.expected_remote,
        "config_search_roots": args.config_search_root,
        "production_working_directory_candidates": args.production_working_directory,
        "safety": {
            "sqlite_connection_performed": False,
            "writer_state_changed": False,
            "git_mutation_performed": False,
            "preflight_executed": False,
            "production_drill_executed": False,
            "cutover_executed": False,
        },
    }


def run_command(args: argparse.Namespace) -> int:
    bundle_dir = Path(args.bundle_dir).resolve()
    output_root = Path(args.output_root).resolve()
    repo_path = Path(args.repo_path).resolve(strict=False)
    protected_path = Path(args.protected_checkout_path).resolve(strict=False)
    config_search_roots = [
        str(Path(value).resolve(strict=False)) for value in args.config_search_root
    ]
    runtime_directories = [
        str(Path(value).resolve(strict=False))
        for value in args.production_working_directory
    ]
    validate_output_root(output_root, bundle_dir, repo_path, protected_path)

    gate_path = bundle_dir / GATE_FILENAME
    expected_files = dict(EXPECTED_BUNDLE_FILE_SHA256)
    expected_files[GATE_FILENAME] = EXPECTED_GATE_SHA256
    for name, expected_hash in expected_files.items():
        path = bundle_dir / name
        if not path.is_file():
            raise OperatorError(f"Required bundle file is missing: {path}")
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise OperatorError(
                f"Bundle SHA-256 mismatch for {name}: "
                f"expected {expected_hash}, got {actual_hash}"
            )

    evidence_dir = make_evidence_dir(output_root)
    write_json(evidence_dir / "00-manifest.json", manifest_payload(args, evidence_dir, bundle_dir))

    ps_args = [
        args.powershell,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(gate_path),
        "-ExpectedFileHashesJson",
        json.dumps(EXPECTED_BUNDLE_FILE_SHA256, ensure_ascii=False, sort_keys=True),
        "-RunDiscovery",
        "-RepoPath",
        str(repo_path),
        "-ProtectedCheckoutPath",
        str(protected_path),
        "-ExpectedHead",
        args.expected_head,
        "-ExpectedRemote",
        args.expected_remote,
        "-ConfigSearchRootsJson",
        json.dumps(config_search_roots, ensure_ascii=False),
        "-ProductionWorkingDirectoryCandidatesJson",
        json.dumps(runtime_directories, ensure_ascii=False),
    ]

    completed = run_process(ps_args, bundle_dir)
    (evidence_dir / "01-discovery-stdout.bin").write_bytes(completed.stdout)
    (evidence_dir / "01-discovery-stderr.bin").write_bytes(completed.stderr)

    try:
        payload = load_json_bytes(completed.stdout, "PowerShell stdout")
    except OperatorError as exc:
        result = {
            "report_type": "moomoo_discovery_operator_result",
            "operator_version": VERSION,
            "status": "blocked",
            "operator_exit_code": 1,
            "validation_status": "BLOCKED",
            "production_readiness": "BLOCKED",
            "preflight_authorized": False,
            "production_drill_authorized": False,
            "cutover_authorized": False,
            "evidence_complete": False,
            "error": str(exc),
            "powershell_exit_code": completed.returncode,
        }
        write_json(evidence_dir / "05-operator-result.json", result)
        return 1

    write_json(evidence_dir / "01-gated-discovery.json", payload)
    review = review_payload(payload, sha256_file(evidence_dir / "01-gated-discovery.json"))
    write_json(evidence_dir / "02-discovery-review.json", review)
    write_json(evidence_dir / "03-discovery-redacted.json", redact_payload(payload))
    (evidence_dir / "04-discovery-summary.md").write_text(
        markdown_summary(review), encoding="utf-8", newline="\n"
    )

    correction_required = review["validation_status"] in {
        "CORRECTION_REQUIRED",
        "INCONCLUSIVE",
    }
    operator_exit_code = 2 if correction_required else 0
    status = (
        "completed_with_corrections_required"
        if correction_required
        else "completed_readonly_discovery"
    )
    if completed.returncode != 0:
        operator_exit_code = 1
        status = "blocked"

    required_names = {
        "00-manifest.json",
        "01-discovery-stdout.bin",
        "01-discovery-stderr.bin",
        "01-gated-discovery.json",
        "02-discovery-review.json",
        "03-discovery-redacted.json",
        "04-discovery-summary.md",
    }
    evidence_complete = all((evidence_dir / name).exists() for name in required_names)
    result = {
        "report_type": "moomoo_discovery_operator_result",
        "operator_version": VERSION,
        "created_at": utc_now(),
        "status": status,
        "operator_exit_code": operator_exit_code,
        "powershell_exit_code": completed.returncode,
        "validation_status": review["validation_status"],
        "production_readiness": "BLOCKED",
        "preflight_authorized": False,
        "production_drill_authorized": False,
        "cutover_authorized": False,
        "evidence_complete": evidence_complete,
        "evidence_directory": str(evidence_dir),
        "next_action": review["next_action"],
    }
    write_json(evidence_dir / "05-operator-result.json", result)
    return operator_exit_code


def review_command(args: argparse.Namespace) -> int:
    input_path = Path(args.input).resolve()
    payload = load_json_file(input_path)
    review = review_payload(payload, sha256_file(input_path))
    if args.output:
        write_json(Path(args.output).resolve(), review)
    else:
        print(json.dumps(review, ensure_ascii=False, indent=2))
    return 2 if review["validation_status"] != "PASS" else 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run gated read-only discovery and retain evidence")
    run.add_argument("--bundle-dir", required=True)
    run.add_argument("--output-root", required=True)
    run.add_argument("--repo-path", required=True)
    run.add_argument("--protected-checkout-path", required=True)
    run.add_argument("--expected-head", required=True)
    run.add_argument("--expected-remote", default=DEFAULT_EXPECTED_REMOTE)
    run.add_argument("--config-search-root", action="append", required=True)
    run.add_argument("--production-working-directory", action="append", default=[])
    run.add_argument("--powershell", default="powershell.exe")
    run.set_defaults(func=run_command)

    review = sub.add_parser("review", help="Review a retained gated discovery JSON")
    review.add_argument("--input", required=True)
    review.add_argument("--output")
    review.set_defaults(func=review_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        return 130
