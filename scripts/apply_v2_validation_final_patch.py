"""Temporary deterministic patch for final V2 validation hardening."""

from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


module = Path("src/v2_validation.py")
replace_once(
    module,
    '''    destination_path = Path(destination).resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
''',
    '''    destination_path = Path(destination).resolve()
    if source_path == destination_path:
        raise ValueError("source and destination database must be different")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
''',
)
replace_once(
    module,
    '''def _run_snapshot(
    database: str | Path,
    run_id: int,
) -> dict[str, tuple[dict[str, Any], ...]]:
''',
    '''def _ensure_backtest_run_exists(database: str | Path, run_id: int) -> None:
    with sqlite3.connect(database) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='backtest_runs'"
        ).fetchone()
        if table is None:
            raise ValueError(f"backtest_runs table not found: {database}")
        row = conn.execute(
            "SELECT 1 FROM backtest_runs WHERE id=? LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"backtest run not found: database={database}, run_id={run_id}")


def _run_snapshot(
    database: str | Path,
    run_id: int,
) -> dict[str, tuple[dict[str, Any], ...]]:
''',
)
replace_once(
    module,
    '''    expected = set(expected_difference_fields)
    legacy = _run_snapshot(legacy_database, legacy_run_id)
    candidate = _run_snapshot(candidate_database, candidate_run_id)
''',
    '''    expected = set(expected_difference_fields)
    _ensure_backtest_run_exists(legacy_database, legacy_run_id)
    _ensure_backtest_run_exists(candidate_database, candidate_run_id)
    legacy = _run_snapshot(legacy_database, legacy_run_id)
    candidate = _run_snapshot(candidate_database, candidate_run_id)
''',
)

tests = Path("tests/test_v2_validation.py")
replace_once(
    tests,
    '''import yaml

from src.v2_validation import (
''',
    '''import pytest
import yaml

from src.v2_validation import (
''',
)
replace_once(
    tests,
    '''    file_sha256,
    validate_database_migration,
''',
    '''    file_sha256,
    online_backup,
    validate_database_migration,
''',
)
replace_once(
    tests,
    '''        conn.executescript(
            """
            CREATE TABLE backtest_orders (
''',
    '''        conn.executescript(
            """
            CREATE TABLE backtest_runs (
                id INTEGER PRIMARY KEY,
                strategy_name TEXT NOT NULL
            );
            INSERT INTO backtest_runs(id, strategy_name) VALUES (1, 'momentum');
            CREATE TABLE backtest_orders (
''',
)
append_text = '''

def test_backtest_comparison_rejects_unknown_run_id(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy-missing-run.db"
    candidate = tmp_path / "candidate-missing-run.db"
    create_backtest_result_database(legacy)
    create_backtest_result_database(candidate)

    with pytest.raises(ValueError, match="backtest run not found"):
        compare_backtest_runs(legacy, 999, candidate, 1)


def test_online_backup_rejects_source_as_destination(tmp_path: Path) -> None:
    database = tmp_path / "same.db"
    create_backtest_result_database(database)
    before = file_sha256(database)

    with pytest.raises(ValueError, match="must be different"):
        online_backup(database, database)

    assert file_sha256(database) == before
'''
current_tests = tests.read_text(encoding="utf-8")
if "test_backtest_comparison_rejects_unknown_run_id" in current_tests:
    raise RuntimeError("final V2 validation tests are already present")
tests.write_text(current_tests.rstrip() + append_text + "\n", encoding="utf-8")

print("Applied final V2 validation hardening patch.")
