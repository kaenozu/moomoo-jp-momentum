from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


root = Path(__file__).resolve().parents[1]
backup = root / "src" / "database_backup.py"
drill = root / "scripts" / "sqlite_backup_recovery_drill.ps1"

replace_once(
    backup,
    '''        *,
        portfolio_name: str,
        as_of_date: str | None = None,
''',
    '''        *,
        portfolio_name: str | None = None,
        as_of_date: str | None = None,
''',
)
replace_once(
    backup,
    '''        source_path = self.source_path.resolve()
        if destination_path == source_path:
''',
    '''        source_path = self.source_path.resolve()
        if portfolio_name is None:
            from .trading_identity import virtual_portfolio_name

            portfolio_name = virtual_portfolio_name(self.config)
        if destination_path == source_path:
''',
)
replace_once(
    drill,
    '''        --strategy $Portfolio --dry-run 2>&1
''',
    '''        --portfolio $Portfolio --dry-run 2>&1
''',
)

print("portfolio compatibility follow-ups applied")
