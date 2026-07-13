from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts" / "build_moomoo_discovery_release.py"
COMPARE_PATH = ROOT / "scripts" / "compare_moomoo_discovery_releases.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = load_module("moomoo_release_builder_test", BUILDER_PATH)
comparer = load_module("moomoo_release_comparer_test", COMPARE_PATH)


def write_fixture_release(path: Path, payload: bytes = b"operator\n") -> None:
    members = {
        comparer.OPERATOR_ZIP: payload,
        "human-validation.schema.json": b"{}\n",
        "human-validation.template.json": b"{}\n",
        "validate_moomoo_human_validation.py": b"print('ok')\n",
        "README_moomoo_human_validation_ja.md": b"# readme\n",
    }
    manifest = {
        "report_type": "moomoo_discovery_release_manifest",
        "release_format_version": 1,
        "operator_version": "1.2.1",
        "source_commit": "a" * 40,
        "source_ref": "refs/heads/master",
        "source_event": "push",
        "release_candidate": True,
        "distribution_status": "MASTER_RELEASE_CANDIDATE",
        "operator_bundle": {
            "filename": comparer.OPERATOR_ZIP,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "authorization": dict(comparer.EXPECTED_AUTHORIZATION),
    }
    members["release-manifest.json"] = (
        json.dumps(manifest, sort_keys=True) + "\n"
    ).encode("utf-8")
    sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(members.items())
    ).encode("utf-8")
    members["SHA256SUMS.txt"] = sums
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 7, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)


def test_release_candidate_requires_master_push() -> None:
    sha = "a" * 40
    assert builder.classify_release_candidate(
        sha, "refs/heads/master", "push", sha
    )
    assert not builder.classify_release_candidate(
        sha, "refs/heads/master", "workflow_dispatch", sha
    )
    assert not builder.classify_release_candidate(
        sha, "refs/pull/32/merge", "pull_request", sha
    )
    assert not builder.classify_release_candidate(
        sha, "refs/heads/master", "push", "b" * 40
    )


def test_identical_release_packages_compare_equal() -> None:
    with tempfile.TemporaryDirectory() as directory:
        left = Path(directory) / "left.zip"
        right = Path(directory) / "right.zip"
        write_fixture_release(left)
        write_fixture_release(right)
        report = comparer.compare_releases(left, right)
    assert report["passed"] is True
    assert report["comparison"]["differing_members"] == []


def test_changed_nested_operator_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        left = Path(directory) / "left.zip"
        right = Path(directory) / "right.zip"
        write_fixture_release(left, b"first\n")
        write_fixture_release(right, b"second\n")
        report = comparer.compare_releases(left, right)
    assert report["passed"] is False
    assert comparer.OPERATOR_ZIP in report["comparison"]["differing_members"]
