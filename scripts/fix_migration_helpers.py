"""Repair helper expressions in the one-time readable migration scripts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "scripts/apply_review_core.py",
    "scripts/apply_review_storage.py",
    "scripts/apply_review_pipeline.py",
)

OLD = 'next_def = re.compile(rf"^{{{indent}}}(?:def|class)\\s+")'
NEW = 'next_def = re.compile(r"^" + (" " * indent) + r"(?:def|class)\\s+")'

for relative_path in SCRIPTS:
    path = ROOT / relative_path
    text = path.read_text(encoding="utf-8")
    if OLD in text:
        path.write_text(text.replace(OLD, NEW), encoding="utf-8")

print("migration helpers repaired")
