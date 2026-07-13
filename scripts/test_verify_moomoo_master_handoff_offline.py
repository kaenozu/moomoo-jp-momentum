from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from unittest import mock
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = load_module(
    "offline_handoff_verifier",
    HERE / "verify_moomoo_master_handoff_offline.py",
)


class OfflineHandoffVerifierTests(unittest.TestCase):
    head = "a" * 40

    def make_zip(self, members: dict[str, bytes]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(members.items()):
                archive.writestr(name, data)
        return output.getvalue()

    def make_operator(
        self,
        *,
        source_ref: str = "refs/heads/master",
        authorization: dict[str, object] | None = None,
        omit: str | None = None,
        extra: tuple[str, bytes] | None = None,
    ) -> bytes:
        source_names = verifier.OPERATOR_MEMBERS - {
            "bundle-manifest.json",
            "SHA256SUMS.txt",
        }
        payload = {
            name: f"payload:{name}\n".encode("utf-8")
            for name in sorted(source_names)
            if name != omit
        }
        sums = b"".join(
            f"{verifier.sha256_bytes(data)}  {name}\n".encode("utf-8")
            for name, data in sorted(payload.items())
        )
        files = {
            name: verifier.sha256_bytes(data)
            for name, data in payload.items()
        }
        files["SHA256SUMS.txt"] = verifier.sha256_bytes(sums)
        manifest = {
            "report_type": "moomoo_discovery_operator_bundle_manifest",
            "operator_version": verifier.OPERATOR_VERSION,
            "source_commit": self.head,
            "source_ref": source_ref,
            "authorization": authorization or verifier.AUTHORIZATION,
            "files": files,
        }
        members = {
            **payload,
            "SHA256SUMS.txt": sums,
            "bundle-manifest.json": json.dumps(manifest).encode("utf-8"),
        }
        if extra:
            members[extra[0]] = extra[1]
        return self.make_zip(members)

    def make_handoff(
        self,
        *,
        source_ref: str = "refs/heads/master",
        authorization: dict[str, object] | None = None,
        operator: bytes | None = None,
        tamper_after_sums: str | None = None,
    ) -> bytes:
        operator = operator or self.make_operator(source_ref=source_ref)
        payload_names = verifier.HANDOFF_MEMBERS - {
            "HANDOFF_MANIFEST.json",
            "HANDOFF_SHA256SUMS.txt",
            verifier.OPERATOR_BUNDLE_NAME,
        }
        payload = {
            name: f"payload:{name}\n".encode("utf-8")
            for name in sorted(payload_names)
        }
        payload[verifier.OPERATOR_BUNDLE_NAME] = operator
        manifest = {
            "report_type": "moomoo_readonly_discovery_handoff_manifest",
            "schema_version": verifier.HANDOFF_FORMAT_VERSION,
            "handoff_format_version": verifier.HANDOFF_FORMAT_VERSION,
            "handoff_package_version": verifier.HANDOFF_VERSION,
            "handoff_version": verifier.HANDOFF_VERSION,
            "operator_version": verifier.OPERATOR_VERSION,
            "source_commit": self.head,
            "source_ref": source_ref,
            "expected_checkout_head": self.head,
            "expected_remote": verifier.EXPECTED_REMOTE,
            "operator_bundle": {
                "name": verifier.OPERATOR_BUNDLE_NAME,
                "sha256": verifier.sha256_bytes(operator),
                "source_commit": self.head,
                "source_ref": source_ref,
            },
            "payload_files": {
                name: verifier.sha256_bytes(data)
                for name, data in payload.items()
            },
            "authorization": authorization or verifier.AUTHORIZATION,
            "distribution_policy": verifier.DISTRIBUTION_POLICY,
        }
        members = {
            **payload,
            "HANDOFF_MANIFEST.json": json.dumps(manifest).encode("utf-8"),
        }
        sums = b"".join(
            f"{verifier.sha256_bytes(data)}  {name}\n".encode("utf-8")
            for name, data in sorted(members.items())
        )
        members["HANDOFF_SHA256SUMS.txt"] = sums
        if tamper_after_sums:
            members[tamper_after_sums] += b"tampered"
        return self.make_zip(members)

    def verify_bytes(self, data: bytes, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.zip"
            path.write_bytes(data)
            return verifier.verify_handoff(path, **kwargs)

    def test_accepts_direct_master_handoff(self) -> None:
        data = self.make_handoff()
        report = self.verify_bytes(
            data,
            expected_handoff_sha256=verifier.sha256_bytes(data),
            expected_source_commit=self.head,
        )
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["actions_wrapper"])

    def test_accepts_single_file_actions_wrapper(self) -> None:
        handoff = self.make_handoff()
        wrapper = self.make_zip(
            {"moomoo-readonly-discovery-handoff-v1.2.2.zip": handoff}
        )
        report = self.verify_bytes(
            wrapper,
            expected_input_sha256=verifier.sha256_bytes(wrapper),
            expected_handoff_sha256=verifier.sha256_bytes(handoff),
        )
        self.assertTrue(report["actions_wrapper"])

    def test_rejects_wrapper_with_extra_member(self) -> None:
        handoff = self.make_handoff()
        wrapper = self.make_zip(
            {
                "moomoo-readonly-discovery-handoff-v1.2.2.zip": handoff,
                "unexpected.txt": b"unexpected",
            }
        )
        with self.assertRaises(verifier.VerificationError):
            self.verify_bytes(wrapper)

    def test_rejects_handoff_checksum_tampering(self) -> None:
        with self.assertRaises(verifier.VerificationError):
            self.verify_bytes(
                self.make_handoff(tamper_after_sums="README_FIRST.md")
            )

    def test_rejects_open_authorization(self) -> None:
        authorization = dict(verifier.AUTHORIZATION)
        authorization["preflight_authorized"] = True
        with self.assertRaises(verifier.VerificationError):
            self.verify_bytes(self.make_handoff(authorization=authorization))

    def test_rejects_validation_ref_by_default(self) -> None:
        data = self.make_handoff(source_ref="refs/pull/40/merge")
        with self.assertRaises(verifier.VerificationError):
            self.verify_bytes(data)
        report = self.verify_bytes(data, require_master=False)
        self.assertEqual(report["status"], "PASS")

    def test_rejects_operator_missing_common_error_test(self) -> None:
        operator = self.make_operator(omit="test_moomoo_operator_common_errors.py")
        with self.assertRaises(verifier.VerificationError):
            self.verify_bytes(self.make_handoff(operator=operator))

    def test_rejects_unexpected_operator_member(self) -> None:
        operator = self.make_operator(extra=("unexpected.py", b"x"))
        with self.assertRaises(verifier.VerificationError):
            self.verify_bytes(self.make_handoff(operator=operator))

    def test_rejects_expected_source_mismatch(self) -> None:
        with self.assertRaises(verifier.VerificationError):
            self.verify_bytes(
                self.make_handoff(), expected_source_commit="b" * 40
            )

    def test_rejects_case_colliding_entries(self) -> None:
        data = self.make_handoff()
        with zipfile.ZipFile(io.BytesIO(data), "r") as original:
            members = {name: original.read(name) for name in original.namelist()}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in members.items():
                archive.writestr(name, content)
            archive.writestr("readme_first.md", b"collision")
        with self.assertRaises(verifier.VerificationError):
            self.verify_bytes(output.getvalue())

    def test_rejects_oversized_input_before_zip_parsing(self) -> None:
        with mock.patch.object(verifier, "MAX_INPUT_BYTES", 100):
            with self.assertRaisesRegex(verifier.VerificationError, "input file exceeds"):
                self.verify_bytes(b"x" * 101)

    def test_rejects_oversized_wrapper_member(self) -> None:
        handoff = self.make_handoff()
        wrapper = self.make_zip(
            {"moomoo-readonly-discovery-handoff-v1.2.2.zip": handoff}
        )
        with mock.patch.object(verifier, "MAX_HANDOFF_BYTES", len(handoff) - 1):
            with self.assertRaisesRegex(verifier.VerificationError, "member is too large"):
                self.verify_bytes(wrapper)

    def test_rejects_oversized_nested_operator(self) -> None:
        operator = self.make_operator()
        handoff = self.make_handoff(operator=operator)
        with mock.patch.object(verifier, "MAX_OPERATOR_BYTES", len(operator) - 1):
            with self.assertRaisesRegex(verifier.VerificationError, "member is too large"):
                self.verify_bytes(handoff)

    def test_rejects_excessive_compression_ratio(self) -> None:
        compressed = self.make_zip({"payload.txt": b"A" * 10000})
        with mock.patch.object(verifier, "MAX_COMPRESSION_RATIO", 2.0):
            with self.assertRaisesRegex(verifier.VerificationError, "compression ratio"):
                verifier.open_verified_zip(
                    compressed,
                    "ratio test",
                    max_archive_bytes=len(compressed),
                    max_members=1,
                    max_member_bytes=20000,
                )

    def test_rejects_resource_member_count_overflow(self) -> None:
        data = self.make_zip({"a.txt": b"a", "b.txt": b"b"})
        with self.assertRaisesRegex(verifier.VerificationError, "too many members"):
            verifier.open_verified_zip(
                data,
                "member count test",
                max_archive_bytes=len(data),
                max_members=1,
                max_member_bytes=10,
            )

    def test_cli_refuses_to_overwrite_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "handoff.zip"
            output = root / "report.json"
            artifact.write_bytes(self.make_handoff())
            output.write_text("existing", encoding="utf-8")
            result = verifier.main([str(artifact), "--output", str(output)])
            self.assertEqual(result, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
