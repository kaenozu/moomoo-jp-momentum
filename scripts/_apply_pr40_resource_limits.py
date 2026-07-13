from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {path}: found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


verifier = ROOT / "scripts" / "verify_moomoo_master_handoff_offline.py"
tests = ROOT / "scripts" / "test_verify_moomoo_master_handoff_offline.py"

replace_once(
    verifier,
    'HEX64 = re.compile(r"^[0-9a-f]{64}$")\n',
    '''HEX64 = re.compile(r"^[0-9a-f]{64}$")
MIB = 1024 * 1024
MAX_INPUT_BYTES = 10 * MIB
MAX_HANDOFF_BYTES = 5 * MIB
MAX_OPERATOR_BYTES = 5 * MIB
MAX_MEMBER_BYTES = 2 * MIB
MAX_TOTAL_UNCOMPRESSED_BYTES = 20 * MIB
MAX_COMPRESSION_RATIO = 100.0
''',
)

replace_once(
    verifier,
    '''def validate_zip_infos(
    infos: list[zipfile.ZipInfo], label: str
) -> dict[str, zipfile.ZipInfo]:
    result: dict[str, zipfile.ZipInfo] = {}
    seen_casefold: dict[str, str] = {}
    for info in infos:
        name = info.filename
        normalized = name.replace("\\\\", "/")
        parts = PurePosixPath(normalized).parts
        unsafe = (
            not name
            or "\\x00" in name
            or info.is_dir()
            or normalized.startswith(("/", "//"))
            or bool(re.match(r"^[A-Za-z]:", normalized))
            or len(parts) != 1
            or parts[0] in {"", ".", ".."}
        )
        if unsafe:
            raise VerificationError(f"{label} contains unsafe entry: {name!r}")
        key = normalized.casefold()
        if key in seen_casefold:
            raise VerificationError(
                f"{label} contains duplicate or case-colliding entries: "
                f"{seen_casefold[key]!r}, {name!r}"
            )
        seen_casefold[key] = name
        result[name] = info
    return result


def open_verified_zip(data: bytes, label: str) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
        infos = validate_zip_infos(archive.infolist(), label)
        bad_member = archive.testzip()
        if bad_member is not None:
            archive.close()
            raise VerificationError(
                f"{label} compressed data is corrupt: {bad_member}"
            )
        return archive, infos
    except (OSError, zipfile.BadZipFile) as exc:
        raise VerificationError(f"{label} is not a readable ZIP: {exc}") from exc
''',
    '''def validate_zip_infos(
    infos: list[zipfile.ZipInfo],
    label: str,
    *,
    max_members: int,
    max_member_bytes: int,
    max_total_uncompressed_bytes: int = MAX_TOTAL_UNCOMPRESSED_BYTES,
    max_compression_ratio: float = MAX_COMPRESSION_RATIO,
) -> dict[str, zipfile.ZipInfo]:
    if len(infos) > max_members:
        raise VerificationError(
            f"{label} contains too many members: {len(infos)} > {max_members}"
        )

    result: dict[str, zipfile.ZipInfo] = {}
    seen_casefold: dict[str, str] = {}
    total_uncompressed = 0
    for info in infos:
        name = info.filename
        normalized = name.replace("\\\\", "/")
        parts = PurePosixPath(normalized).parts
        unsafe = (
            not name
            or "\\x00" in name
            or info.is_dir()
            or normalized.startswith(("/", "//"))
            or bool(re.match(r"^[A-Za-z]:", normalized))
            or len(parts) != 1
            or parts[0] in {"", ".", ".."}
        )
        if unsafe:
            raise VerificationError(f"{label} contains unsafe entry: {name!r}")
        if info.file_size > max_member_bytes:
            raise VerificationError(
                f"{label} member is too large: {name!r} "
                f"{info.file_size} > {max_member_bytes}"
            )
        total_uncompressed += info.file_size
        if total_uncompressed > max_total_uncompressed_bytes:
            raise VerificationError(
                f"{label} uncompressed size exceeds limit: "
                f"{total_uncompressed} > {max_total_uncompressed_bytes}"
            )
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > max_compression_ratio:
            raise VerificationError(
                f"{label} compression ratio exceeds limit for {name!r}: "
                f"{ratio:.1f} > {max_compression_ratio:.1f}"
            )
        key = normalized.casefold()
        if key in seen_casefold:
            raise VerificationError(
                f"{label} contains duplicate or case-colliding entries: "
                f"{seen_casefold[key]!r}, {name!r}"
            )
        seen_casefold[key] = name
        result[name] = info
    return result


def open_verified_zip(
    data: bytes,
    label: str,
    *,
    max_archive_bytes: int,
    max_members: int,
    max_member_bytes: int,
) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    if len(data) > max_archive_bytes:
        raise VerificationError(
            f"{label} exceeds compressed size limit: {len(data)} > {max_archive_bytes}"
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
        infos = validate_zip_infos(
            archive.infolist(),
            label,
            max_members=max_members,
            max_member_bytes=max_member_bytes,
        )
        bad_member = archive.testzip()
        if bad_member is not None:
            archive.close()
            raise VerificationError(
                f"{label} compressed data is corrupt: {bad_member}"
            )
        return archive, infos
    except (OSError, zipfile.BadZipFile) as exc:
        raise VerificationError(f"{label} is not a readable ZIP: {exc}") from exc
''',
)

replace_once(
    verifier,
    'archive, infos = open_verified_zip(data, "nested operator ZIP")',
    '''archive, infos = open_verified_zip(
        data,
        "nested operator ZIP",
        max_archive_bytes=MAX_OPERATOR_BYTES,
        max_members=len(OPERATOR_MEMBERS),
        max_member_bytes=MAX_MEMBER_BYTES,
    )''',
)

replace_once(
    verifier,
    'archive, infos = open_verified_zip(input_data, "input ZIP")',
    '''archive, infos = open_verified_zip(
        input_data,
        "input ZIP",
        max_archive_bytes=MAX_INPUT_BYTES,
        max_members=len(HANDOFF_MEMBERS),
        max_member_bytes=MAX_HANDOFF_BYTES,
    )''',
)

replace_once(
    verifier,
    '''    if not input_path.is_file():
        raise VerificationError(f"input file does not exist: {input_path}")
''',
    '''    if not input_path.is_file():
        raise VerificationError(f"input file does not exist: {input_path}")
    input_size = input_path.stat().st_size
    if input_size > MAX_INPUT_BYTES:
        raise VerificationError(
            f"input file exceeds size limit: {input_size} > {MAX_INPUT_BYTES}"
        )
''',
)

replace_once(
    verifier,
    'archive, infos = open_verified_zip(handoff_data, "handoff ZIP")',
    '''archive, infos = open_verified_zip(
        handoff_data,
        "handoff ZIP",
        max_archive_bytes=MAX_HANDOFF_BYTES,
        max_members=len(HANDOFF_MEMBERS),
        max_member_bytes=MAX_OPERATOR_BYTES,
    )''',
)

replace_once(
    verifier,
    '''            "authorization": AUTHORIZATION,
            "production_execution_performed": False,
''',
    '''            "authorization": AUTHORIZATION,
            "resource_limits": {
                "max_input_bytes": MAX_INPUT_BYTES,
                "max_handoff_bytes": MAX_HANDOFF_BYTES,
                "max_operator_bytes": MAX_OPERATOR_BYTES,
                "max_member_bytes": MAX_MEMBER_BYTES,
                "max_total_uncompressed_bytes": MAX_TOTAL_UNCOMPRESSED_BYTES,
                "max_compression_ratio": MAX_COMPRESSION_RATIO,
            },
            "production_execution_performed": False,
''',
)

replace_once(
    tests,
    'import unittest\n',
    'import unittest\nfrom unittest import mock\n',
)

replace_once(
    tests,
    '''    def test_cli_refuses_to_overwrite_report(self) -> None:
''',
    '''    def test_rejects_oversized_input_before_zip_parsing(self) -> None:
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
''',
)

print("PR #40 verifier resource limits staged")
