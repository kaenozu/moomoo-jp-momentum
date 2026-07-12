"""Normal alert generation must follow the requested processing date."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from src.alerts import AlertManager
from src.data_freshness import FreshnessStatus


class _ConfigStub:
    def __init__(self, database_path: Path):
        self.database_path = str(database_path)
        self.values: dict[str, Any] = {
            "alerts": {
                "enabled": True,
                "console": False,
                "file": False,
                "webhook": {"enabled": False, "url": ""},
                "score_threshold": 90,
                "notify_new_candidates": True,
                "notify_sell_watch": True,
                "notify_stale_data": True,
            },
            "report.output_dir": str(database_path.parent / "reports"),
        }

    def get(self, key: str, default: Any = None) -> Any:
        if key == "alerts":
            return self.values["alerts"]
        return self.values.get(key, default)


def _manager(tmp_path: Path) -> AlertManager:
    database = tmp_path / "alerts.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE symbols (code TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE signals (
                code TEXT, date TEXT, score REAL, reason TEXT, signal_type TEXT
            );
            CREATE TABLE trades_manual (code TEXT, side TEXT, quantity INTEGER);
            CREATE TABLE alert_logs (
                code TEXT, date TEXT, alert_type TEXT, message TEXT,
                sent_to TEXT, created_at TEXT,
                UNIQUE(code, date, alert_type)
            );
            INSERT INTO symbols VALUES ('JP.7203', 'Toyota');
            INSERT INTO signals VALUES (
                'JP.7203', '2026-07-10', 95, 'momentum', 'BUY_CANDIDATE'
            );
            INSERT INTO signals VALUES (
                'JP.7203', '2026-07-13', 10, 'excluded', 'EXCLUDE'
            );
            INSERT INTO trades_manual VALUES ('JP.7203', 'BUY', 1);
            """
        )
    return AlertManager(cast(Any, _ConfigStub(database)))


def test_candidate_alert_uses_target_date(tmp_path: Path) -> None:
    alerts = _manager(tmp_path).check_new_candidates("2026-07-10")
    assert len(alerts) == 1
    assert alerts[0].date == "2026-07-10"
    assert alerts[0].code == "JP.7203"


def test_sell_watch_uses_target_date(tmp_path: Path) -> None:
    alerts = _manager(tmp_path).check_sell_watch("2026-07-13")
    assert len(alerts) == 1
    assert alerts[0].date == "2026-07-13"


def test_freshness_alert_uses_target_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str | None] = []

    class _Guard:
        def __init__(self, _config: Any):
            pass

        def check_freshness(self, reference_date: str | None = None) -> FreshnessStatus:
            captured.append(reference_date)
            return FreshnessStatus(
                is_fresh=False,
                latest_date="2026-07-09",
                days_stale=1,
                message="stale",
                level="warning",
            )

    monkeypatch.setattr("src.data_freshness.DataFreshnessGuard", _Guard)
    alerts = _manager(tmp_path).check_data_freshness("2026-07-10")
    assert captured == ["2026-07-10"]
    assert alerts[0].date == "2026-07-10"


def test_run_all_checks_propagates_one_resolved_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        manager,
        "check_new_candidates",
        lambda target_date=None: calls.append(("candidate", target_date)) or [],
    )
    monkeypatch.setattr(
        manager,
        "check_sell_watch",
        lambda target_date=None: calls.append(("sell", target_date)) or [],
    )
    monkeypatch.setattr(
        manager,
        "check_data_freshness",
        lambda target_date=None: calls.append(("freshness", target_date)) or [],
    )

    assert manager.run_all_checks("2026-07-10") == []
    assert calls == [
        ("candidate", "2026-07-10"),
        ("sell", "2026-07-10"),
        ("freshness", "2026-07-10"),
    ]
