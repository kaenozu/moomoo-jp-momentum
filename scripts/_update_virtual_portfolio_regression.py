from pathlib import Path

path = Path(__file__).resolve().parents[1] / "tests" / "test_regressions.py"
text = path.read_text(encoding="utf-8")
old = '''        "skip_reason": "",
        "symbols": 2,
'''
new = '''        "skip_reason": "",
        "virtual_portfolio": "default",
        "symbols": 2,
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one result-schema block, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
