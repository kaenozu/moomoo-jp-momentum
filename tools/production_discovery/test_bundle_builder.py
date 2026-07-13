from __future__ import annotations

import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = ROOT / "scripts" / "build_moomoo_discovery_operator_bundle.py"
spec = importlib.util.spec_from_file_location("moomoo_bundle_builder", BUILDER_PATH)
assert spec and spec.loader
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)

OPERATOR_COMMON = Path(__file__).with_name("moomoo_operator_common.py")
operator_spec = importlib.util.spec_from_file_location(
    "moomoo_operator_common_for_bundle_test", OPERATOR_COMMON
)
assert operator_spec and operator_spec.loader
operator_common = importlib.util.module_from_spec(operator_spec)
sys.modules[operator_spec.name] = operator_common
operator_spec.loader.exec_module(operator_common)


class BundleBuilderTests(unittest.TestCase):
    def test_checkout_line_endings_do_not_change_blob_equivalence(self) -> None:
        blob = b"first\nsecond\n"
        checkout = b"first\r\nsecond\r\n"
        self.assertTrue(builder.checkout_matches_tracked_blob(checkout, blob))

    def test_substantive_change_is_not_treated_as_line_ending_change(self) -> None:
        blob = b"first\nsecond\n"
        checkout = b"first\r\nchanged\r\n"
        self.assertFalse(builder.checkout_matches_tracked_blob(checkout, blob))

    def test_frozen_hash_uses_exact_git_blob_bytes(self) -> None:
        discovery = Path(__file__).with_name(operator_common.DISCOVERY_FILENAME)
        blob = builder.read_tracked_blob(discovery)
        self.assertEqual(
            hashlib.sha256(blob).hexdigest(),
            operator_common.EXPECTED_DISCOVERY_SHA256,
        )

    def test_gate_hash_uses_exact_git_blob_bytes(self) -> None:
        gate = Path(__file__).with_name(operator_common.GATE_FILENAME)
        blob = builder.read_tracked_blob(gate)
        self.assertEqual(
            hashlib.sha256(blob).hexdigest(),
            operator_common.EXPECTED_GATE_SHA256,
        )


if __name__ == "__main__":
    unittest.main()
