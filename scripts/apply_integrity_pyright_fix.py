"""Fix integrity finding parameter naming for Pyright."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "src" / "virtual_trade_integrity.py"
text = path.read_text(encoding="utf-8")
old = '''        severity: str,
        code: str,
        message: str,
        **context: Any,
    ) -> None:
        report.findings.append(
            IntegrityFinding(severity, code, message, context)
        )'''
new = '''        severity: str,
        finding_code: str,
        message: str,
        **context: Any,
    ) -> None:
        report.findings.append(
            IntegrityFinding(severity, finding_code, message, context)
        )'''
if text.count(old) != 1:
    raise RuntimeError("integrity _add signature did not match exactly once")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
