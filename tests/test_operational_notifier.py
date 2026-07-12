"""Operational notifier and scheduler notification regressions."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
import requests

import scheduler
from src.operational_notifier import OperationalNotifier


class _ConfigStub:
    def __init__(self, values: dict[str, Any]):
        self.values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def _enabled_config() -> _ConfigStub:
    return _ConfigStub(
        {
            "alerts.operational.enabled": True,
            "alerts.webhook.enabled": True,
            "alerts.webhook.url": "https://example.invalid/hook",
            "alerts.operational.timeout_seconds": 4,
        }
    )


def test_operational_notifier_posts_structured_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

    def post(url: str, *, json: dict[str, Any], timeout: float) -> _Response:
        captured.update(url=url, json=json, timeout=timeout)
        return _Response()

    monkeypatch.setattr("src.operational_notifier.requests.post", post)
    notifier = OperationalNotifier(_enabled_config())

    assert notifier.send_failure(
        "integrity_failure",
        "broken",
        target_date="2026-07-13",
        context={"errors": 2},
    ) is True
    assert captured["url"] == "https://example.invalid/hook"
    assert captured["timeout"] == 4.0
    assert captured["json"]["event_type"] == "integrity_failure"
    assert captured["json"]["context"] == {"errors": 2}


def test_operational_notifier_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.operational_notifier.requests.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not post")),
    )
    notifier = OperationalNotifier(_ConfigStub({}))
    assert notifier.send_failure("x", "y") is False


def test_operational_notifier_transport_error_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("src.operational_notifier.requests.post", fail)
    assert OperationalNotifier(_enabled_config()).send_failure("x", "y") is False


def test_scheduler_timeout_notifies_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def timeout(*_args: Any, **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=7200)

    def notify(
        _config_path: str,
        event_type: str,
        message: str,
        _context: dict[str, object] | None = None,
    ) -> bool:
        calls.append((event_type, message))
        return True

    monkeypatch.setattr(scheduler, "_run_script", timeout)
    monkeypatch.setattr(scheduler, "_notify_scheduler_failure", notify)

    with pytest.raises(RuntimeError, match="タイムアウト"):
        scheduler.job_daily_cycle("config.yaml")
    assert calls and calls[0][0] == "scheduler_timeout"


def test_connection_check_failure_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Connection:
        def __init__(self, _config: Any):
            pass

        def __enter__(self) -> "_Connection":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def connect(self) -> SimpleNamespace:
            return SimpleNamespace(connected=False, message="offline")

    monkeypatch.setattr("src.connection.OpenDConnection", _Connection)
    monkeypatch.setattr(scheduler, "load_config", lambda _path: _ConfigStub({}))
    monkeypatch.setattr(
        scheduler,
        "_notify_scheduler_failure",
        lambda _path, event_type, _message, _context=None: calls.append(event_type) or True,
    )

    with pytest.raises(RuntimeError, match="OpenD接続失敗"):
        scheduler.job_connection_check("config.yaml")
    assert calls == ["opend_connection_check_failure"]
