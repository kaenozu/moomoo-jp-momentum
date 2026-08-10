import sqlite3

from grid_etf_backtest import load_bars
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
