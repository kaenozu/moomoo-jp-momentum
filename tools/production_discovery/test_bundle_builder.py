from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = (
    ROOT / "scripts" / "build_moomoo_discovery_operator_bundle.py"
)
COMPARE_PATH = (
    ROOT / "scripts" / "compare_moomoo_discovery_operator_bundles.py"
)
REPOSITORY_TESTS_AVAILABLE = (
    BUILDER_PATH.is_file() and COMPARE_PATH.is_file()
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = (
    load_module("moomoo_bundle_builder", BUILDER_PATH)
    if REPOSITORY_TESTS_AVAILABLE
    else None
)
comparer = (
    load_module("moomoo_bundle_comparer", COMPARE_PATH)
    if REPOSITORY_TESTS_AVAILABLE
    else None
)
operator_common = load_module(
    "moomoo_operator_common_for_bundle_test",
    Path(__file__).with_name("moomoo_operator_common.py"),
)


def write_fixture_bundle(
    path: Path,
    payload: bytes = b"payload\n",
) -> None:
    payload_hash = hashlib.sha256(payload).hexdigest()
    sums = f"{payload_hash}  payload.txt\n".encode("utf-8")
    manifest = {
        "report_type": (
            "moomoo_discovery_operator_bundle_manifest"
        ),
        "operator_version": "1.2.2",
        "source_commit": "a" * 40,
        "source_ref": "refs/pull/33/merge",
        "authorization": {
            "production_readiness": "BLOCKED",
            "preflight_authorized": False,
            "production_drill_authorized": False,
            "cutover_authorized": False,
        },
    }
    members = {
        "payload.txt": payload,
        "SHA256SUMS.txt": sums,
        "bundle-manifest.json": (
            json.dumps(manifest, sort_keys=True) + "\n"
        ).encode("utf-8"),
    }
    with zipfile.ZipFile(
        path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(
                name, date_time=(2026, 7, 13, 0, 0, 0)
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)


@unittest.skipUnless(
    REPOSITORY_TESTS_AVAILABLE,
    "repository-only builder/comparer sources are not in operator bundle",
)
class BundleBuilderTests(unittest.TestCase):
    def test_checkout_line_endings_do_not_change_blob_equivalence(
        self,
    ) -> None:
        assert builder is not None
        blob = b"first\nsecond\n"
        checkout = b"first\r\nsecond\r\n"
        self.assertTrue(
            builder.checkout_matches_tracked_blob(checkout, blob)
        )

    def test_substantive_change_is_not_line_ending_change(
        self,
    ) -> None:
        assert builder is not None
        blob = b"first\nsecond\n"
        checkout = b"first\r\nchanged\r\n"
        self.assertFalse(
            builder.checkout_matches_tracked_blob(checkout, blob)
        )

    def assert_frozen_blob_hash(
        self,
        filename: str,
        expected: str,
    ) -> None:
        assert builder is not None
        path = Path(__file__).with_name(filename)
        blob = builder.read_tracked_blob(path)
        self.assertEqual(
            hashlib.sha256(blob).hexdigest(), expected
        )

    def test_all_frozen_discovery_hashes_use_git_blobs(self) -> None:
        for filename, expected in (
            operator_common.EXPECTED_BUNDLE_FILE_SHA256.items()
        ):
            with self.subTest(filename=filename):
                self.assert_frozen_blob_hash(filename, expected)

    def test_gate_hash_uses_git_blob(self) -> None:
        self.assert_frozen_blob_hash(
            operator_common.GATE_FILENAME,
            operator_common.EXPECTED_GATE_SHA256,
        )

    def test_identical_bundles_compare_equal(self) -> None:
        assert comparer is not None
        with tempfile.TemporaryDirectory() as root:
            left = Path(root) / "left.zip"
            right = Path(root) / "right.zip"
            write_fixture_bundle(left)
            write_fixture_bundle(right)
            report = comparer.compare_bundles(left, right)
            self.assertTrue(report["passed"])
            self.assertTrue(
                report["comparison"]["outer_sha256_equal"]
            )

    def test_different_bundles_are_rejected(self) -> None:
        assert comparer is not None
        with tempfile.TemporaryDirectory() as root:
            left = Path(root) / "left.zip"
            right = Path(root) / "right.zip"
            write_fixture_bundle(left, b"first\n")
            write_fixture_bundle(right, b"second\n")
            report = comparer.compare_bundles(left, right)
            self.assertFalse(report["passed"])
            self.assertIn(
                "payload.txt",
                report["comparison"]["differing_members"],
            )


if __name__ == "__main__":
    unittest.main()
