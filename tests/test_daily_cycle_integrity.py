"""Regression coverage for the daily-cycle virtual-trade integrity gate."""

from __future__ import annotations

from typing import Any

import pytest

import run_daily_cycle
from src.virtual_trade_integrity import IntegrityFinding, IntegrityReport


class _ConfigStub:
    def __init__(self, values: dict[str, Any]):
        self.values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def _report(*findings: IntegrityFinding) -> IntegrityReport:
    report = IntegrityReport(strategy_name="default")
    report.findings.extend(findings)
    return report


def _install_checker(
    monkeypatch: pytest.MonkeyPatch,
    report: IntegrityReport,
    calls: list[tuple[str, str | None]] | None = None,
) -> None:
    class _Checker:
        def __init__(self, config: Any):
            self.config = config

        def run(
            self,
            strategy_name: str,
            as_of_date: str | None = None,
        ) -> IntegrityReport:
            if calls is not None:
                calls.append((strategy_name, as_of_date))
            return report

    monkeypatch.setattr(run_daily_cycle, "VirtualTradeIntegrityChecker", _Checker)


def test_integrity_settings_disable_gate_with_virtual_trade() -> None:
    config = _ConfigStub({"virtual_trade.enabled": False})

    assert run_daily_cycle._virtual_trade_integrity_settings(config) == (False, False)


def test_integrity_settings_reject_truthy_strings() -> None:
    config = _ConfigStub(
        {
            "virtual_trade.enabled": True,
            "virtual_trade.integrity_check.enabled": "false",
        }
    )

    with pytest.raises(ValueError, match="true/false"):
        run_daily_cycle._virtual_trade_integrity_settings(config)


def test_integrity_gate_passes_clean_report_and_uses_target_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []
    report = _report()
    _install_checker(monkeypatch, report, calls)

    returned = run_daily_cycle._run_virtual_trade_integrity_gate(
        _ConfigStub({}),
        "default",
        "2026-07-14",
        fail_on_warning=False,
    )

    assert returned is report
    assert calls == [("default", "2026-07-14")]


def test_integrity_gate_always_fails_on_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report(
        IntegrityFinding("error", "equity.cash_mismatch", "cash mismatch")
    )
    _install_checker(monkeypatch, report)

    with pytest.raises(
        run_daily_cycle.DailyCycleStoppedError,
        match="errors=1",
    ):
        run_daily_cycle._run_virtual_trade_integrity_gate(
            _ConfigStub({}),
            "default",
            "2026-07-14",
            fail_on_warning=False,
        )


def test_integrity_warning_passes_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report(
        IntegrityFinding("warning", "fill.legacy_commission", "legacy")
    )
    _install_checker(monkeypatch, report)

    assert (
        run_daily_cycle._run_virtual_trade_integrity_gate(
            _ConfigStub({}),
            "default",
            "2026-07-14",
            fail_on_warning=False,
        )
        is report
    )


def test_integrity_warning_can_be_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report(
        IntegrityFinding("warning", "fill.legacy_commission", "legacy")
    )
    _install_checker(monkeypatch, report)

    with pytest.raises(
        run_daily_cycle.DailyCycleStoppedError,
        match="警告を厳格設定",
    ):
        run_daily_cycle._run_virtual_trade_integrity_gate(
            _ConfigStub({}),
            "default",
            "2026-07-14",
            fail_on_warning=True,
        )


def test_dry_run_reports_gate_settings_without_opening_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnexpectedChecker:
        def __init__(self, config: Any):
            raise AssertionError("dry-run must not instantiate the integrity checker")

    monkeypatch.setattr(
        run_daily_cycle,
        "VirtualTradeIntegrityChecker",
        _UnexpectedChecker,
    )

    results = run_daily_cycle.run_cycle(
        "2026-07-01",
        dry_run=True,
        config_path="tests/fixtures/config.test.yaml",
    )

    assert results["integrity_check_enabled"] is True
    assert results["integrity_fail_on_warning"] is False
