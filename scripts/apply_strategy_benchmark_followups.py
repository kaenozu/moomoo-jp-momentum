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

test_path = Path("tests/test_strategy_runner_benchmark.py")
test_text = test_path.read_text(encoding="utf-8")
append = '''\n\ndef test_benchmark_returns_require_full_period_history(tmp_path: Path) -> None:\n    config = _config(tmp_path)\n\n    with sqlite3.connect(config.database_path) as connection:\n        connection.executemany(\n            "INSERT INTO daily_bars (code, date, close) VALUES (?, ?, ?)",\n            [\n                ("JP.1306", "2026-01-01", 100.0),\n                ("JP.1306", "2026-01-02", 102.0),\n                ("JP.1306", "2026-01-05", 104.0),\n                ("JP.1306", "2026-01-06", 106.0),\n                ("JP.1306", "2026-01-07", 108.0),\n                ("JP.1306", "2026-01-08", 110.0),\n            ],\n        )\n\n    returns = StrategyRunner(config)._benchmark_returns("2026-01-08")\n\n    assert returns["return_5d"] == pytest.approx(10.0)\n    assert returns["return_20d"] is None\n    assert returns["return_60d"] is None\n\n\ndef test_momentum_uses_20d_and_60d_benchmark_returns(tmp_path: Path) -> None:\n    from src.strategies.momentum import MomentumStrategy\n\n    config = _config(tmp_path)\n    indicators = _indicator("JP.0001", return_5d=8.0)\n    indicators.return_20d = 15.0\n    indicators.return_60d = 30.0\n\n    result = MomentumStrategy(config).evaluate(\n        indicators,\n        {\n            "return_5d": 2.0,\n            "return_20d": 5.0,\n            "return_60d": 12.0,\n        },\n    )\n\n    assert result.return_5d_vs_benchmark == pytest.approx(6.0)\n    assert result.return_20d_vs_benchmark == pytest.approx(10.0)\n    assert result.return_60d_vs_benchmark == pytest.approx(18.0)\n'''
if "test_benchmark_returns_require_full_period_history" in test_text:
    raise RuntimeError("follow-up tests already exist")
test_path.write_text(test_text.rstrip() + append + "\n", encoding="utf-8")
