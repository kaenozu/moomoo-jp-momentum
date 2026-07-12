from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(
            f"expected one marker in {path}, found {text.count(old)}: {old[:100]!r}"
        )
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "run_daily_cycle.py",
    "DEFAULT_HISTORY_LIMIT = 120\n\n\n",
    '''DEFAULT_HISTORY_LIMIT = 120\n\n\nclass DailyCycleStoppedError(RuntimeError):\n    """Expected operational stop caused by a failed daily-cycle guard."""\n\n\n''',
)

replace_once(
    "run_daily_cycle.py",
    '''    if error_count > 0:\n        raise SystemError(\n            "仮想取引整合性チェックでエラーを検出しました: "\n            f"strategy={strategy_name}, date={target_date}, "\n            f"errors={error_count}, warnings={warning_count}"\n        )\n    if fail_on_warning and warning_count > 0:\n        raise SystemError(\n            "仮想取引整合性チェックの警告を厳格設定によりエラー扱いします: "\n            f"strategy={strategy_name}, date={target_date}, "\n            f"warnings={warning_count}"\n        )\n''',
    '''    if error_count > 0:\n        raise DailyCycleStoppedError(\n            "仮想取引整合性チェックでエラーを検出しました: "\n            f"strategy={strategy_name}, date={target_date}, "\n            f"errors={error_count}, warnings={warning_count}"\n        )\n    if fail_on_warning and warning_count > 0:\n        raise DailyCycleStoppedError(\n            "仮想取引整合性チェックの警告を厳格設定によりエラー扱いします: "\n            f"strategy={strategy_name}, date={target_date}, "\n            f"warnings={warning_count}"\n        )\n''',
)

replace_once(
    "run_daily_cycle.py",
    '''    results: dict[str, int | bool | str] = {\n        "connection_attempted": False,\n        "database_write_attempted": False,\n    }\n    config = load_config(config_path)\n\n    if dry_run:\n        virtual_trade_config = config.get("virtual_trade", {})\n        results["virtual_trade_enabled"] = virtual_trade_config.get(\n            "enabled",\n            True,\n        )\n        integrity_enabled, integrity_fail_on_warning = (\n            _virtual_trade_integrity_settings(config)\n        )\n        results["integrity_check_enabled"] = integrity_enabled\n        results["integrity_fail_on_warning"] = integrity_fail_on_warning\n''',
    '''    results: dict[str, int | bool | str] = {\n        "connection_attempted": False,\n        "database_write_attempted": False,\n        "virtual_trade_enabled": False,\n        "integrity_check_enabled": False,\n        "integrity_fail_on_warning": False,\n        "integrity_errors": 0,\n        "integrity_warnings": 0,\n        "integrity_exit_code": 0,\n    }\n    config = load_config(config_path)\n    virtual_trade_enabled = _read_bool_setting(\n        config,\n        "virtual_trade.enabled",\n        True,\n    )\n    integrity_enabled, integrity_fail_on_warning = (\n        _virtual_trade_integrity_settings(config)\n    )\n    results["virtual_trade_enabled"] = virtual_trade_enabled\n    results["integrity_check_enabled"] = integrity_enabled\n    results["integrity_fail_on_warning"] = integrity_fail_on_warning\n\n    if dry_run:\n''',
)

replace_once(
    "run_daily_cycle.py",
    '''        integrity_enabled, integrity_fail_on_warning = (\n            _virtual_trade_integrity_settings(config)\n        )\n        results["integrity_check_enabled"] = integrity_enabled\n        results["integrity_fail_on_warning"] = integrity_fail_on_warning\n        if integrity_enabled:\n''',
    '''        if integrity_enabled:\n''',
)

replace_once(
    "run_daily_cycle.py",
    '''        else:\n            results["integrity_errors"] = 0\n            results["integrity_warnings"] = 0\n            results["integrity_exit_code"] = 0\n\n        alert_manager = AlertManager(config)\n''',
    '''\n        alert_manager = AlertManager(config)\n''',
)

replace_once(
    "run_daily_cycle.py",
    '''    except SystemError as error:\n        logger.error("日次サイクル停止: %s", error)\n        return 1\n''',
    '''    except (SystemError, DailyCycleStoppedError) as error:\n        logger.error("日次サイクル停止: %s", error)\n        return 1\n''',
)

replace_once(
    "src/virtual_trade_integrity.py",
    '''        report.checked["position_rows"] = len(rows)\n        if as_of_date is not None:\n''',
    '''        report.checked["position_rows"] = len(rows)\n        report.checked["position_comparison_skipped_future_fills"] = 0\n        if as_of_date is not None:\n''',
)

replace_once(
    "tests/test_daily_cycle_integrity.py",
    'with pytest.raises(SystemError, match="errors=1"):',
    'with pytest.raises(run_daily_cycle.DailyCycleStoppedError, match="errors=1"): ',
)
replace_once(
    "tests/test_daily_cycle_integrity.py",
    'with pytest.raises(SystemError, match="警告を厳格設定"):',
    'with pytest.raises(\n        run_daily_cycle.DailyCycleStoppedError,\n        match="警告を厳格設定",\n    ):',
)

replace_once(
    "tests/test_regressions.py",
    '''    assert results == {\n        "connection_attempted": False,\n        "database_write_attempted": False,\n        "virtual_trade_enabled": True,\n        "symbols": 2,\n        "benchmarks": 1,\n    }\n''',
    '''    assert results == {\n        "connection_attempted": False,\n        "database_write_attempted": False,\n        "virtual_trade_enabled": True,\n        "integrity_check_enabled": True,\n        "integrity_fail_on_warning": False,\n        "integrity_errors": 0,\n        "integrity_warnings": 0,\n        "integrity_exit_code": 0,\n        "symbols": 2,\n        "benchmarks": 1,\n    }\n''',
)

# Normalize the accidental trailing space added by the compact replacement above.
path = Path("tests/test_daily_cycle_integrity.py")
path.write_text(
    path.read_text(encoding="utf-8").replace(
        'with pytest.raises(run_daily_cycle.DailyCycleStoppedError, match="errors=1"): \n',
        'with pytest.raises(\n        run_daily_cycle.DailyCycleStoppedError,\n        match="errors=1",\n    ):\n',
    ),
    encoding="utf-8",
)
