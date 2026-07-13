from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = (
    ROOT / "scripts" / "build_moomoo_discovery_release.py"
)
COMPARE_PATH = (
    ROOT / "scripts" / "compare_moomoo_discovery_releases.py"
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = load_module("moomoo_release_builder_test", BUILDER_PATH)
comparer = load_module("moomoo_release_comparer_test", COMPARE_PATH)


def deterministic_zip(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(
                name, date_time=(2026, 7, 13, 0, 0, 0)
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return output.getvalue()


def make_operator_bundle(
    commit: str = "a" * 40,
    source_ref: str = "refs/heads/master",
) -> bytes:
    sources = {
        name: f"fixture:{name}\n".encode("utf-8")
        for name in comparer.EXPECTED_OPERATOR_SOURCE_MEMBERS
    }
    sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(sources.items())
    ).encode("utf-8")
    members = {**sources, "SHA256SUMS.txt": sums}
    manifest = {
        "report_type": (
            "moomoo_discovery_operator_bundle_manifest"
        ),
        "operator_version": "1.2.1",
        "source_commit": commit,
        "source_ref": source_ref,
        "source_bytes": "git_blob",
        "authorization": dict(comparer.EXPECTED_AUTHORIZATION),
        "files": {
            name: hashlib.sha256(data).hexdigest()
            for name, data in sorted(members.items())
        },
    }
    members["bundle-manifest.json"] = (
        json.dumps(manifest, sort_keys=True) + "\n"
    ).encode("utf-8")
    return deterministic_zip(members)


def write_fixture_release(
    path: Path,
    operator_payload: bytes | None = None,
    extra_member: tuple[str, bytes] | None = None,
) -> None:
    payload = operator_payload or make_operator_bundle()
    members = {
        comparer.OPERATOR_ZIP: payload,
        "human-validation.schema.json": b"{}\n",
        "human-validation.template.json": b"{}\n",
        "validate_moomoo_human_validation.py": b"print('ok')\n",
        "README_moomoo_human_validation_ja.md": b"# readme\n",
        comparer.RELEASE_VERIFIER: b"print('verify')\n",
    }
    manifest = {
        "report_type": "moomoo_discovery_release_manifest",
        "release_format_version": 1,
        "operator_version": "1.2.1",
        "source_commit": "a" * 40,
        "source_ref": "refs/heads/master",
        "source_event": "push",
        "source_bytes": "git_blob",
        "release_candidate": True,
        "distribution_status": "MASTER_RELEASE_CANDIDATE",
        "operator_bundle": {
            "filename": comparer.OPERATOR_ZIP,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "manifest_source_commit": "a" * 40,
            "manifest_source_ref": "refs/heads/master",
        },
        "human_validation": {
            "schema": "human-validation.schema.json",
            "template": "human-validation.template.json",
            "validator": "validate_moomoo_human_validation.py",
            "readme": "README_moomoo_human_validation_ja.md",
            "release_verifier": comparer.RELEASE_VERIFIER,
            "outputs": [
                "06-human-validation.json",
                "07-preflight-eligibility.json",
            ],
        },
        "authorization": dict(comparer.EXPECTED_AUTHORIZATION),
        "separate_approval_required": True,
    }
    members["release-manifest.json"] = (
        json.dumps(manifest, sort_keys=True) + "\n"
    ).encode("utf-8")
    sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(members.items())
    ).encode("utf-8")
    members["SHA256SUMS.txt"] = sums
    if extra_member:
        members[extra_member[0]] = extra_member[1]
    path.write_bytes(deterministic_zip(members))


def test_sha256sums_utf8_bom_is_supported() -> None:
    digest = "a" * 64
    parsed = comparer.parse_sums(
        b"\xef\xbb\xbf" + f"{digest}  payload.txt\n".encode("utf-8")
    )
    assert parsed == {"payload.txt": digest}


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


def test_single_release_verification_passes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        release = Path(directory) / "release.zip"
        write_fixture_release(release)
        report = comparer.verify_release(release)
    assert report["passed"] is True


def test_changed_nested_operator_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        left = Path(directory) / "left.zip"
        right = Path(directory) / "right.zip"
        write_fixture_release(left)
        changed = make_operator_bundle(commit="b" * 40)
        write_fixture_release(right, operator_payload=changed)
        report = comparer.compare_releases(left, right)
    assert report["passed"] is False
    assert comparer.OPERATOR_ZIP in (
        report["comparison"]["differing_members"]
    )


def test_unexpected_release_member_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        release = Path(directory) / "release.zip"
        write_fixture_release(
            release,
            extra_member=("unexpected.txt", b"unexpected\n"),
        )
        report = comparer.verify_release(release)
    assert report["passed"] is False
    assert any(
        "release member set mismatch" in error
        for error in report["release"]["errors"]
    )


def test_nested_operator_internal_tamper_is_rejected() -> None:
    valid = make_operator_bundle()
    with zipfile.ZipFile(io.BytesIO(valid), "r") as archive:
        members = {
            name: archive.read(name)
            for name in archive.namelist()
        }
    members["moomoo_operator_cli.py"] = b"tampered\n"
    tampered = deterministic_zip(members)
    with tempfile.TemporaryDirectory() as directory:
        release = Path(directory) / "release.zip"
        write_fixture_release(
            release, operator_payload=tampered
        )
        report = comparer.verify_release(release)
    assert report["passed"] is False
    assert any(
        "nested operator bundle validation failed" in error
        for error in report["release"]["errors"]
    )
