from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
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


builder = load_module(
    "handoff_builder",
    HERE / "build_moomoo_readonly_discovery_handoff.py",
)
comparer = load_module(
    "handoff_comparer",
    HERE / "compare_moomoo_readonly_discovery_handoffs.py",
)


class HandoffBuilderTests(unittest.TestCase):
    def make_operator_bundle(
        self,
        path: Path,
        source_commit: str,
        *,
        authorization: dict[str, object] | None = None,
        duplicate: bool = False,
        corrupt_sum: bool = False,
    ) -> None:
        source_members = sorted(
            builder.OPERATOR_REQUIRED_MEMBERS
            - {"SHA256SUMS.txt", "bundle-manifest.json"}
        )
        payload = {
            name: (
                b"# operator readme\n"
                if name == "README_moomoo_discovery_operator_ja.md"
                else f"payload:{name}\n".encode("utf-8")
            )
            for name in source_members
        }
        sums = []
        for name in source_members:
            digest = builder.sha256_bytes(payload[name])
            if corrupt_sum and name == source_members[0]:
                digest = "0" * 64
            sums.append(f"{digest}  {name}\n")
        manifest = {
            "operator_version": builder.OPERATOR_VERSION,
            "source_commit": source_commit,
            "source_ref": "refs/heads/test",
            "authorization": authorization or builder.AUTHORIZATION,
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in payload.items():
                archive.writestr(name, data)
            archive.writestr("SHA256SUMS.txt", "".join(sums).encode())
            archive.writestr(
                "bundle-manifest.json",
                json.dumps(manifest).encode("utf-8"),
            )
            if duplicate:
                archive.writestr(source_members[0], b"duplicate")

    def test_parse_sha256sums_rejects_traversal(self) -> None:
        with self.assertRaises(builder.BuildError):
            builder.parse_sha256sums(
                ("0" * 64 + "  ../escape\n").encode(),
                "test",
            )

    def test_operator_bundle_accepts_valid_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operator.zip"
            head = "a" * 40
            self.make_operator_bundle(path, head)
            result = builder.inspect_operator_bundle(path, head)
            self.assertEqual(result["zip_sha256"], builder.sha256_file(path))

    def test_operator_bundle_rejects_duplicate_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operator.zip"
            self.make_operator_bundle(path, "a" * 40, duplicate=True)
            with self.assertRaises(builder.BuildError):
                builder.inspect_operator_bundle(path, "a" * 40)

    def test_operator_bundle_rejects_source_commit_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operator.zip"
            self.make_operator_bundle(path, "a" * 40)
            with self.assertRaises(builder.BuildError):
                builder.inspect_operator_bundle(path, "b" * 40)

    def test_operator_bundle_rejects_open_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operator.zip"
            authorization = dict(builder.AUTHORIZATION)
            authorization["preflight_authorized"] = True
            self.make_operator_bundle(
                path,
                "a" * 40,
                authorization=authorization,
            )
            with self.assertRaises(builder.BuildError):
                builder.inspect_operator_bundle(path, "a" * 40)

    def test_operator_bundle_rejects_internal_hash_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operator.zip"
            self.make_operator_bundle(path, "a" * 40, corrupt_sum=True)
            with self.assertRaises(builder.BuildError):
                builder.inspect_operator_bundle(path, "a" * 40)

    def test_handoff_compare_detects_identical_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left = root / "left.zip"
            right = root / "right.zip"
            self.make_minimal_handoff(left)
            right.write_bytes(left.read_bytes())
            report = comparer.compare_handoffs(left, right)
            self.assertTrue(report["passed"], report)

    def test_handoff_compare_rejects_tampered_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left = root / "left.zip"
            right = root / "right.zip"
            self.make_minimal_handoff(left)
            self.make_minimal_handoff(right, tamper=True)
            report = comparer.compare_handoffs(left, right)
            self.assertFalse(report["passed"])

    def make_minimal_handoff(self, path: Path, tamper: bool = False) -> None:
        head = "a" * 40
        operator = b"operator-zip"
        payload = {
            "README_FIRST.md": b"readme\n",
            "LOCAL_AGENT_PROMPT.md": b"prompt\n",
            "EVIDENCE_REVIEW_CHECKLIST.md": b"checklist\n",
            "OPERATOR_README_ORIGINAL.md": b"operator readme\n",
            "run-readonly-discovery.ps1": b"runner\n",
            "verify-handoff.ps1": b"verify\n",
            builder.OPERATOR_BUNDLE_NAME: operator,
        }
        payload_hashes = {
            name: comparer.sha256_bytes(data)
            for name, data in payload.items()
        }
        manifest = {
            "report_type": "moomoo_readonly_discovery_handoff_manifest",
            "schema_version": 1,
            "handoff_version": comparer.HANDOFF_VERSION,
            "operator_version": comparer.OPERATOR_VERSION,
            "source_commit": head,
            "expected_checkout_head": head,
            "expected_remote": builder.EXPECTED_REMOTE,
            "operator_bundle": {
                "name": builder.OPERATOR_BUNDLE_NAME,
                "sha256": comparer.sha256_bytes(operator),
                "source_commit": head,
            },
            "payload_files": payload_hashes,
            "authorization": comparer.AUTHORIZATION,
        }
        payload["HANDOFF_MANIFEST.json"] = (
            json.dumps(manifest, sort_keys=True).encode("utf-8")
        )
        if tamper:
            payload["README_FIRST.md"] += b"tamper"
        sums = b"".join(
            f"{comparer.sha256_bytes(data)}  {name}\n".encode("utf-8")
            for name, data in sorted(payload.items())
        )
        payload["HANDOFF_SHA256SUMS.txt"] = sums
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(payload.items()):
                archive.writestr(name, data)


if __name__ == "__main__":
    unittest.main()
