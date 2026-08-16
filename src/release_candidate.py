from __future__ import annotations

import hashlib
import json
import re
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from src.source_manifest import is_manifest_path

SCHEMA_VERSION = 1
ARTIFACT_TYPE = "MOOMOO_SOURCE_RELEASE_CANDIDATE"
VALIDATION_ONLY = "VALIDATION_ONLY"
MASTER_RELEASE_CANDIDATE = "MASTER_RELEASE_CANDIDATE"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MAX_SOURCE_FILE_SIZE = 10 * 1024 * 1024
MAX_TOTAL_SOURCE_SIZE = 100 * 1024 * 1024

_MANIFEST_NAME = "release-manifest.json"
_CHECKSUMS_NAME = "SHA256SUMS.txt"
_SOURCE_PREFIX = "source/"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

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
_MEMBER_KEYS = {"mode", "path", "sha256", "size"}


class ReleaseCandidateError(ValueError):
    """Raised when a release candidate violates the fail-closed contract."""


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str
    data: bytes
    mode: str = "100644"


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    repository: str
    source_commit: str
    source_ref: str
    source_event: str
    default_branch: str = "master"
    source_dirty: bool = False


@dataclass(frozen=True, slots=True)
class VerificationResult:
    archive_sha256: str
    distribution_status: str
    member_count: int
    repository: str
    source_commit: str
    source_event: str
    source_ref: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "archive_sha256": self.archive_sha256,
            "distribution_status": self.distribution_status,
            "member_count": self.member_count,
            "repository": self.repository,
            "source_commit": self.source_commit,
            "source_event": self.source_event,
            "source_ref": self.source_ref,
        }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_archive_path(path: str) -> None:
    if not path or path.startswith("/") or path.endswith("/"):
        raise ReleaseCandidateError(f"invalid archive path: {path!r}")
    if any(character in path for character in ("\\", "\x00", "\n", "\r", "\t", ":")):
        raise ReleaseCandidateError(f"unsafe or non-portable archive path: {path!r}")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ReleaseCandidateError(f"unsafe archive path components: {path!r}")
    if PurePosixPath(path).as_posix() != path:
        raise ReleaseCandidateError(f"non-canonical archive path: {path!r}")


def is_releasable_source_path(path: str) -> bool:
    validate_archive_path(path)
    return is_manifest_path(path)


def classify_distribution(metadata: SourceMetadata) -> str:
    candidate = (
        metadata.source_event == "push"
        and metadata.source_ref == f"refs/heads/{metadata.default_branch}"
        and not metadata.source_dirty
    )
    return MASTER_RELEASE_CANDIDATE if candidate else VALIDATION_ONLY


def _validate_source_files(source_files: Sequence[SourceFile]) -> list[SourceFile]:
    if not source_files:
        raise ReleaseCandidateError("source release must contain at least one file")
    ordered = sorted(source_files, key=lambda item: item.path)
    seen: set[str] = set()
    seen_casefold: dict[str, str] = {}
    total_size = 0
    for source_file in ordered:
        if not is_releasable_source_path(source_file.path):
            raise ReleaseCandidateError(
                f"excluded source path cannot enter release: {source_file.path}"
            )
        if source_file.mode not in {"100644", "100755"}:
            raise ReleaseCandidateError(
                f"unsupported git mode for {source_file.path}: {source_file.mode}"
            )
        if source_file.path in seen:
            raise ReleaseCandidateError(f"duplicate source path: {source_file.path}")
        folded = source_file.path.casefold()
        prior = seen_casefold.get(folded)
        if prior is not None:
            raise ReleaseCandidateError(
                f"case-colliding source paths: {prior!r} and {source_file.path!r}"
            )
        if len(source_file.data) > MAX_SOURCE_FILE_SIZE:
            raise ReleaseCandidateError(
                f"source file exceeds size limit: {source_file.path}"
            )
        seen.add(source_file.path)
        seen_casefold[folded] = source_file.path
        total_size += len(source_file.data)
    if total_size > MAX_TOTAL_SOURCE_SIZE:
        raise ReleaseCandidateError("source release exceeds total size limit")
    return ordered


def _zip_info(name: str, mode: int = 0o644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = mode << 16
    info.flag_bits |= 0x800
    return info


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    text = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{text}\n".encode("utf-8")


def _create_manifest(
    source_files: Sequence[SourceFile], metadata: SourceMetadata
) -> tuple[dict[str, Any], dict[str, bytes]]:
    members: list[dict[str, str | int]] = []
    payloads: dict[str, bytes] = {}
    total_size = 0
    for source_file in source_files:
        archive_path = f"{_SOURCE_PREFIX}{source_file.path}"
        payloads[archive_path] = source_file.data
        total_size += len(source_file.data)
        members.append(
            {
                "mode": source_file.mode,
                "path": archive_path,
                "sha256": sha256_bytes(source_file.data),
                "size": len(source_file.data),
            }
        )
    status = classify_distribution(metadata)
    manifest: dict[str, Any] = {
        "artifact_type": ARTIFACT_TYPE,
        "cutover_authorized": False,
        "default_branch": metadata.default_branch,
        "distribution_status": status,
        "member_count": len(members),
        "members": members,
        "production_authority": False,
        "real_order_authorized": False,
        "release_candidate": status == MASTER_RELEASE_CANDIDATE,
        "repository": metadata.repository,
        "schema_version": SCHEMA_VERSION,
        "source_commit": metadata.source_commit,
        "source_dirty": metadata.source_dirty,
        "source_event": metadata.source_event,
        "source_ref": metadata.source_ref,
        "total_source_bytes": total_size,
    }
    return manifest, payloads


def build_release_archive(
    output_path: Path,
    source_files: Sequence[SourceFile],
    metadata: SourceMetadata,
) -> VerificationResult:
    if not _GIT_COMMIT_RE.fullmatch(metadata.source_commit):
        raise ReleaseCandidateError("source_commit must be a lowercase Git object ID")
    ordered = _validate_source_files(source_files)
    manifest, payloads = _create_manifest(ordered, metadata)
    manifest_bytes = _canonical_json(manifest)
    checksummed = {_MANIFEST_NAME: manifest_bytes, **payloads}
    checksums_bytes = "".join(
        f"{sha256_bytes(data)}  {name}\n"
        for name, data in sorted(checksummed.items())
    ).encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", allowZip64=True) as archive:
        archive.writestr(_zip_info(_MANIFEST_NAME), manifest_bytes)
        archive.writestr(_zip_info(_CHECKSUMS_NAME), checksums_bytes)
        modes = {
            f"{_SOURCE_PREFIX}{item.path}": 0o755 if item.mode == "100755" else 0o644
            for item in ordered
        }
        for name, data in sorted(payloads.items()):
            archive.writestr(_zip_info(name, modes[name]), data)
    return verify_release_archive(output_path)


def _strict_object(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseCandidateError(f"{label} must be a JSON object")
    actual = set(value)
    if actual != keys:
        raise ReleaseCandidateError(
            f"{label} key mismatch: missing={sorted(keys - actual)}, "
            f"extra={sorted(actual - keys)}"
        )
    return value


def _require_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseCandidateError(f"{label} must be a non-empty string")
    return value


def _require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReleaseCandidateError(f"{label} must be a non-negative integer")
    return value


def _require_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ReleaseCandidateError(f"{label} must be a boolean")
    return value


def _validate_member_names(infos: Sequence[zipfile.ZipInfo]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    seen_casefold: dict[str, str] = {}
    for info in infos:
        if info.is_dir():
            raise ReleaseCandidateError(f"directory entries are forbidden: {info.filename}")
        validate_archive_path(info.filename)
        if info.filename in seen:
            raise ReleaseCandidateError(f"duplicate archive member: {info.filename}")
        folded = info.filename.casefold()
        prior = seen_casefold.get(folded)
        if prior is not None:
            raise ReleaseCandidateError(
                f"case-colliding archive members: {prior!r} and {info.filename!r}"
            )
        seen.add(info.filename)
        seen_casefold[folded] = info.filename
        names.append(info.filename)
    return names


def _parse_checksums(data: bytes) -> dict[str, str]:
    try:
        lines = data.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as error:
        raise ReleaseCandidateError("SHA256SUMS.txt must be UTF-8") from error
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        if len(line) < 67 or line[64:66] != "  ":
            raise ReleaseCandidateError(f"malformed checksum line {line_number}")
        digest, name = line[:64], line[66:]
        if not _SHA256_RE.fullmatch(digest):
            raise ReleaseCandidateError(f"invalid checksum on line {line_number}")
        validate_archive_path(name)
        if name in checksums:
            raise ReleaseCandidateError(f"duplicate checksum entry: {name}")
        checksums[name] = digest
    return checksums


def verify_release_archive(
    archive_path: Path,
    *,
    expected_repository: str | None = None,
    expected_commit: str | None = None,
    expected_ref: str | None = None,
    expected_event: str | None = None,
    expected_status: str | None = None,
) -> VerificationResult:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            corrupt = archive.testzip()
            if corrupt is not None:
                raise ReleaseCandidateError(f"corrupt archive member: {corrupt}")
            names = _validate_member_names(archive.infolist())
            if _MANIFEST_NAME not in names or _CHECKSUMS_NAME not in names:
                raise ReleaseCandidateError("release metadata members are missing")
            try:
                raw_manifest = json.loads(archive.read(_MANIFEST_NAME))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ReleaseCandidateError("release manifest is not valid UTF-8 JSON") from error
            manifest = _strict_object(raw_manifest, _MANIFEST_KEYS, "release manifest")

            if manifest["schema_version"] != SCHEMA_VERSION:
                raise ReleaseCandidateError("unsupported release manifest schema")
            if manifest["artifact_type"] != ARTIFACT_TYPE:
                raise ReleaseCandidateError("unexpected release artifact type")

            repository = _require_str(manifest["repository"], "repository")
            source_commit = _require_str(manifest["source_commit"], "source_commit")
            source_ref = _require_str(manifest["source_ref"], "source_ref")
            source_event = _require_str(manifest["source_event"], "source_event")
            default_branch = _require_str(manifest["default_branch"], "default_branch")
            source_dirty = _require_bool(manifest["source_dirty"], "source_dirty")
            release_candidate = _require_bool(
                manifest["release_candidate"], "release_candidate"
            )
            if not _GIT_COMMIT_RE.fullmatch(source_commit):
                raise ReleaseCandidateError("source_commit must be a lowercase Git object ID")
            for key in (
                "production_authority",
                "real_order_authorized",
                "cutover_authorized",
            ):
                if _require_bool(manifest[key], key):
                    raise ReleaseCandidateError(f"{key} must remain false")

            status = _require_str(manifest["distribution_status"], "distribution_status")
            classified = classify_distribution(
                SourceMetadata(
                    repository=repository,
                    source_commit=source_commit,
                    source_ref=source_ref,
                    source_event=source_event,
                    default_branch=default_branch,
                    source_dirty=source_dirty,
                )
            )
            if status != classified:
                raise ReleaseCandidateError(
                    "distribution status does not match source metadata"
                )
            if release_candidate != (status == MASTER_RELEASE_CANDIDATE):
                raise ReleaseCandidateError(
                    "release_candidate flag does not match distribution status"
                )

            raw_members = manifest["members"]
            if not isinstance(raw_members, list):
                raise ReleaseCandidateError("members must be an array")
            member_count = _require_int(manifest["member_count"], "member_count")
            total_declared = _require_int(
                manifest["total_source_bytes"], "total_source_bytes"
            )
            if member_count != len(raw_members):
                raise ReleaseCandidateError("member_count does not match members")

            expected_names = {_MANIFEST_NAME, _CHECKSUMS_NAME}
            expected_hashes: dict[str, str] = {}
            total_actual = 0
            previous_path = ""
            for index, raw_member in enumerate(raw_members):
                member = _strict_object(raw_member, _MEMBER_KEYS, f"members[{index}]")
                path = _require_str(member["path"], f"members[{index}].path")
                if not path.startswith(_SOURCE_PREFIX):
                    raise ReleaseCandidateError("source member lacks source/ prefix")
                source_path = path.removeprefix(_SOURCE_PREFIX)
                if not is_releasable_source_path(source_path):
                    raise ReleaseCandidateError(
                        f"manifest contains excluded source path: {source_path}"
                    )
                if path <= previous_path:
                    raise ReleaseCandidateError("manifest members must be strictly sorted")
                previous_path = path
                mode = _require_str(member["mode"], f"members[{index}].mode")
                if mode not in {"100644", "100755"}:
                    raise ReleaseCandidateError(f"unsupported member mode: {mode}")
                digest = _require_str(member["sha256"], f"members[{index}].sha256")
                if not _SHA256_RE.fullmatch(digest):
                    raise ReleaseCandidateError(f"invalid member SHA-256: {path}")
                size = _require_int(member["size"], f"members[{index}].size")
                if path in expected_names:
                    raise ReleaseCandidateError(f"duplicate declared member: {path}")
                expected_names.add(path)
                payload = archive.read(path)
                if len(payload) != size:
                    raise ReleaseCandidateError(f"member size mismatch: {path}")
                if sha256_bytes(payload) != digest:
                    raise ReleaseCandidateError(f"member hash mismatch: {path}")
                expected_hashes[path] = digest
                total_actual += size

            if total_actual != total_declared:
                raise ReleaseCandidateError("total_source_bytes does not match members")
            actual_names = set(names)
            if actual_names != expected_names:
                raise ReleaseCandidateError(
                    "archive member-set mismatch: "
                    f"missing={sorted(expected_names - actual_names)}, "
                    f"extra={sorted(actual_names - expected_names)}"
                )
            manifest_bytes = archive.read(_MANIFEST_NAME)
            expected_hashes[_MANIFEST_NAME] = sha256_bytes(manifest_bytes)
            if _parse_checksums(archive.read(_CHECKSUMS_NAME)) != expected_hashes:
                raise ReleaseCandidateError("SHA256SUMS.txt coverage or digest mismatch")
    except ReleaseCandidateError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise ReleaseCandidateError(f"invalid release archive: {error}") from error

    expected_values = {
        "repository": (repository, expected_repository),
        "source_commit": (source_commit, expected_commit),
        "source_ref": (source_ref, expected_ref),
        "source_event": (source_event, expected_event),
        "distribution_status": (status, expected_status),
    }
    for label, (actual, expected) in expected_values.items():
        if expected is not None and actual != expected:
            raise ReleaseCandidateError(
                f"{label} mismatch: expected {expected!r}, got {actual!r}"
            )
    return VerificationResult(
        archive_sha256=sha256_file(archive_path),
        distribution_status=status,
        member_count=member_count,
        repository=repository,
        source_commit=source_commit,
        source_event=source_event,
        source_ref=source_ref,
    )


def _run_git(arguments: Iterable[str]) -> bytes:
    command = ["git", *arguments]
    try:
        completed = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        stderr = ""
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            stderr = error.stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {stderr}" if stderr else ""
        raise ReleaseCandidateError(
            f"git command failed: {' '.join(command)}{suffix}"
        ) from error
    return completed.stdout


def git_head_commit() -> str:
    return _run_git(["rev-parse", "HEAD"]).decode("ascii").strip()


def normalize_git_commit(commit: str) -> str:
    return _run_git(["rev-parse", f"{commit}^{{commit}}"]).decode("ascii").strip()


def git_worktree_dirty() -> bool:
    return bool(_run_git(["status", "--porcelain=v1", "--untracked-files=no"]))


def source_files_from_git(commit: str) -> list[SourceFile]:
    records = _run_git(["ls-tree", "-r", "-z", "--full-tree", commit]).split(b"\x00")
    source_files: list[SourceFile] = []
    for record in records:
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", maxsplit=1)
            raw_mode, object_type, _object_sha = metadata.split(b" ", maxsplit=2)
            mode = raw_mode.decode("ascii")
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise ReleaseCandidateError("unable to parse git tree entry") from error
        if object_type != b"blob":
            raise ReleaseCandidateError(f"unsupported git object type for {path}")
        if not is_releasable_source_path(path):
            continue
        if mode not in {"100644", "100755"}:
            raise ReleaseCandidateError(f"unsupported git mode for {path}: {mode}")
        data = _run_git(["cat-file", "blob", f"{commit}:{path}"])
        source_files.append(SourceFile(path=path, data=data, mode=mode))
    return _validate_source_files(source_files)
