from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    if content.count(old) != 1:
        raise RuntimeError(
            f"expected exactly one match in {path}, found {content.count(old)}"
        )
    file_path.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "run_daily_cycle.py",
    '''def _virtual_trade_integrity_settings(config) -> tuple[bool, bool]:
    """Return whether the daily integrity gate is enabled and strict on warnings."""
    virtual_trade_enabled = _read_bool_setting(
        config,
        "virtual_trade.enabled",
        True,
    )
    if not virtual_trade_enabled:
        return False, False
''',
    '''def _virtual_trade_integrity_settings(
    config,
    virtual_trade_enabled: bool,
) -> tuple[bool, bool]:
    """Return whether the daily integrity gate is enabled and strict on warnings."""
    if not virtual_trade_enabled:
        return False, False
''',
)
replace_once(
    "run_daily_cycle.py",
    "        _virtual_trade_integrity_settings(config)\n",
    "        _virtual_trade_integrity_settings(config, virtual_trade_enabled)\n",
)
replace_once(
    "tests/test_daily_cycle_integrity.py",
    "    assert run_daily_cycle._virtual_trade_integrity_settings(config) == (False, False)\n",
    "    assert run_daily_cycle._virtual_trade_integrity_settings(config, False) == (\n        False,\n        False,\n    )\n",
)
replace_once(
    "tests/test_daily_cycle_integrity.py",
    "        run_daily_cycle._virtual_trade_integrity_settings(config)\n",
    "        run_daily_cycle._virtual_trade_integrity_settings(config, True)\n",
)

# Temporary files must not remain in the final diff.
Path("scripts/apply_pr22_review_fix.py").unlink()
Path(".github/workflows/apply-pr22-review-fix.yml").unlink()
