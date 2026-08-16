from __future__ import annotations

import json
import subprocess
import warnings
import zipfile
from pathlib import Path

import pytest

from src.release_candidate import (
    MASTER_RELEASE_CANDIDATE,
    VALIDATION_ONLY,
    ReleaseCandidateError,
    SourceFile,
    SourceMetadata,
)
from src.release_candidate_security import (
    secure_build_release_archive,
    secure_git_worktree_dirty,
    secure_source_path,
    secure_verify_release_archive,
)


def _source_files() -> list[SourceFile]:
    return [
        SourceFile("README.md", b"# release fixture\n"),
        SourceFile("data/symbols.json", b"[]\n"),
        SourceFile("scripts/run.py", b"print('ok')\n", mode="100755"),
        SourceFile("src/app.py", b"VALUE = 1\n"),
    ]


def _metadata(
    *,
    event: str = "pull_request",
    ref: str = "refs/pull/81/merge",
    dirty: bool = False,
) -> SourceMetadata:
    return SourceMetadata(
        repository="kaenozu/moomoo-jp-momentum",
        source_commit="a" * 40,
        source_ref=ref,
        source_event=event,
        default_branch="master",
        source_dirty=dirty,
    )


def _rewrite_zip(
    source: Path,
    destination: Path,
    *,
    replacements: dict[str, bytes] | None = None,
    extra_members: list[tuple[str, bytes]] | None = None,
) -> None:
    replacements = replacements or {}
    extra_members = extra_members or []
    with zipfile.ZipFile(source, "r") as original:
        original_members = [
            (info, original.read(info.filename)) for info in original.infolist()
        ]
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as rewritten:
        for info, data in original_members:
            new_info = zipfile.ZipInfo(info.filename, info.date_time)
            new_info.compress_type = info.compress_type
            new_info.create_system = info.create_system
            new_info.external_attr = info.external_attr
            rewritten.writestr(new_info, replacements.get(info.filename, data))
        for name, data in extra_members:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            rewritten.writestr(info, data)


def test_validation_archive_is_deterministic_and_verifiable(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    first_result = secure_build_release_archive(first, _source_files(), _metadata())
    second_result = secure_build_release_archive(second, _source_files(), _metadata())

    assert first.read_bytes() == second.read_bytes()
    assert first_result.archive_sha256 == second_result.archive_sha256
    assert first_result.distribution_status == VALIDATION_ONLY

    verified = secure_verify_release_archive(
        first,
        expected_repository="kaenozu/moomoo-jp-momentum",
        expected_commit="a" * 40,
        expected_ref="refs/pull/81/merge",
        expected_event="pull_request",
        expected_status=VALIDATION_ONLY,
    )
    assert verified.member_count == 4


def test_master_push_candidate_never_has_production_authority(tmp_path: Path) -> None:
    archive_path = tmp_path / "candidate.zip"
    result = secure_build_release_archive(
        archive_path,
        _source_files(),
        _metadata(event="push", ref="refs/heads/master"),
    )

    assert result.distribution_status == MASTER_RELEASE_CANDIDATE
    with zipfile.ZipFile(archive_path, "r") as archive:
        manifest = json.loads(archive.read("release-manifest.json"))
    assert manifest["release_candidate"] is True
    assert manifest["production_authority"] is False
    assert manifest["real_order_authorized"] is False
    assert manifest["cutover_authorized"] is False


def test_dirty_master_push_is_validation_only(tmp_path: Path) -> None:
    archive_path = tmp_path / "dirty.zip"
    result = secure_build_release_archive(
        archive_path,
        _source_files(),
        _metadata(event="push", ref="refs/heads/master", dirty=True),
    )
    assert result.distribution_status == VALIDATION_ONLY


def test_verifier_rejects_production_authority(tmp_path: Path) -> None:
    original = tmp_path / "original.zip"
    tampered = tmp_path / "tampered.zip"
    secure_build_release_archive(original, _source_files(), _metadata())

    with zipfile.ZipFile(original, "r") as archive:
        manifest = json.loads(archive.read("release-manifest.json"))
    manifest["production_authority"] = True
    _rewrite_zip(
        original,
        tampered,
        replacements={
            "release-manifest.json": (
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
        },
    )

    with pytest.raises(
        ReleaseCandidateError,
        match="production_authority must remain false",
    ):
        secure_verify_release_archive(tampered)


def test_verifier_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "traversal.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", b"unsafe")

    with pytest.raises(ReleaseCandidateError, match="unsafe archive path"):
        secure_verify_release_archive(archive_path)


def test_verifier_rejects_case_colliding_members(tmp_path: Path) -> None:
    archive_path = tmp_path / "collision.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive_path, "w") as archive:
            for name, data in (
                ("source/File.py", b"one"),
                ("source/file.py", b"two"),
            ):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = 0o644 << 16
                archive.writestr(info, data)

    with pytest.raises(ReleaseCandidateError, match="case-colliding archive members"):
        secure_verify_release_archive(archive_path)


def test_verifier_rejects_unexpected_member(tmp_path: Path) -> None:
    original = tmp_path / "original.zip"
    tampered = tmp_path / "unexpected.zip"
    secure_build_release_archive(original, _source_files(), _metadata())
    _rewrite_zip(
        original,
        tampered,
        extra_members=[("unexpected.txt", b"not declared")],
    )

    with pytest.raises(ReleaseCandidateError, match="archive member-set mismatch"):
        secure_verify_release_archive(tampered)


def test_source_policy_excludes_runtime_and_sensitive_files() -> None:
    assert secure_source_path("data/symbols.json")
    assert not secure_source_path("config.yaml")
    assert not secure_source_path("data/moomoo.db")
    assert not secure_source_path("reports/latest.csv")
    assert not secure_source_path("secrets/private.pem")
    assert not secure_source_path(".env.staging")
    assert not secure_source_path("secrets.env")
    assert not secure_source_path("Data/private.json")
    assert not secure_source_path("certs/app.jks")
    assert not secure_source_path("certs/app.cer")
    assert not secure_source_path("keys/app.pkcs12")
    assert not secure_source_path("subdir/.env")
    assert not secure_source_path("nested/env.file.env")


def test_verifier_rejects_duplicate_manifest_keys(tmp_path: Path) -> None:
    original = tmp_path / "original.zip"
    tampered = tmp_path / "duplicate-key.zip"
    secure_build_release_archive(original, _source_files(), _metadata())
    with zipfile.ZipFile(original, "r") as archive:
        raw = archive.read("release-manifest.json")
    duplicate = raw.replace(
        b'"production_authority":false,',
        b'"production_authority":false,"production_authority":true,',
        1,
    )
    _rewrite_zip(
        original,
        tampered,
        replacements={"release-manifest.json": duplicate},
    )
    with pytest.raises(ReleaseCandidateError, match="duplicate JSON key"):
        secure_verify_release_archive(tampered)


def test_verifier_rejects_compressed_member_before_reading(tmp_path: Path) -> None:
    archive_path = tmp_path / "compressed.zip"
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("release-manifest.json", b"{}")
    with pytest.raises(ReleaseCandidateError, match="compressed member is forbidden"):
        secure_verify_release_archive(archive_path)


def test_untracked_file_marks_worktree_dirty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "untracked.txt").write_text("secret", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert secure_git_worktree_dirty() is True
