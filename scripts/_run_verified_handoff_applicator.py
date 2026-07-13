from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLICATOR = ROOT / "scripts/_apply_verified_handoff_split.py"


def main() -> None:
    text = APPLICATOR.read_text(encoding="utf-8")

    docs_start = text.index("def patch_docs() -> None:")
    docs_end = text.index("\ndef main() -> None:", docs_start)
    docs = '''def patch_docs() -> None:
    for relative in [
        "tools/production_discovery/handoff/README_FIRST.md",
        "tools/production_discovery/handoff/LOCAL_AGENT_PROMPT.md",
    ]:
        path = ROOT / relative
        text = path.read_text(encoding="utf-8-sig").replace("\\r\\n", "\\n")
        marker = "handoff version"
        if marker not in text.lower():
            text += (
                "\\n## Version contract\\n\\n"
                "- operator version: `1.2.2`\\n"
                "- handoff package version: `1.2.2`\\n"
                "- handoff format version: `1`\\n"
                "\\nこれらは別々のversion軸です。機械検証PASSでもpreflightは承認されません。\\n"
            )
        path.write_text(text, encoding="utf-8", newline="\\n")
'''
    text = text[:docs_start] + docs + text[docs_end:]

    validation_start = text.index("    run(\\n        sys.executable,")
    validation_end = text.index(
        '    run(sys.executable, "scripts/test_moomoo_readonly_discovery_handoff_builder.py", "-v")',
        validation_start,
    )
    validation = '''    run(
        sys.executable,
        "-m",
        "py_compile",
        "scripts/build_moomoo_readonly_discovery_handoff.py",
        "scripts/compare_moomoo_readonly_discovery_handoffs.py",
        "scripts/test_moomoo_readonly_discovery_handoff_builder.py",
        "tools/production_discovery/handoff_test_python_shim.py",
    )
'''
    text = text[:validation_start] + validation + text[validation_end:]

    comparer_end = '''    path.write_text(text, encoding="utf-8", newline="\\n")


def safe_extract_function'''
    comparer_replacement = '''    text = replace_once(
        text,
        "except (OSError, zipfile.BadZipFile) as exc:",
        "except (OSError, ValueError, zipfile.BadZipFile) as exc:",
        "comparer unsafe ZIP error handling",
    )
    path.write_text(text, encoding="utf-8", newline="\\n")


def safe_extract_function'''
    if comparer_end not in text:
        raise RuntimeError("Could not locate comparer function end")
    text = text.replace(comparer_end, comparer_replacement, 1)

    cleanup_marker = '(ROOT / ".github/workflows/_apply-verified-handoff-split.yml").unlink()'
    cleanup = '''(ROOT / ".github/workflows/_apply-verified-handoff-split.yml").unlink()
    (ROOT / "scripts/_verified_handoff_trigger.txt").unlink()
    (ROOT / "scripts/_run_verified_handoff_applicator.py").unlink()
    (ROOT / ".github/workflows/tests.yml").write_bytes(
        subprocess.run(
            ["git", "show", "origin/master:.github/workflows/tests.yml"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
    )'''
    if cleanup_marker not in text:
        raise RuntimeError("Could not locate cleanup block")
    text = text.replace(cleanup_marker, cleanup, 1)
    text = text.replace(
        "HEAD:agent/verified-readonly-handoff",
        "HEAD:agent/verified-readonly-handoff-v2",
    )

    APPLICATOR.write_text(text, encoding="utf-8", newline="\n")
    py_compile.compile(str(APPLICATOR), doraise=True)
    subprocess.run([sys.executable, str(APPLICATOR)], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
