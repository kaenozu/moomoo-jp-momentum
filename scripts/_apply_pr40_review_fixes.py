from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


root = Path(__file__).resolve().parents[1]
verifier = root / "scripts" / "verify_moomoo_master_handoff_offline.py"
tests = root / "scripts" / "test_verify_moomoo_master_handoff_offline.py"

replace_once(
    verifier,
    '''def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


''',
    "",
)

replace_once(
    verifier,
    '''    try:
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
    '''    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise VerificationError(f"{label} is not a readable ZIP: {exc}") from exc

    try:
        infos = validate_zip_infos(
            archive.infolist(),
            label,
            max_members=max_members,
            max_member_bytes=max_member_bytes,
        )
        bad_member = archive.testzip()
        if bad_member is not None:
            raise VerificationError(
                f"{label} compressed data is corrupt: {bad_member}"
            )
        return archive, infos
    except Exception:
        archive.close()
        raise
''',
)

replace_once(
    verifier,
    '''        if args.output:
            output = Path(args.output).resolve()
            if output.exists():
                raise VerificationError(f"output already exists: {output}")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8", newline="\\n")
''',
    '''        if args.output:
            output = Path(args.output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                with output.open("x", encoding="utf-8", newline="\\n") as handle:
                    handle.write(rendered)
            except FileExistsError as exc:
                raise VerificationError(f"output already exists: {output}") from exc
''',
)

replace_once(
    tests,
    '''    def test_rejects_resource_member_count_overflow(self) -> None:
''',
    '''    def test_open_verified_zip_closes_archive_on_validation_failure(self) -> None:
        archive = mock.Mock()
        archive.infolist.return_value = []
        with (
            mock.patch.object(verifier.zipfile, "ZipFile", return_value=archive),
            mock.patch.object(
                verifier,
                "validate_zip_infos",
                side_effect=verifier.VerificationError("invalid members"),
            ),
            self.assertRaisesRegex(verifier.VerificationError, "invalid members"),
        ):
            verifier.open_verified_zip(
                b"not-read-by-fake",
                "close test",
                max_archive_bytes=100,
                max_members=1,
                max_member_bytes=10,
            )
        archive.close.assert_called_once_with()

    def test_rejects_resource_member_count_overflow(self) -> None:
''',
)

print("PR #40 review fixes applied")
