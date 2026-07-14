from pathlib import Path

path = Path(__file__).with_name("_apply_virtual_portfolio_identity.py")
text = path.read_text(encoding="utf-8")
old = '''replace_all("scripts/sqlite_backup_recovery_drill.ps1", '"--strategy", $Portfolio', '"--portfolio", $Portfolio', minimum=3)'''
new = '''replace_all("scripts/sqlite_backup_recovery_drill.ps1", '"--strategy", $Portfolio', '"--portfolio", $Portfolio', minimum=2)'''
if text.count(old) != 1:
    raise SystemExit(f"expected one contract line, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
