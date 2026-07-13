from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("moomoo_discovery_operator.py")
spec = importlib.util.spec_from_file_location("moomoo_discovery_operator", MODULE_PATH)
assert spec and spec.loader
operator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = operator
spec.loader.exec_module(operator)


def base_payload() -> dict:
    discovery_hash = operator.EXPECTED_DISCOVERY_SHA256
    return {
        "report_type": "moomoo_discovery_v4_gated_result",
        "gate": {
            "gate_passed": True,
            "parser_error_count": 0,
            "hash_matches": True,
            "actual_sha256": discovery_hash,
            "discovery_executed": True,
            "powershell": {"version": "5.1", "edition": "Desktop"},
        },
        "discovery_error": None,
        "discovery": {
            "report_type": "moomoo_production_readonly_discovery",
            "schema_version": 4,
            "captured_at": "2026-07-13T00:00:00Z",
            "script_identity": {
                "sha256": discovery_hash,
                "microsoft_parser": {"error_count": 0},
            },
            "safety": {
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
            },
            "authorization": {
                "production_readiness": "BLOCKED",
                "preflight_authorized": False,
                "production_drill_authorized": False,
                "cutover_authorized": False,
            },
            "machine": {"powershell": {"version": "5.1", "edition": "Desktop"}},
            "command_availability": [
                {"name": name, "available": True}
                for name in sorted(operator.REQUIRED_COMMANDS | operator.OPTIONAL_COMMANDS)
            ],
            "repositories": {
                "preflight_candidate": {
                    "status": "PASS",
                    "head": "a" * 40,
                    "clean": True,
                    "drill_script_exists": True,
                }
            },
            "config_candidates": [
                {
                    "path": r"C:\runtime\config.yaml",
                    "selected_values": {
                        "database_path_raw": r"data\moomoo.db",
                        "database_backup_directory_raw": "backups",
                    },
                }
            ],
            "runtime_working_directory_candidates": [
                {
                    "path": r"C:\runtime",
                    "authoritative": True,
                    "evidence": [
                        {
                            "source_type": "operator_explicit",
                            "source_id": r"C:\runtime",
                            "authoritative": True,
                        }
                    ],
                }
            ],
            "runtime_path_evidence": [
                {
                    "config_path": r"C:\runtime\config.yaml",
                    "production_working_directory": r"C:\runtime",
                    "runtime_authoritative": True,
                    "resolved_database_path": r"C:\runtime\data\moomoo.db",
                    "database_exists": True,
                    "resolution_error": None,
                }
            ],
            "runtime_writer_candidates": {
                "processes": [],
                "scheduled_tasks": [],
                "services": [],
                "startup_commands": [],
                "external_runtime": {
                    "remote_host_limitation": "cannot prove remote hosts"
                },
            },
            "database_file_candidates": [
                {"path": r"C:\runtime\data\moomoo.db"}
            ],
        },
    }


class OperatorTests(unittest.TestCase):
    def test_load_utf16_json(self) -> None:
        payload = {"ok": True}
        raw = json.dumps(payload).encode("utf-16")
        self.assertEqual(operator.load_json_bytes(raw, "fixture"), payload)

    def test_redaction(self) -> None:
        payload = {
            "user_name": "alice",
            "command": "python app.py --token secret",
            "path": r"C:\Users\alice\project",
            "email": "alice@example.com",
        }
        redacted = operator.redact_payload(payload)
        self.assertEqual(redacted["user_name"], "<REDACTED>")
        self.assertIn("<REDACTED>", redacted["command"])
        self.assertIn("<REDACTED_USER>", redacted["path"])
        self.assertEqual(redacted["email"], "<REDACTED_EMAIL>")

    def test_single_mapping_machine_validation_passes(self) -> None:
        review = operator.review_payload(base_payload(), "f" * 64)
        self.assertEqual(review["validation_status"], "PASS")
        self.assertEqual(review["production_readiness"], "BLOCKED")
        self.assertFalse(review["preflight_authorized"])
        codes = {item["code"] for item in review["findings"]}
        self.assertIn("SINGLE_EXISTING_DB_MAPPING", codes)
        self.assertIn("REMOTE_HOST_NOT_PROVABLE", codes)

    def test_no_authoritative_runtime_is_correction_required(self) -> None:
        payload = base_payload()
        payload["discovery"]["runtime_working_directory_candidates"] = []
        payload["discovery"]["runtime_path_evidence"] = []
        review = operator.review_payload(payload, "f" * 64)
        self.assertEqual(review["validation_status"], "CORRECTION_REQUIRED")
        codes = {item["code"] for item in review["findings"]}
        self.assertIn("NO_AUTHORITATIVE_RUNTIME_DIRECTORY", codes)
        self.assertIn("NO_EXISTING_AUTHORITATIVE_DB_MAPPING", codes)

    def test_multiple_existing_db_paths_is_conflict(self) -> None:
        payload = base_payload()
        payload["discovery"]["runtime_path_evidence"].append(
            {
                "config_path": r"C:\runtime\config.yaml",
                "production_working_directory": r"D:\runtime",
                "runtime_authoritative": True,
                "resolved_database_path": r"D:\runtime\data\moomoo.db",
                "database_exists": True,
                "resolution_error": None,
            }
        )
        review = operator.review_payload(payload, "f" * 64)
        self.assertEqual(review["validation_status"], "CORRECTION_REQUIRED")
        codes = {item["code"] for item in review["findings"]}
        self.assertIn("MULTIPLE_LIVE_DB_PATHS", codes)

    def test_hash_mismatch_is_correction_required(self) -> None:
        payload = base_payload()
        payload["gate"]["actual_sha256"] = "0" * 64
        payload["gate"]["hash_matches"] = False
        review = operator.review_payload(payload, "f" * 64)
        self.assertEqual(review["validation_status"], "CORRECTION_REQUIRED")

    def test_authorization_boundary_mismatch_fails(self) -> None:
        payload = base_payload()
        payload["discovery"]["authorization"]["preflight_authorized"] = True
        review = operator.review_payload(payload, "f" * 64)
        codes = {item["code"] for item in review["findings"]}
        self.assertIn("AUTHORIZATION_BOUNDARY_MISMATCH", codes)
        self.assertEqual(review["validation_status"], "CORRECTION_REQUIRED")

    def test_service_context_remains_inconclusive(self) -> None:
        payload = base_payload()
        payload["discovery"]["runtime_writer_candidates"]["services"] = [
            {"name": "moomoo", "path_name": "python app.py"}
        ]
        review = operator.review_payload(payload, "f" * 64)
        codes = {item["code"] for item in review["findings"]}
        self.assertIn("SERVICE_RUNTIME_CONTEXT_REQUIRES_REVIEW", codes)

    def test_output_root_must_be_separate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            bundle = base / "bundle"
            repo = base / "repo"
            protected = base / "protected"
            for path in (bundle, repo, protected):
                path.mkdir()
            with self.assertRaises(operator.OperatorError):
                operator.validate_output_root(bundle, bundle, repo, protected)

    def test_output_root_valid(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            bundle = base / "bundle"
            repo = base / "repo"
            protected = base / "protected"
            output = base / "output"
            for path in (bundle, repo, protected, output):
                path.mkdir()
            operator.validate_output_root(output, bundle, repo, protected)

    def test_parser_requires_expected_head(self) -> None:
        parser = operator.make_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "run",
                        "--bundle-dir",
                        ".",
                        "--output-root",
                        ".",
                        "--repo-path",
                        "repo",
                        "--protected-checkout-path",
                        "protected",
                        "--config-search-root",
                        "root",
                    ]
                )

    def test_markdown_repeats_no_authorization_boundary(self) -> None:
        review = operator.review_payload(base_payload(), "f" * 64)
        text = operator.markdown_summary(review)
        self.assertIn("Production readiness: **BLOCKED**", text)
        self.assertIn("Preflight authorized: `False`", text)
        self.assertIn("Production drill authorized: `False`", text)
        self.assertIn("Cutover authorized: `False`", text)


if __name__ == "__main__":
    unittest.main()
