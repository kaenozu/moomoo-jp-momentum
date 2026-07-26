from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config import Config
from src.paper_trade_readiness import (
    build_mp20_paper_config,
    evaluate_paper_trade_readiness,
    execute_if_ready,
    write_mp20_paper_config,
)


def _config(tmp_path: Path, overrides: dict[str, Any] | None = None) -> Config:
    db_path = tmp_path / "moomoo.db"
    db_path.touch()
    watchlist_path = tmp_path / "symbols.json"
    watchlist_path.write_text("[]", encoding="utf-8")
    payload: dict[str, Any] = {
        "database": {"path": str(db_path)},
        "watchlist": {"symbols_file": str(watchlist_path)},
        "backtest": {"max_positions": 20, "stop_loss_pct": 5.0},
        "virtual_trade": {
            "enabled": True,
            "initial_cash": 100000,
            "max_position_amount": 20000,
            "max_total_positions": 20,
            "max_position_per_symbol": 1,
            "market_fill_mode": "next_day_open",
        },
        "paper_trade": {
            "enabled": False,
            "jp_api_simulate_supported": False,
            "allow_market_order": False,
        },
    }
    for key, value in (overrides or {}).items():
        payload[key] = value
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return Config(str(config_path))


def test_build_mp20_profile_preserves_unrelated_settings() -> None:
    source = {
        "alerts": {"enabled": True},
        "scheduler": {"enabled": True, "timezone": "Asia/Tokyo"},
        "backtest": {"max_positions": 5, "stop_loss_pct": 8.0},
        "virtual_trade": {"enabled": False, "max_total_positions": 5},
        "paper_trade": {"enabled": True, "allow_market_order": True},
    }

    profile = build_mp20_paper_config(source)

    assert profile["alerts"] == {"enabled": True}
    assert profile["scheduler"] == {
        "enabled": False,
        "timezone": "Asia/Tokyo",
    }
    assert profile["backtest"]["max_positions"] == 20
    assert profile["backtest"]["stop_loss_pct"] == 5.0
    assert profile["virtual_trade"]["enabled"] is True
    assert profile["virtual_trade"]["max_total_positions"] == 20
    assert profile["paper_trade"]["enabled"] is False
    assert profile["paper_trade"]["allow_market_order"] is False
    assert source["backtest"]["max_positions"] == 5


def test_write_mp20_profile_refuses_to_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    source.write_text("alerts:\n  enabled: true\n", encoding="utf-8")
    output = tmp_path / "paper.yaml"

    write_mp20_paper_config(source, output)

    with pytest.raises(FileExistsError):
        write_mp20_paper_config(source, output)


def test_readiness_passes_for_safe_mp20_profile(tmp_path: Path) -> None:
    report = evaluate_paper_trade_readiness(_config(tmp_path))

    assert report.ready is True
    assert all(check.status == "pass" for check in report.checks)


@pytest.mark.parametrize(
    "paper_trade",
    [
        {
            "enabled": True,
            "jp_api_simulate_supported": False,
            "allow_market_order": False,
        },
        {
            "enabled": False,
            "jp_api_simulate_supported": False,
            "allow_market_order": True,
        },
    ],
)
def test_readiness_blocks_api_order_paths(
    tmp_path: Path,
    paper_trade: dict[str, Any],
) -> None:
    report = evaluate_paper_trade_readiness(
        _config(tmp_path, {"paper_trade": paper_trade})
    )

    assert report.ready is False
    assert any(check.status == "error" for check in report.checks)


def test_readiness_requires_validated_mp20_settings(tmp_path: Path) -> None:
    report = evaluate_paper_trade_readiness(
        _config(
            tmp_path,
            {
                "backtest": {"max_positions": 5, "stop_loss_pct": 8.0},
                "virtual_trade": {
                    "enabled": True,
                    "initial_cash": 100000,
                    "max_position_amount": 20000,
                    "max_total_positions": 5,
                    "max_position_per_symbol": 1,
                    "market_fill_mode": "next_day_open",
                },
            },
        )
    )

    assert report.ready is False
    names = {check.name for check in report.checks if check.status == "error"}
    assert {"backtest_mp20", "virtual_trade_mp20", "validated_stop_loss"} <= names


def test_execute_if_ready_does_not_call_runner_on_failure(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        {
            "paper_trade": {
                "enabled": True,
                "jp_api_simulate_supported": False,
                "allow_market_order": False,
            }
        },
    )
    called = False

    def runner() -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    with pytest.raises(RuntimeError, match="readiness failed"):
        execute_if_ready(config, runner)

    assert called is False


def test_execute_if_ready_calls_runner_after_gate(tmp_path: Path) -> None:
    result = execute_if_ready(_config(tmp_path), lambda: {"virtual_orders": 3})

    assert result == {"virtual_orders": 3}
