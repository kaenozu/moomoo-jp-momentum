from __future__ import annotations

import unittest

from moomoo_operator_common import is_error_object, non_error_rows


class ErrorRowClassificationTests(unittest.TestCase):
    def test_null_error_fields_do_not_discard_successful_gate_rows(self) -> None:
        row = {
            "name": "moomoo_production_readonly_discovery_v4.ps1",
            "actual_sha256": "a" * 64,
            "error": None,
        }
        self.assertFalse(is_error_object(row))
        self.assertEqual(non_error_rows([row]), [row])

    def test_empty_error_fields_do_not_discard_successful_rows(self) -> None:
        row = {"name": "git", "available": True, "invocation_error": ""}
        self.assertFalse(is_error_object(row))
        self.assertEqual(non_error_rows([row]), [row])

    def test_nonempty_error_fields_are_rejected(self) -> None:
        rows = [
            {"name": "ok", "error": None},
            {"name": "failed", "error": "access denied"},
            {"name": "failed-type", "error_type": "RuntimeError"},
            {"name": "failed-invocation", "invocation_error": "not found"},
        ]
        self.assertEqual(non_error_rows(rows), [rows[0]])


if __name__ == "__main__":
    unittest.main()
