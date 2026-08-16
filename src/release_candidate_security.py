from __future__ import annotations

import json
import subprocess
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

from src.release_candidate import (
    FIXED_ZIP_TIME,
    MAX_SOURCE_FILE_SIZE,
    MAX_TOTAL_SOURCE_SIZE,
    ReleaseCandidateError,
    SourceFile,
    SourceMetadata,
    VerificationResult,
    build_release_archive,
    source_files_from_git,
    validate_archive_path,
    verify_release_archive,
)
from src.source_manifest import is_manifest_path

MAX_METADATA_MEMBER_SIZE = 4 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_PATH_LENGTH = 4096
MAX_PATH_COMPONENT_LENGTH = 255
SUPPORTED_DEFAULT_BRANCH = "master"

_MANIFEST_NAME = "release-manifest.json"
_CHECKSUMS_NAME = "SHA256SUMS.txt"
_SOURCE_PREFIX = "source/"
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_MANIFEST_KEYS = {
    "artifact_type",
    "cutover_authorized",
    "default_branch",
    "distribution_status",
    "member_count",
    "members",
    "production_authority",
    "real_order_authorized",
    "release_candidate",
    "repository",
    "schema_version",
    "source_commit",
    "source_dirty",
    "source_event",
    "source_ref",
    "total_source_bytes",
}


def secure_source_path(path: str) -> bool:
    """Return whether a tracked path may enter a source release artifact.

    Path portability is enforced here; the source boundary itself is the
    canonical ``src.source_manifest.is_manifest_path`` rule set.
    """
    validate_archive_path(path)
    if len(path) > MAX_PATH_LENGTH or unicodedata.normalize("NFC", path) != path:
        raise ReleaseCandidateError(f"non-canonical source path: {path!r}")

    parts = path.split("/")
    for part in parts:
        if (
            len(part) > MAX_PATH_COMPONENT_LENGTH
            or part != part.strip()
            or part.endswith((".", " "))
        ):
            raise ReleaseCandidateError(f"non-portable source path: {path!r}")
        windows_stem = part.split(".", maxsplit=1)[0].upper()
        if windows_stem in _WINDOWS_RESERVED_NAMES:
            raise ReleaseCandidateError(f"Windows-reserved source path: {path!r}")

    return is_manifest_path(path)


def secure_source_files_from_git(commit: str) -> list[SourceFile]:
    """Apply the stricter source boundary to files loaded from the Git tree."""
    return [
        source_file
        for source_file in source_files_from_git(commit)
        if secure_source_path(source_file.path)
    ]


def secure_git_worktree_dirty() -> bool:
    """Include tracked and untracked non-ignored files in the dirty-state claim."""
    try:
        completed = subprocess.run(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseCandidateError("unable to determine Git worktree state") from error
    return bool(completed.stdout)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseCandidateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _load_canonical_manifest(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_METADATA_MEMBER_SIZE:
        raise ReleaseCandidateError("release manifest exceeds size limit")
    try:
        raw = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except ReleaseCandidateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseCandidateError(
            "release manifest is not valid canonical UTF-8 JSON"
        ) from error
    if not isinstance(raw, dict):
        raise ReleaseCandidateError("release manifest must be a JSON object")
    if set(raw) != _MANIFEST_KEYS:
        raise ReleaseCandidateError("release manifest key set is not canonical")
    if _canonical_json(raw) != data:
        raise ReleaseCandidateError("release manifest is not canonical JSON")
    return raw


def _validate_zip_entry(info: zipfile.ZipInfo) -> None:
    validate_archive_path(info.filename)
    if info.flag_bits & 0x1:
        raise ReleaseCandidateError(f"encrypted member is forbidden: {info.filename}")
    if info.compress_type != zipfile.ZIP_STORED:
        raise ReleaseCandidateError(f"compressed member is forbidden: {info.filename}")
    if info.compress_size != info.file_size:
        raise ReleaseCandidateError(f"stored member size mismatch: {info.filename}")
    if info.date_time != FIXED_ZIP_TIME:
        raise ReleaseCandidateError(f"non-deterministic timestamp: {info.filename}")
    if info.create_system != 3:
        raise ReleaseCandidateError(f"unexpected ZIP creator: {info.filename}")


def _preflight_archive(archive_path: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise ReleaseCandidateError("archive contains too many members")

            seen: set[str] = set()
            seen_casefold: dict[str, str] = {}
            total_source_size = 0
            for info in infos:
                if info.is_dir():
                    raise ReleaseCandidateError(
                        f"directory entry is forbidden: {info.filename}"
                    )
                if info.filename in seen:
                    raise ReleaseCandidateError(
                        f"duplicate archive member: {info.filename}"
                    )
                folded = info.filename.casefold()
                prior = seen_casefold.get(folded)
                if prior is not None:
                    raise ReleaseCandidateError(
                        "case-colliding archive members: "
                        f"{prior!r} and {info.filename!r}"
                    )
                seen.add(info.filename)
                seen_casefold[folded] = info.filename
                _validate_zip_entry(info)

                if info.filename.startswith(_SOURCE_PREFIX):
                    if info.file_size > MAX_SOURCE_FILE_SIZE:
                        raise ReleaseCandidateError(
                            f"source member exceeds size limit: {info.filename}"
                        )
                    total_source_size += info.file_size
                    if total_source_size > MAX_TOTAL_SOURCE_SIZE:
                        raise ReleaseCandidateError(
                            "archive source size exceeds total limit"
                        )
                elif info.file_size > MAX_METADATA_MEMBER_SIZE:
                    raise ReleaseCandidateError(
                        f"metadata member exceeds size limit: {info.filename}"
                    )

            if _MANIFEST_NAME not in seen or _CHECKSUMS_NAME not in seen:
                raise ReleaseCandidateError("release metadata members are missing")

            manifest = _load_canonical_manifest(archive.read(_MANIFEST_NAME))
            if manifest["default_branch"] != SUPPORTED_DEFAULT_BRANCH:
                raise ReleaseCandidateError(
                    "release candidate default branch must remain master"
                )
            members = manifest.get("members")
            if not isinstance(members, list):
                raise ReleaseCandidateError("manifest members must be an array")
            expected_order = [_MANIFEST_NAME, _CHECKSUMS_NAME]
            for raw_member in members:
                if not isinstance(raw_member, dict):
                    raise ReleaseCandidateError(
                        "manifest member must be a JSON object"
                    )
                path = raw_member.get("path")
                if not isinstance(path, str) or not path.startswith(_SOURCE_PREFIX):
                    raise ReleaseCandidateError(
                        "manifest source member path is invalid"
                    )
                if not secure_source_path(path.removeprefix(_SOURCE_PREFIX)):
                    raise ReleaseCandidateError(
                        f"sensitive source member is forbidden: {path}"
                    )
                expected_order.append(path)
            actual_order = [info.filename for info in infos]
            if set(actual_order) != set(expected_order):
                raise ReleaseCandidateError("archive member-set mismatch")
            if actual_order != expected_order:
                raise ReleaseCandidateError(
                    "archive members are not in canonical order"
                )
    except ReleaseCandidateError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise ReleaseCandidateError(f"invalid release archive: {error}") from error


def secure_verify_release_archive(
    archive_path: Path,
    *,
    expected_repository: str | None = None,
    expected_commit: str | None = None,
    expected_ref: str | None = None,
    expected_event: str | None = None,
    expected_status: str | None = None,
) -> VerificationResult:
    """Perform bounded preflight validation before the full checksum verifier."""
    _preflight_archive(archive_path)
    return verify_release_archive(
        archive_path,
        expected_repository=expected_repository,
        expected_commit=expected_commit,
        expected_ref=expected_ref,
        expected_event=expected_event,
        expected_status=expected_status,
    )


def secure_build_release_archive(
    output_path: Path,
    source_files: list[SourceFile],
    metadata: SourceMetadata,
) -> VerificationResult:
    """Build with a hardened source boundary, then run the secure verifier."""
    filtered = [
        source_file
        for source_file in source_files
        if secure_source_path(source_file.path)
    ]
    result = build_release_archive(output_path, filtered, metadata)
    verified = secure_verify_release_archive(
        output_path,
        expected_repository=metadata.repository,
        expected_commit=metadata.source_commit,
        expected_ref=metadata.source_ref,
        expected_event=metadata.source_event,
        expected_status=result.distribution_status,
    )
    return verified
