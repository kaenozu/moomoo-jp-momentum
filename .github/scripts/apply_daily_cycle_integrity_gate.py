from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"replacement marker not found in {path}: {old[:80]!r}")
    if text.count(old) != 1:
        raise RuntimeError(f"replacement marker is not unique in {path}: {old[:80]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "run_daily_cycle.py",
    "from src.virtual_trade import VirtualTradeManager\n",
    "from src.virtual_trade import VirtualTradeManager\n"
    "from src.virtual_trade_integrity import IntegrityReport, VirtualTradeIntegrityChecker\n",
)

replace_once(
    "run_daily_cycle.py",
    "\ndef _load_indicator_inputs(\n",
    '''\ndef _read_bool_setting(config, key: str, default: bool) -> bool:\n    """Read a boolean setting without accepting truthy strings or integers."""\n    value = config.get(key, default)\n    if not isinstance(value, bool):\n        raise ValueError(f"{key}はtrue/falseで指定してください: {value!r}")\n    return value\n\n\ndef _virtual_trade_integrity_settings(config) -> tuple[bool, bool]:\n    """Return whether the daily integrity gate is enabled and strict on warnings."""\n    virtual_trade_enabled = _read_bool_setting(\n        config,\n        "virtual_trade.enabled",\n        True,\n    )\n    if not virtual_trade_enabled:\n        return False, False\n    return (\n        _read_bool_setting(\n            config,\n            "virtual_trade.integrity_check.enabled",\n            True,\n        ),\n        _read_bool_setting(\n            config,\n            "virtual_trade.integrity_check.fail_on_warning",\n            False,\n        ),\n    )\n\n\ndef _log_virtual_trade_integrity_report(report: IntegrityReport) -> None:\n    """Write every actionable finding to the normal operation log."""\n    for finding in report.findings:\n        context = (\n            json.dumps(\n                finding.context,\n                ensure_ascii=False,\n                sort_keys=True,\n                default=str,\n            )\n            if finding.context\n            else "{}"\n        )\n        message = (\n            "仮想取引整合性: severity=%s code=%s message=%s context=%s"\n        )\n        if finding.severity == "error":\n            logger.error(\n                message,\n                finding.severity,\n                finding.code,\n                finding.message,\n                context,\n            )\n        else:\n            logger.warning(\n                message,\n                finding.severity,\n                finding.code,\n                finding.message,\n                context,\n            )\n\n\ndef _run_virtual_trade_integrity_gate(\n    config,\n    strategy_name: str,\n    target_date: str,\n    *,\n    fail_on_warning: bool,\n) -> IntegrityReport:\n    """Run the read-only integrity checker and enforce the configured policy."""\n    report = VirtualTradeIntegrityChecker(config).run(\n        strategy_name,\n        as_of_date=target_date,\n    )\n    _log_virtual_trade_integrity_report(report)\n    error_count = len(report.errors)\n    warning_count = len(report.warnings)\n    if error_count > 0:\n        raise SystemError(\n            "仮想取引整合性チェックでエラーを検出しました: "\n            f"strategy={strategy_name}, date={target_date}, "\n            f"errors={error_count}, warnings={warning_count}"\n        )\n    if fail_on_warning and warning_count > 0:\n        raise SystemError(\n            "仮想取引整合性チェックの警告を厳格設定によりエラー扱いします: "\n            f"strategy={strategy_name}, date={target_date}, "\n            f"warnings={warning_count}"\n        )\n    logger.info(\n        "仮想取引整合性チェック完了: strategy=%s, date=%s, warnings=%d",\n        strategy_name,\n        target_date,\n        warning_count,\n    )\n    return report\n\n\ndef _load_indicator_inputs(\n''',
)

replace_once(
    "run_daily_cycle.py",
    '''        results["virtual_trade_enabled"] = virtual_trade_config.get(\n            "enabled",\n            True,\n        )\n        symbol_count, benchmark_count = _validate_watchlist_for_dry_run(config)\n''',
    '''        results["virtual_trade_enabled"] = virtual_trade_config.get(\n            "enabled",\n            True,\n        )\n        integrity_enabled, integrity_fail_on_warning = (\n            _virtual_trade_integrity_settings(config)\n        )\n        results["integrity_check_enabled"] = integrity_enabled\n        results["integrity_fail_on_warning"] = integrity_fail_on_warning\n        symbol_count, benchmark_count = _validate_watchlist_for_dry_run(config)\n''',
)

replace_once(
    "run_daily_cycle.py",
    '''        manager.save_equity_curve("default", target_date)\n\n        alert_manager = AlertManager(config)\n''',
    '''        manager.save_equity_curve("default", target_date)\n\n        integrity_enabled, integrity_fail_on_warning = (\n            _virtual_trade_integrity_settings(config)\n        )\n        results["integrity_check_enabled"] = integrity_enabled\n        results["integrity_fail_on_warning"] = integrity_fail_on_warning\n        if integrity_enabled:\n            integrity_report = _run_virtual_trade_integrity_gate(\n                config,\n                "default",\n                target_date,\n                fail_on_warning=integrity_fail_on_warning,\n            )\n            results["integrity_errors"] = len(integrity_report.errors)\n            results["integrity_warnings"] = len(integrity_report.warnings)\n            results["integrity_exit_code"] = integrity_report.exit_code\n        else:\n            results["integrity_errors"] = 0\n            results["integrity_warnings"] = 0\n            results["integrity_exit_code"] = 0\n\n        alert_manager = AlertManager(config)\n''',
)

replace_once(
    "src/virtual_trade_integrity.py",
    '''        report.checked["position_rows"] = len(rows)\n        if as_of_date is not None:\n            return\n        if not complete:\n''',
    '''        report.checked["position_rows"] = len(rows)\n        if as_of_date is not None:\n            future_fill = connection.execute(\n                """\n                SELECT 1 FROM virtual_fills\n                WHERE strategy_name = ?\n                  AND COALESCE(substr(filled_at, 1, 10), '') > ?\n                LIMIT 1\n                """,\n                (strategy_name, as_of_date),\n            ).fetchone()\n            report.checked["position_comparison_skipped_future_fills"] = int(\n                future_fill is not None\n            )\n            if future_fill is not None:\n                return\n        if not complete:\n''',
)

replace_once(
    "config.example.yaml",
    '''  tax_enabled: false\n  default_benchmark: JP.1306\npaper_trade:\n''',
    '''  tax_enabled: false\n  default_benchmark: JP.1306\n  integrity_check:\n    enabled: true\n    fail_on_warning: false\npaper_trade:\n''',
)

replace_once(
    "tests/fixtures/config.test.yaml",
    '''  reserve_buffer_pct: 2.0\n  default_benchmark: "JP.2559"\n\npaper_trade:\n''',
    '''  reserve_buffer_pct: 2.0\n  default_benchmark: "JP.2559"\n  integrity_check:\n    enabled: true\n    fail_on_warning: false\n\npaper_trade:\n''',
)

replace_once(
    "pyrightconfig.json",
    '''    "src/virtual_trade_integrity.py",\n    "tests/test_virtual_fill_commission_integrity.py"\n''',
    '''    "src/virtual_trade_integrity.py",\n    "tests/test_virtual_fill_commission_integrity.py",\n    "tests/test_daily_cycle_integrity.py"\n''',
)

new_test = '''"""Regression coverage for the daily-cycle virtual-trade integrity gate."""\n\nfrom __future__ import annotations\n\nfrom collections.abc import Callable\nfrom typing import Any\n\nimport pytest\n\nimport run_daily_cycle\nfrom src.virtual_trade_integrity import IntegrityFinding, IntegrityReport\n\n\nclass _ConfigStub:\n    def __init__(self, values: dict[str, Any]):\n        self.values = values\n\n    def get(self, key: str, default: Any = None) -> Any:\n        return self.values.get(key, default)\n\n\ndef _report(*findings: IntegrityFinding) -> IntegrityReport:\n    report = IntegrityReport(strategy_name="default")\n    report.findings.extend(findings)\n    return report\n\n\ndef _install_checker(\n    monkeypatch: pytest.MonkeyPatch,\n    report: IntegrityReport,\n    calls: list[tuple[str, str | None]] | None = None,\n) -> None:\n    class _Checker:\n        def __init__(self, config: Any):\n            self.config = config\n\n        def run(\n            self,\n            strategy_name: str,\n            as_of_date: str | None = None,\n        ) -> IntegrityReport:\n            if calls is not None:\n                calls.append((strategy_name, as_of_date))\n            return report\n\n    monkeypatch.setattr(run_daily_cycle, "VirtualTradeIntegrityChecker", _Checker)\n\n\ndef test_integrity_settings_disable_gate_with_virtual_trade() -> None:\n    config = _ConfigStub({"virtual_trade.enabled": False})\n\n    assert run_daily_cycle._virtual_trade_integrity_settings(config) == (False, False)\n\n\ndef test_integrity_settings_reject_truthy_strings() -> None:\n    config = _ConfigStub(\n        {\n            "virtual_trade.enabled": True,\n            "virtual_trade.integrity_check.enabled": "false",\n        }\n    )\n\n    with pytest.raises(ValueError, match="true/false"):\n        run_daily_cycle._virtual_trade_integrity_settings(config)\n\n\ndef test_integrity_gate_passes_clean_report_and_uses_target_date(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    calls: list[tuple[str, str | None]] = []\n    report = _report()\n    _install_checker(monkeypatch, report, calls)\n\n    returned = run_daily_cycle._run_virtual_trade_integrity_gate(\n        _ConfigStub({}),\n        "default",\n        "2026-07-14",\n        fail_on_warning=False,\n    )\n\n    assert returned is report\n    assert calls == [("default", "2026-07-14")]\n\n\ndef test_integrity_gate_always_fails_on_errors(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    report = _report(\n        IntegrityFinding("error", "equity.cash_mismatch", "cash mismatch")\n    )\n    _install_checker(monkeypatch, report)\n\n    with pytest.raises(SystemError, match="errors=1"):\n        run_daily_cycle._run_virtual_trade_integrity_gate(\n            _ConfigStub({}),\n            "default",\n            "2026-07-14",\n            fail_on_warning=False,\n        )\n\n\ndef test_integrity_warning_passes_by_default(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    report = _report(\n        IntegrityFinding("warning", "fill.legacy_commission", "legacy")\n    )\n    _install_checker(monkeypatch, report)\n\n    assert (\n        run_daily_cycle._run_virtual_trade_integrity_gate(\n            _ConfigStub({}),\n            "default",\n            "2026-07-14",\n            fail_on_warning=False,\n        )\n        is report\n    )\n\n\ndef test_integrity_warning_can_be_strict(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    report = _report(\n        IntegrityFinding("warning", "fill.legacy_commission", "legacy")\n    )\n    _install_checker(monkeypatch, report)\n\n    with pytest.raises(SystemError, match="警告を厳格設定"):\n        run_daily_cycle._run_virtual_trade_integrity_gate(\n            _ConfigStub({}),\n            "default",\n            "2026-07-14",\n            fail_on_warning=True,\n        )\n\n\ndef test_dry_run_reports_gate_settings_without_opening_database(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    class _UnexpectedChecker:\n        def __init__(self, config: Any):\n            raise AssertionError("dry-run must not instantiate the integrity checker")\n\n    monkeypatch.setattr(\n        run_daily_cycle,\n        "VirtualTradeIntegrityChecker",\n        _UnexpectedChecker,\n    )\n\n    results = run_daily_cycle.run_cycle(\n        "2026-07-01",\n        dry_run=True,\n        config_path="tests/fixtures/config.test.yaml",\n    )\n\n    assert results["integrity_check_enabled"] is True\n    assert results["integrity_fail_on_warning"] is False\n'''
Path("tests/test_daily_cycle_integrity.py").write_text(new_test, encoding="utf-8")

existing_test = Path("tests/test_virtual_fill_commission_integrity.py")
text = existing_test.read_text(encoding="utf-8")
append_test = '''\n\ndef test_as_of_latest_fill_still_checks_current_position_cache(\n    tmp_path: Path,\n) -> None:\n    config = _config(tmp_path, commission=55.0)\n    _fill_buy(config)\n    with sqlite3.connect(config.database_path) as connection:\n        connection.execute(\n            "UPDATE virtual_positions SET quantity = quantity + 1 "\n            "WHERE strategy_name = 'momentum'"\n        )\n\n    report = VirtualTradeIntegrityChecker(config).run(\n        "momentum",\n        as_of_date="2026-07-14",\n    )\n\n    assert any(\n        item.code == "position.quantity_mismatch" for item in report.errors\n    )\n    assert report.checked["position_comparison_skipped_future_fills"] == 0\n'''
if "test_as_of_latest_fill_still_checks_current_position_cache" in text:
    raise RuntimeError("position-cache regression test already exists")
existing_test.write_text(text.rstrip() + append_test + "\n", encoding="utf-8")
