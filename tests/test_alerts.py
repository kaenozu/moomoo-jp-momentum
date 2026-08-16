"""Webhook失敗時の再試行と同一イベント重複防止を検証する。"""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml

from src.alerts import Alert, AlertManager
from src.config import Config
from src.data_store import DataStore


def _manager(tmp_path: Path) -> AlertManager:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "database": {"path": str(tmp_path / "acceptance.db")},
                "alerts": {
                    "enabled": True,
                    "console": False,
                    "file": False,
                    "webhook": {
                        "enabled": True,
                        "url": "http://127.0.0.1:28080/webhook",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    config = Config(str(config_path))
    DataStore(config)
    return AlertManager(config)


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


def test_webhook_retries_once_and_deduplicates_alert_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    responses = iter([_Response(500), _Response(200)])
    calls: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        return next(responses)

    monkeypatch.setattr("src.alerts.requests.post", post)
    alert = Alert(
        code="SYSTEM",
        date="2026-08-06",
        alert_type="opend_connection_failure",
        message="isolated OpenD connection failed",
    )

    assert manager.send_alert(alert) is True
    assert len(calls) == 2
    with sqlite3.connect(manager.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM alert_logs").fetchone()[0] == 1

    assert manager.send_alert(alert) is False
    assert len(calls) == 2
