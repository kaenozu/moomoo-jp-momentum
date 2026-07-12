from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/relative_strength.py",
    """        if len(rows) < 2:\n            return None\n\n        # 最新の終値とdays前の終値を比較\n""",
    """        if len(rows) < days + 1:\n            return None\n\n        # 最新の終値と正確にdays営業日前の終値を比較\n""",
)

replace_once(
    "src/strategies/momentum.py",
    """            result.return_20d_vs_benchmark = self._calc_vs_benchmark(\n                None,  # 20日リターンは別途計算が必要\n                benchmark_returns.get(\"return_20d\"),\n            )\n            result.return_60d_vs_benchmark = self._calc_vs_benchmark(\n                None,\n                benchmark_returns.get(\"return_60d\"),\n            )\n""",
    """            result.return_20d_vs_benchmark = self._calc_vs_benchmark(\n                indicators.return_20d,\n                benchmark_returns.get(\"return_20d\"),\n            )\n            result.return_60d_vs_benchmark = self._calc_vs_benchmark(\n                indicators.return_60d,\n                benchmark_returns.get(\"return_60d\"),\n            )\n""",
)

replace_once(
    "src/strategies/etf_rotation.py",
    """        # 条件4: ベンチマークを上回るリターン（2559比）\n        if result.return_5d_vs_benchmark is not None and result.return_5d_vs_benchmark > 0:\n            buy_reasons.append(f\"2559比+{result.return_5d_vs_benchmark:.1f}%\")\n""",
    """        # 条件4: 設定ベンチマークを上回るリターン\n        if result.return_5d_vs_benchmark is not None and result.return_5d_vs_benchmark > 0:\n            buy_reasons.append(f\"ベンチマーク比+{result.return_5d_vs_benchmark:.1f}%\")\n""",
)

replace_once(
    "src/strategy_runner.py",
    """import logging\nimport sqlite3\nfrom datetime import datetime\n""",
    """import logging\nimport sqlite3\nfrom contextlib import closing\nfrom datetime import datetime\n""",
)

replace_once(
    "src/strategy_runner.py",
    """            for period in (5, 20, 60)\n""",
    """            for period in self.relative_strength.periods\n""",
)

replace_once(
    "src/strategy_runner.py",
    """        with sqlite3.connect(self.db_path) as conn:\n""",
    """        with closing(sqlite3.connect(self.db_path)) as conn, conn:\n""",
)

test_path = Path("tests/test_strategy_runner_benchmark.py")
test_text = test_path.read_text(encoding="utf-8")
append = '''

def test_benchmark_returns_require_full_period_history(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with sqlite3.connect(config.database_path) as connection:
        connection.executemany(
            "INSERT INTO daily_bars (code, date, close) VALUES (?, ?, ?)",
            [
                ("JP.1306", "2026-01-01", 100.0),
                ("JP.1306", "2026-01-02", 102.0),
                ("JP.1306", "2026-01-05", 104.0),
                ("JP.1306", "2026-01-06", 106.0),
                ("JP.1306", "2026-01-07", 108.0),
                ("JP.1306", "2026-01-08", 110.0),
            ],
        )

    returns = StrategyRunner(config)._benchmark_returns("2026-01-08")

    assert returns["return_5d"] == pytest.approx(10.0)
    assert returns["return_20d"] is None
    assert returns["return_60d"] is None


def test_momentum_uses_20d_and_60d_benchmark_returns(tmp_path: Path) -> None:
    from src.strategies.momentum import MomentumStrategy

    config = _config(tmp_path)
    indicators = _indicator("JP.0001", return_5d=8.0)
    indicators.return_20d = 15.0
    indicators.return_60d = 30.0

    result = MomentumStrategy(config).evaluate(
        indicators,
        {
            "return_5d": 2.0,
            "return_20d": 5.0,
            "return_60d": 12.0,
        },
    )

    assert result.return_5d_vs_benchmark == pytest.approx(6.0)
    assert result.return_20d_vs_benchmark == pytest.approx(10.0)
    assert result.return_60d_vs_benchmark == pytest.approx(18.0)
'''
if "test_benchmark_returns_require_full_period_history" in test_text:
    raise RuntimeError("follow-up tests already exist")
test_path.write_text(test_text.rstrip() + append + "\n", encoding="utf-8")
