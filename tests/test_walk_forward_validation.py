from __future__ import annotations

import pytest

from src.backtest_evaluation import (
    ParameterSet,
    build_walk_forward_folds,
    cash_matched_benchmark_return,
    realized_trade_pnls,
    summarize_walk_forward,
    trade_distribution,
    training_selection_key,
)
from src.walk_forward_validation import coordinate_search


def _fold_report(
    excess: float,
    *,
    drawdown: float = 10.0,
    closed_trades: int = 12,
    profit_factor: float = 1.5,
) -> dict:
    return {
        "performance": {
            "total_return_pct": excess + 2.0,
            "max_drawdown_pct": drawdown,
            "closed_trade_count": closed_trades,
            "profit_factor": profit_factor,
        },
        "benchmarks": {
            "excess_vs_JP.1306_cash_matched_pct": excess,
        },
    }


def _coordinate_report(parameters: ParameterSet) -> dict:
    excess = (
        parameters.score_threshold / 100.0
        + parameters.max_positions / 10.0
        - parameters.cash_reserve_ratio
        - parameters.stop_loss_pct / 100.0
    )
    return {
        "run_id": 1,
        "status": "PROMISING_BACKTEST_ONLY",
        "period": {
            "requested_start": "D000",
            "requested_end": "D179",
            "first_equity_date": "D000",
            "last_equity_date": "D179",
            "trading_days": 180,
        },
        "parameters": parameters.to_dict(),
        "capital": {
            "account_initial_cash": 150000,
            "active_cash": 150000 * (1 - parameters.cash_reserve_ratio),
            "cash_reserve": 150000 * parameters.cash_reserve_ratio,
            "cash_reserve_ratio": parameters.cash_reserve_ratio,
            "max_positions": parameters.max_positions,
            "max_position_amount": 20000,
            "final_equity": 160000,
            "historical_profit_yen": 10000,
        },
        "performance": {
            "total_return_pct": excess + 2,
            "max_drawdown_pct": 10,
            "profit_factor": 1.5,
            "profit_factor_unbounded": False,
            "closed_trade_count": 20,
            "trade_distribution": {
                "top_5_gross_profit_share_pct": 25.0,
            },
        },
        "benchmarks": {
            "JP.1306_full_investment_return_pct": 5,
            "JP.1306_cash_matched_return_pct": 3,
            "excess_vs_JP.1306_cash_matched_pct": excess,
        },
        "execution": {
            "slippage_bps": 10,
            "commission": 0,
        },
    }


def test_cash_matched_benchmark_uses_only_active_capital() -> None:
    result = cash_matched_benchmark_return(
        49.93,
        active_cash=100000,
        account_initial_cash=150000,
    )

    assert result == pytest.approx(33.2866666667)


def test_walk_forward_folds_use_complete_unseen_test_windows() -> None:
    days = [f"D{index:03d}" for index in range(393)]

    folds = build_walk_forward_folds(
        days,
        train_days=180,
        test_days=60,
        step_days=60,
    )

    assert len(folds) == 3
    assert folds[0].train_start == "D000"
    assert folds[0].train_end == "D179"
    assert folds[0].test_start == "D180"
    assert folds[0].test_end == "D239"
    assert folds[2].train_start == "D120"
    assert folds[2].test_end == "D359"


def test_realized_trade_pnls_match_partial_closes_fifo() -> None:
    rows = [
        {"code": "JP.1", "side": "BUY", "price": 100, "quantity": 10},
        {"code": "JP.1", "side": "SELL", "price": 110, "quantity": 4},
        {"code": "JP.1", "side": "SELL", "price": 90, "quantity": 6},
    ]

    pnls = realized_trade_pnls(rows)

    assert pnls == [40.0, -60.0]
    distribution = trade_distribution(pnls)
    assert distribution["profit_factor"] == pytest.approx(2.0 / 3.0)
    assert distribution["top_5_gross_profit_share_pct"] == 100.0


def test_training_selection_key_prefers_cash_matched_excess() -> None:
    weaker = _fold_report(1.0)
    stronger = _fold_report(2.0)

    assert training_selection_key(
        stronger,
        min_closed_trades=10,
    ) > training_selection_key(
        weaker,
        min_closed_trades=10,
    )


def test_coordinate_search_uses_training_reports_only() -> None:
    initial = ParameterSet(
        score_threshold=70,
        max_positions=5,
        cash_reserve_ratio=0.2,
        stop_loss_pct=5,
    )
    grids = {
        "score_threshold": [60, 80],
        "max_positions": [3, 8],
        "cash_reserve_ratio": [0.0, 0.3],
        "stop_loss_pct": [3, 10],
    }

    selected, trace = coordinate_search(
        initial,
        grids,
        _coordinate_report,
        min_closed_trades=10,
    )

    assert selected == ParameterSet(
        score_threshold=80,
        max_positions=8,
        cash_reserve_ratio=0.0,
        stop_loss_pct=3,
    )
    assert [step["dimension"] for step in trace] == [
        "score_threshold",
        "max_positions",
        "cash_reserve_ratio",
        "stop_loss_pct",
    ]


def test_walk_forward_summary_can_be_promising_only_out_of_sample() -> None:
    base = [_fold_report(1.0), _fold_report(2.0), _fold_report(-0.5)]
    stress = [_fold_report(0.5), _fold_report(0.2), _fold_report(-0.1)]
    realized = [10.0] * 20 + [-5.0] * 10

    summary = summarize_walk_forward(base, stress, realized)

    assert summary["status"] == "PROMISING_WALK_FORWARD_ONLY"
    assert summary["positive_excess_fold_ratio"] == pytest.approx(2 / 3)
    assert (
        summary["aggregate_trade_distribution"][
            "top_5_gross_profit_share_pct"
        ]
        == 25.0
    )


def test_walk_forward_summary_rejects_slippage_fragility() -> None:
    base = [_fold_report(1.0), _fold_report(2.0), _fold_report(0.5)]
    stress = [_fold_report(-0.5), _fold_report(-0.2), _fold_report(0.1)]
    realized = [10.0] * 20 + [-5.0] * 10

    summary = summarize_walk_forward(base, stress, realized)

    assert summary["status"] == "FRAGILE_TO_SLIPPAGE"


def test_walk_forward_summary_rejects_profit_concentration() -> None:
    base = [_fold_report(1.0), _fold_report(2.0), _fold_report(0.5)]
    stress = [_fold_report(0.5), _fold_report(0.2), _fold_report(0.1)]
    realized = [1000.0] + [1.0] * 19 + [-5.0] * 10

    summary = summarize_walk_forward(base, stress, realized)

    assert summary["status"] == "WEAK_EDGE"
