"""Safe rolling-origin walk-forward validation.

All runs use an SQLite Online Backup copy created by ``validated_backtest``.
Parameter selection reads only each fold's training interval. The selected
parameters are then evaluated on the following unseen interval at normal and
stress slippage.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml

from src.backtest_evaluation import (
    ParameterSet,
    WalkForwardFold,
    cash_matched_benchmark_return,
    realized_trade_pnls,
    summarize_walk_forward,
    trade_distribution,
    training_selection_key,
)
from src.backtest_runner import BacktestRunner
from src.benchmarking import load_benchmark_specs
from src.config import Config, load_config
from validated_backtest import (
    CapitalPlan,
    _load_report,
    _register_scored_strategy,
    _sha256,
    build_capital_plan,
)


def trading_days(
    db_path: str,
    start_date: str | None,
    end_date: str | None,
) -> list[str]:
    """Read the sorted market dates used for fold construction."""

    clauses: list[str] = []
    parameters: list[str] = []
    if start_date is not None:
        clauses.append("date >= ?")
        parameters.append(start_date)
    if end_date is not None:
        clauses.append("date <= ?")
        parameters.append(end_date)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            f"SELECT DISTINCT date FROM daily_bars {where} ORDER BY date",
            parameters,
        ).fetchall()
    return [str(row[0]) for row in rows]


def capital_plan(
    account_cash: float,
    parameters: ParameterSet,
) -> CapitalPlan:
    """Convert reserve ratio and position count into a full account plan."""

    if account_cash <= 0:
        raise ValueError("account cash must be positive")
    if parameters.max_positions <= 0:
        raise ValueError("max positions must be positive")
    if not 0 <= parameters.cash_reserve_ratio < 1:
        raise ValueError("cash reserve ratio must be within [0, 1)")
    reserve = account_cash * parameters.cash_reserve_ratio
    active = account_cash - reserve
    return CapitalPlan(
        account_initial_cash=account_cash,
        active_cash=active,
        cash_reserve=reserve,
        max_positions=parameters.max_positions,
        max_position_amount=active / parameters.max_positions,
    )


def _candidate_config(
    base_config: Config,
    parameters: ParameterSet,
    *,
    slippage_bps: float,
    commission: float,
    path: Path,
) -> Config:
    raw = yaml.safe_load(
        Path(base_config.config_path).read_text(encoding="utf-8")
    ) or {}
    backtest = raw.setdefault("backtest", {})
    backtest["score_threshold"] = parameters.score_threshold
    backtest["max_positions"] = parameters.max_positions
    backtest["stop_loss_pct"] = parameters.stop_loss_pct
    virtual_trade = raw.setdefault("virtual_trade", {})
    virtual_trade["slippage_bps"] = slippage_bps
    virtual_trade["commission"] = commission
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return load_config(str(path))


def realized_pnls(config: Config, run_id: int) -> list[float]:
    """Load one backtest run's closed trades from the isolated database."""

    with sqlite3.connect(config.database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT code, side, price, quantity, filled_at, id
            FROM backtest_fills
            WHERE run_id = ?
            ORDER BY filled_at, id
            """,
            (run_id,),
        ).fetchall()
    return realized_trade_pnls([dict(row) for row in rows])


def _cash_matched_status(report: dict[str, Any]) -> str:
    performance = report["performance"]
    primary_code = report["benchmark_roles"]["primary"]
    benchmark = report["benchmarks"].get(
        f"excess_vs_{primary_code}_cash_matched_pct"
    )
    trades = int(performance["closed_trade_count"])
    total_return = float(performance["total_return_pct"])
    drawdown = float(performance["max_drawdown_pct"])
    distribution = performance["trade_distribution"]
    pf = distribution["profit_factor"]
    unbounded = bool(distribution["profit_factor_unbounded"])
    if trades <= 0:
        return "INSUFFICIENT_EVIDENCE"
    if benchmark is None or float(benchmark) <= 0 or total_return <= 0:
        return "NO_EDGE_OBSERVED"
    if (pf is None and not unbounded) or (
        pf is not None and float(pf) < 1.2
    ):
        return "WEAK_EDGE"
    if drawdown > 20:
        return "WEAK_EDGE"
    return "PROMISING_BACKTEST_ONLY"


def augment_report(
    report: dict[str, Any],
    *,
    config: Config,
    run_id: int,
    plan: CapitalPlan,
) -> dict[str, Any]:
    """Add fair cash-matched benchmarks and trade concentration evidence."""

    performance = report["performance"]
    benchmarks = report["benchmarks"]
    pnls = realized_pnls(config, run_id)
    distribution = trade_distribution(pnls)
    performance["trade_distribution"] = distribution
    performance["closed_trade_count"] = distribution["closed_trade_count"]
    performance["profit_factor"] = distribution["profit_factor"]
    performance["profit_factor_unbounded"] = distribution[
        "profit_factor_unbounded"
    ]
    performance["win_rate_pct"] = (
        distribution["winning_trade_count"]
        / distribution["closed_trade_count"]
        * 100.0
        if distribution["closed_trade_count"]
        else None
    )

    specs = load_benchmark_specs(config)
    strategy_return = float(performance["total_return_pct"])
    for spec in specs.all():
        full = benchmarks.get(f"{spec.code}_return_pct")
        matched = cash_matched_benchmark_return(
            float(full) if full is not None else None,
            plan.active_cash,
            plan.account_initial_cash,
        )
        benchmarks[f"{spec.code}_full_investment_return_pct"] = full
        benchmarks[f"{spec.code}_cash_matched_return_pct"] = matched
        benchmarks[f"excess_vs_{spec.code}_cash_matched_pct"] = (
            strategy_return - matched if matched is not None else None
        )
    report["full_investment_status"] = report["status"]
    report["status"] = _cash_matched_status(report)
    report["benchmark_policy"] = (
        f"Status uses primary benchmark {specs.primary.code} invested only "
        "with strategy active cash; the same reserve remains zero-return cash."
    )
    report["capital"]["cash_reserve_ratio"] = (
        plan.cash_reserve / plan.account_initial_cash
    )
    return report


def run_case(
    base_config: Config,
    *,
    config_dir: Path,
    config_name: str,
    base_strategy: str,
    start_date: str,
    end_date: str,
    account_cash: float,
    parameters: ParameterSet,
    slippage_bps: float,
    commission: float,
) -> tuple[int, dict[str, Any]]:
    """Run one training, test, or stress case on the isolated DB."""

    plan = capital_plan(account_cash, parameters)
    config = _candidate_config(
        base_config,
        parameters,
        slippage_bps=slippage_bps,
        commission=commission,
        path=config_dir / f"{config_name}.yaml",
    )
    strategy_name = _register_scored_strategy(base_strategy, config)
    runner = BacktestRunner(config)
    runner.initial_cash = int(round(plan.active_cash))
    runner.max_total_positions = plan.max_positions
    runner.max_position_amount = int(round(plan.max_position_amount))
    runner.stop_loss_pct = parameters.stop_loss_pct
    runner.slippage_bps = int(round(slippage_bps))
    runner.commission = int(round(commission))
    run_id = runner.run(strategy_name, start_date, end_date)
    report = _load_report(
        config,
        run_id,
        plan,
        {},
        base_strategy,
        1,
        0,
    )
    report["run_id"] = run_id
    report["parameters"] = parameters.to_dict()
    report["execution"] = {
        "slippage_bps": slippage_bps,
        "commission": commission,
    }
    return run_id, augment_report(
        report,
        config=config,
        run_id=run_id,
        plan=plan,
    )


def compact_report(report: dict[str, Any]) -> dict[str, Any]:
    """Keep fold evidence readable while retaining load-bearing metrics."""

    return {
        "run_id": report["run_id"],
        "status": report["status"],
        "period": report["period"],
        "parameters": report["parameters"],
        "capital": report["capital"],
        "performance": {
            "total_return_pct": report["performance"]["total_return_pct"],
            "max_drawdown_pct": report["performance"][
                "max_drawdown_pct"
            ],
            "closed_trade_count": report["performance"][
                "closed_trade_count"
            ],
            "profit_factor": report["performance"]["profit_factor"],
            "profit_factor_unbounded": report["performance"][
                "profit_factor_unbounded"
            ],
            "top_5_gross_profit_share_pct": report["performance"][
                "trade_distribution"
            ]["top_5_gross_profit_share_pct"],
        },
        "benchmarks": report["benchmarks"],
        "execution": report["execution"],
    }


def parameter_candidates(
    current: ParameterSet,
    dimension: str,
    values: Sequence[float | int],
) -> list[ParameterSet]:
    result: list[ParameterSet] = []
    for value in values:
        if dimension == "score_threshold":
            item = replace(current, score_threshold=float(value))
        elif dimension == "max_positions":
            item = replace(current, max_positions=int(value))
        elif dimension == "cash_reserve_ratio":
            item = replace(current, cash_reserve_ratio=float(value))
        elif dimension == "stop_loss_pct":
            item = replace(current, stop_loss_pct=float(value))
        else:
            raise ValueError(f"unknown dimension: {dimension}")
        if item not in result:
            result.append(item)
    if current not in result:
        result.append(current)
    return result


def coordinate_search(
    initial: ParameterSet,
    grids: dict[str, Sequence[float | int]],
    evaluator: Callable[[ParameterSet], dict[str, Any]],
    *,
    min_closed_trades: int,
) -> tuple[ParameterSet, list[dict[str, Any]]]:
    """Deterministic coordinate search using training reports only."""

    current = initial
    trace: list[dict[str, Any]] = []
    for dimension in (
        "score_threshold",
        "max_positions",
        "cash_reserve_ratio",
        "stop_loss_pct",
    ):
        evaluated = [
            (candidate, evaluator(candidate))
            for candidate in parameter_candidates(
                current,
                dimension,
                grids[dimension],
            )
        ]
        selected, _ = max(
            evaluated,
            key=lambda item: training_selection_key(
                item[1],
                min_closed_trades=min_closed_trades,
            ),
        )
        trace.append(
            {
                "dimension": dimension,
                "selected_parameters": selected.to_dict(),
                "candidates": [
                    {
                        "parameters": candidate.to_dict(),
                        "selection_key": list(
                            training_selection_key(
                                report,
                                min_closed_trades=min_closed_trades,
                            )
                        ),
                        "report": compact_report(report),
                    }
                    for candidate, report in evaluated
                ],
            }
        )
        current = selected
    return current, trace


def initial_parameters(config: Config, account_cash: float) -> ParameterSet:
    """Resolve the first fold's starting point from current configuration."""

    plan = build_capital_plan(config)
    active = min(
        account_cash,
        plan.max_positions * plan.max_position_amount,
    )
    return ParameterSet(
        score_threshold=float(
            config.get(
                "backtest.score_threshold",
                config.get("virtual_trade.score_threshold_for_order", 70),
            )
        ),
        max_positions=plan.max_positions,
        cash_reserve_ratio=(account_cash - active) / account_cash,
        stop_loss_pct=float(config.get("backtest.stop_loss_pct", 5.0)),
    )


def validate_grid(
    score_thresholds: Sequence[float],
    max_positions: Sequence[int],
    reserve_ratios: Sequence[float],
    stop_losses: Sequence[float],
) -> None:
    if any(value < 0 or value > 100 for value in score_thresholds):
        raise ValueError("score thresholds must be within [0, 100]")
    if any(value <= 0 for value in max_positions):
        raise ValueError("max positions must be positive")
    if any(value < 0 or value >= 1 for value in reserve_ratios):
        raise ValueError("reserve ratios must be within [0, 1)")
    if any(value <= 0 or value >= 100 for value in stop_losses):
        raise ValueError("stop losses must be within (0, 100)")


def run_walk_forward(
    config: Config,
    *,
    workspace: Path,
    provenance: dict[str, Any],
    base_strategy: str,
    account_cash: float,
    folds: Sequence[WalkForwardFold],
    grids: dict[str, Sequence[float | int]],
    slippage_bps: float,
    stress_slippage_bps: float,
    commission: float,
    minimum_train_closed_trades: int,
    minimum_folds: int,
    minimum_total_closed_trades: int,
    minimum_positive_fold_ratio: float,
    minimum_profit_factor: float,
    maximum_drawdown_pct: float,
    maximum_top_5_profit_share_pct: float,
) -> dict[str, Any]:
    """Select in-sample, evaluate out-of-sample, then aggregate."""

    if stress_slippage_bps < slippage_bps:
        raise ValueError("stress slippage must not be lower than base slippage")

    config_dir = workspace / "candidate-configs"
    initial = initial_parameters(config, account_cash)
    fold_payloads: list[dict[str, Any]] = []
    test_reports: list[dict[str, Any]] = []
    stress_reports: list[dict[str, Any]] = []
    all_test_pnls: list[float] = []

    for fold in folds:
        cache: dict[ParameterSet, dict[str, Any]] = {}
        counter = 0

        def train(parameters: ParameterSet) -> dict[str, Any]:
            nonlocal counter
            if parameters in cache:
                return cache[parameters]
            counter += 1
            _, report = run_case(
                config,
                config_dir=config_dir,
                config_name=f"f{fold.index}_train_{counter}",
                base_strategy=base_strategy,
                start_date=fold.train_start,
                end_date=fold.train_end,
                account_cash=account_cash,
                parameters=parameters,
                slippage_bps=slippage_bps,
                commission=commission,
            )
            cache[parameters] = report
            return report

        selected, trace = coordinate_search(
            initial,
            grids,
            train,
            min_closed_trades=minimum_train_closed_trades,
        )
        test_run_id, test_report = run_case(
            config,
            config_dir=config_dir,
            config_name=f"f{fold.index}_test",
            base_strategy=base_strategy,
            start_date=fold.test_start,
            end_date=fold.test_end,
            account_cash=account_cash,
            parameters=selected,
            slippage_bps=slippage_bps,
            commission=commission,
        )
        _, stress_report = run_case(
            config,
            config_dir=config_dir,
            config_name=f"f{fold.index}_stress",
            base_strategy=base_strategy,
            start_date=fold.test_start,
            end_date=fold.test_end,
            account_cash=account_cash,
            parameters=selected,
            slippage_bps=stress_slippage_bps,
            commission=commission,
        )
        all_test_pnls.extend(realized_pnls(config, test_run_id))
        test_reports.append(test_report)
        stress_reports.append(stress_report)
        fold_payloads.append(
            {
                "fold": fold.to_dict(),
                "selected_parameters": selected.to_dict(),
                "training_selection": trace,
                "out_of_sample": compact_report(test_report),
                "stress_out_of_sample": compact_report(stress_report),
            }
        )
        initial = selected

    aggregate = summarize_walk_forward(
        test_reports,
        stress_reports,
        all_test_pnls,
        minimum_folds=minimum_folds,
        minimum_closed_trades=minimum_total_closed_trades,
        minimum_positive_fold_ratio=minimum_positive_fold_ratio,
        minimum_profit_factor=minimum_profit_factor,
        maximum_drawdown_pct=maximum_drawdown_pct,
        maximum_top_5_profit_share_pct=maximum_top_5_profit_share_pct,
    )
    provenance["workspace_database_sha256_after_run"] = _sha256(
        Path(config.database_path)
    )
    frequency = Counter(
        json.dumps(item["selected_parameters"], sort_keys=True)
        for item in fold_payloads
    )
    return {
        "status": aggregate["status"],
        "warning": (
            "Historical walk-forward simulation only; not a profit forecast "
            "or authorization for real trading."
        ),
        "method": {
            "name": "rolling_origin_coordinate_search",
            "training_only_selection": True,
            "coordinate_order": [
                "score_threshold",
                "max_positions",
                "cash_reserve_ratio",
                "stop_loss_pct",
            ],
            "not_exhaustive": True,
        },
        "strategy": base_strategy,
        "account_initial_cash": account_cash,
        "search_grid": {key: list(value) for key, value in grids.items()},
        "execution": {
            "slippage_bps": slippage_bps,
            "stress_slippage_bps": stress_slippage_bps,
            "commission": commission,
            "cash_return_pct": 0.0,
        },
        "folds": fold_payloads,
        "selected_parameter_frequency": [
            {
                "parameters": json.loads(key),
                "fold_count": count,
            }
            for key, count in frequency.most_common()
        ],
        "aggregate": aggregate,
        "provenance": provenance,
    }
