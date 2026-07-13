from __future__ import annotations

from typing import Any, cast

import pytest

from src.virtual_trade_integrity import IntegrityReport, VirtualTradeIntegrityChecker


class _Config:
    database_path = "unused.db"

    def get(self, key_path: str, default: Any = None) -> Any:
        if key_path == "virtual_trade":
            return {"initial_cash": 150000, "commission": 0}
        return default


class _TrackingConnection:
    def __init__(self) -> None:
        self.close_calls = 0
        self.exit_calls = 0

    def __enter__(self) -> "_TrackingConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        self.exit_calls += 1

    def close(self) -> None:
        self.close_calls += 1


def test_run_explicitly_closes_read_only_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = VirtualTradeIntegrityChecker(cast(Any, _Config()))
    connection = _TrackingConnection()

    monkeypatch.setattr(
        checker,
        "_connect_read_only",
        lambda: cast(Any, connection),
    )
    monkeypatch.setattr(
        checker,
        "_validate_schema",
        lambda _connection, _report: False,
    )

    report = checker.run("momentum")

    assert isinstance(report, IntegrityReport)
    assert not report.errors
    assert connection.close_calls == 1
    assert connection.exit_calls == 0
