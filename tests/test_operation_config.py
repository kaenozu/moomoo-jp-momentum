import subprocess
from pathlib import Path

import yaml

import scheduler


def _load(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def test_operational_benchmarks_are_topix() -> None:
    for path in ("config.example.yaml", "config.jp.example.yaml"):
        config = _load(path)
        assert config["signals"]["relative_strength"]["benchmark_code"] == "JP.1306"
        assert config["benchmark"]["primary"]["code"] == "JP.1306"
        assert config["app"]["default_benchmark"] == "JP.1306"
        assert config["backtest"]["idle_cash_allocation"]["benchmark_code"] == "JP.1306"
        assert config["virtual_trade"]["default_benchmark"] == "JP.1306"


def test_operation_config_has_no_dead_signal_keys() -> None:
    config = _load("config.jp.example.yaml")
    signals = config["signals"]
    assert "volume_ratio_threshold" not in signals
    assert "turnover_threshold_yen" not in signals
    assert signals["volume"]["min_ratio"] == 1.5
    assert config["screening"]["min_turnover"] == 100000000


def test_virtual_cash_supports_all_configured_slots() -> None:
    config = _load("config.jp.example.yaml")["virtual_trade"]
    required = (
        config["max_total_positions"]
        * config["max_position_amount"]
        * (1 + config["reserve_buffer_pct"] / 100)
    )
    assert config["initial_cash"] >= required


def test_scheduler_uses_single_daily_pipeline() -> None:
    config = _load("config.jp.example.yaml")
    assert set(config["scheduler"]["jobs"]) == {"connection_check", "daily_cycle"}
    assert set(scheduler.build_job_functions("config.jp.example.yaml")) == {
        "connection_check",
        "daily_cycle",
    }
    source = Path("scheduler.py").read_text(encoding="utf-8")
    assert "generate_reports.py" not in source
    assert "screen_candidates.py" not in source
    assert "performance_report.py" not in source


def test_daily_cycle_job_invokes_only_run_daily_cycle(monkeypatch) -> None:
    calls: list[tuple[list[str], int, str]] = []

    def fake_run(args: list[str], timeout: int, name: str) -> None:
        calls.append((args, timeout, name))

    monkeypatch.setattr(scheduler, "_run_script", fake_run)
    scheduler.job_daily_cycle("custom.yaml")
    assert calls == [
        (["run_daily_cycle.py", "--config", "custom.yaml"], 7200, "日次運用サイクル")
    ]


def test_run_script_raises_on_failure(monkeypatch) -> None:
    result = subprocess.CompletedProcess(["python"], 2, stdout="out", stderr="err")
    monkeypatch.setattr(scheduler.subprocess, "run", lambda *args, **kwargs: result)
    try:
        scheduler._run_script(["broken.py"], 10, "broken")
    except RuntimeError as error:
        assert "exit=2" in str(error)
    else:
        raise AssertionError("non-zero subprocess must fail the scheduler job")
