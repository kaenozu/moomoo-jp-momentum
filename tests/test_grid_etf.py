import sqlite3
import sys

import grid_etf_backtest
from grid_etf_backtest import load_bars
from src.grid_etf_ledger import GridEtfStateStore
from src.grid_etf import GridBar, GridConfig, GridEtfV1, GridOrderSide


def _bars(closes: list[float]) -> list[GridBar]:
    return [
        GridBar(
            date=f"2026-01-{index + 1:02d}",
            open=close,
            high=close + 2.0,
            low=close - 2.0,
            close=close,
        )
        for index, close in enumerate(closes)
    ]


def test_atr_grid_is_not_ready_before_atr_history() -> None:
    strategy = GridEtfV1(GridConfig(atr_period=3, levels=2, initial_cash=100_000))

    for bar in _bars([100, 101, 99]):
        result = strategy.on_bar(bar)

    assert result.orders == []
    assert result.reason == "insufficient_atr_history"


def test_buy_fill_does_not_create_same_bar_take_profit() -> None:
    strategy = GridEtfV1(
        GridConfig(
            atr_period=3,
            atr_multiplier=1.0,
            levels=1,
            initial_cash=100_000,
            level_capital=10_000,
        )
    )
    for bar in _bars([100, 100, 100, 100]):
        strategy.on_bar(bar)

    first = strategy.on_bar(
        GridBar("2026-01-05", open=100, high=101, low=95, close=100)
    )
    assert any(order.side is GridOrderSide.BUY for order in first.fills)
    assert not any(order.side is GridOrderSide.SELL for order in first.fills)

    second = strategy.on_bar(
        GridBar("2026-01-06", open=100, high=105, low=99, close=104)
    )
    assert any(order.side is GridOrderSide.SELL for order in second.fills)

    third = strategy.on_bar(
        GridBar("2026-01-07", open=100, high=101, low=95, close=96)
    )
    assert any(order.side is GridOrderSide.BUY for order in third.fills)


def test_capital_and_drawdown_limits_stop_new_orders() -> None:
    strategy = GridEtfV1(
        GridConfig(
            atr_period=2,
            atr_multiplier=1.0,
            levels=4,
            initial_cash=100_000,
            max_capital_pct=20.0,
            level_capital=10_000,
            max_drawdown_pct=1.0,
        )
    )
    for bar in _bars([100, 100, 100]):
        strategy.on_bar(bar)
    result = strategy.on_bar(GridBar("2026-01-04", 100, 101, 95, 100))
    assert result.reserved_cash <= 20_000

    stopped = strategy.on_bar(GridBar("2026-01-05", 90, 90, 80, 80))
    assert stopped.stopped is True
    assert stopped.orders == []
    assert strategy.pending_orders == []


def test_grid_state_is_independent_between_instances() -> None:
    config = GridConfig(atr_period=2, levels=1, initial_cash=50_000)
    left = GridEtfV1(config)
    right = GridEtfV1(config)

    for bar in _bars([100, 100, 100]):
        left.on_bar(bar)
    assert right.cash == 50_000
    assert right.pending_orders == []


def test_backtest_returns_strategy_scoped_equity_and_fills() -> None:
    strategy = GridEtfV1(GridConfig(atr_period=2, levels=1, initial_cash=50_000))
    result = strategy.backtest(_bars([100, 100, 100, 95, 100, 105]))

    assert result.strategy_name == "grid_etf_v1"
    assert result.initial_cash == 50_000
    assert result.equity_curve
    assert all(fill.side in (GridOrderSide.BUY, GridOrderSide.SELL) for fill in result.fills)


def test_sqlite_loader_is_read_only(tmp_path) -> None:
    db_path = tmp_path / "grid.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE daily_bars (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL)")
        conn.execute("INSERT INTO daily_bars VALUES ('JP.1306', '2026-01-01', 100, 102, 98, 101)")
        conn.commit()

    bars = load_bars(db_path, "JP.1306", "2026-01-01", "2026-01-02")

    assert len(bars) == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == 1


def test_grid_state_can_resume_from_dedicated_sqlite_store(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    store = GridEtfStateStore(db_path)
    config = GridConfig(atr_period=2, levels=1, initial_cash=50_000)
    strategy = GridEtfV1(config)
    for bar in _bars([100, 100, 100, 95]):
        strategy.on_bar(bar)

    store.save("grid_etf_v1", "JP.1306", strategy)
    resumed = store.load("grid_etf_v1", "JP.1306", config)

    assert resumed is not None
    assert resumed.cash == strategy.cash
    assert len(resumed.pending_orders) == len(strategy.pending_orders)
    assert len(resumed._bars) == len(strategy._bars)


def test_grid_state_store_isolated_by_strategy_and_code(tmp_path) -> None:
    store = GridEtfStateStore(tmp_path / "state.db")
    config = GridConfig(atr_period=2, levels=1, initial_cash=50_000)
    strategy = GridEtfV1(config)
    for bar in _bars([100, 100, 100]):
        strategy.on_bar(bar)

    store.save("grid_etf_v1", "JP.1306", strategy)

    assert store.load("grid_etf_v1", "JP.1306", config) is not None
    assert store.load("grid_etf_v1", "JP.2559", config) is None
    assert store.load("momentum", "JP.1306", config) is None


def test_grid_state_store_records_fills_and_rejects_duplicate_dates(tmp_path) -> None:
    store = GridEtfStateStore(tmp_path / "state.db")
    config = GridConfig(atr_period=2, levels=1, initial_cash=50_000)
    bars = _bars([100, 100, 100, 95])
    for bar in bars:
        store.apply_bar("grid_etf_v1", "JP.1306", config, bar)

    with sqlite3.connect(tmp_path / "state.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM grid_etf_equity_curve").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM grid_etf_fills").fetchone()[0] == 1

    import pytest

    with pytest.raises(ValueError, match="重複処理"):
        store.apply_bar("grid_etf_v1", "JP.1306", config, bars[-1])


def test_persist_cli_resumes_without_duplicate_processing(tmp_path, monkeypatch, capsys) -> None:
    db_path = tmp_path / "cli.db"
    bars = _bars([100, 100, 100, 95, 100, 105])
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE daily_bars (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL)")
        conn.executemany(
            "INSERT INTO daily_bars VALUES ('JP.1306', ?, ?, ?, ?, ?)",
            [(bar.date, bar.open, bar.high, bar.low, bar.close) for bar in bars],
        )
        conn.commit()

    argv = [
        "grid_etf_backtest.py",
        "--db",
        str(db_path),
        "--code",
        "JP.1306",
        "--from",
        "2026-01-01",
        "--to",
        "2026-01-06",
        "--persist",
        "--atr-period",
        "2",
        "--levels",
        "1",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert grid_etf_backtest.main() == 0
    first_output = capsys.readouterr().out
    assert "strategy=grid_etf_v1" in first_output

    monkeypatch.setattr(sys, "argv", argv)
    assert grid_etf_backtest.main() == 0
    second_output = capsys.readouterr().out
    assert "fills=2" in second_output
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM grid_etf_equity_curve").fetchone()[0] == 6
