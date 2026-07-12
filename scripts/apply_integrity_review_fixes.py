"""Apply review fixes to virtual trade integrity diagnostics."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_integrity() -> None:
    path = ROOT / "src" / "virtual_trade_integrity.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''        "virtual_equity_curve",
    }''',
        '''        "virtual_equity_curve",
        "daily_bars",
    }''',
        "required daily_bars",
    )
    text = replace_once(
        text,
        '''        uri = f"file:{self.db_path.resolve()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)''',
        '''        uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)''',
        "portable read-only URI",
    )
    text = replace_once(
        text,
        '''        if "commission" not in self._table_columns(connection, "virtual_fills"):
            self._add(
                report,
                "error",
                "schema.missing_fill_commission",
                "virtual_fills.commissionがありません。マイグレーションが必要です",
            )
        return True''',
        '''        if "commission" not in self._table_columns(connection, "virtual_fills"):
            self._add(
                report,
                "error",
                "schema.missing_fill_commission",
                "virtual_fills.commissionがありません。マイグレーションが必要です",
            )
            return False
        return True''',
        "missing commission early return",
    )
    text = replace_once(
        text,
        '''        expected_cash, positions, complete = self._replay(report, fills)''',
        '''        equity_report = IntegrityReport(strategy_name=strategy_name)
        expected_cash, positions, complete = self._replay(equity_report, fills)''',
        "isolated equity replay",
    )
    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    path = ROOT / "tests" / "test_virtual_fill_commission_integrity.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''    assert report.exit_code == 1
    assert any(item.code == "fill.legacy_commission" for item in report.warnings)''',
        '''    assert report.exit_code == 1
    assert sum(
        item.code == "fill.legacy_commission" for item in report.warnings
    ) == 1''',
        "single legacy warning assertion",
    )
    text = replace_once(
        text,
        '''            CREATE TABLE virtual_equity_curve (
                id INTEGER PRIMARY KEY,
                strategy_name TEXT,
                date TEXT,
                cash REAL,
                position_value REAL,
                total_equity REAL
            );
            """''',
        '''            CREATE TABLE virtual_equity_curve (
                id INTEGER PRIMARY KEY,
                strategy_name TEXT,
                date TEXT,
                cash REAL,
                position_value REAL,
                total_equity REAL
            );
            CREATE TABLE daily_bars (
                code TEXT,
                date TEXT,
                close REAL
            );
            """''',
        "legacy daily_bars table",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_integrity()
    patch_tests()


if __name__ == "__main__":
    main()
