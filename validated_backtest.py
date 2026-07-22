"""Safe, evidence-oriented historical backtest entrypoint.

This command never calls OpenD or any order API. It creates a consistent SQLite
copy with the Online Backup API, runs the existing historical engine against the
copy, and writes a machine-readable report without modifying the source DB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from src.backtest_runner import BacktestRunner
from src.benchmarking import load_benchmark_specs, load_run_benchmark_results
from src.config import Config, load_config
from src.scoring import Scorer
from src.signals import SignalResult
from src.strategies import BaseStrategy, StrategyRegistry, StrategyResult


@dataclass(frozen=True)
class CapitalPlan:
    account_initial_cash: float
    active_cash: float
    cash_reserve: float
    max_positions: int
    max_position_amount: float


def build_capital_plan(config: Config) -> CapitalPlan:
    account_cash = float(
        config.get(
            "backtest.initial_cash",
            config.get("virtual_trade.initial_cash", 100000),
        )
    )
    max_positions = int(
        config.get(
            "backtest.max_positions",
            config.get("virtual_trade.max_total_positions", 5),
        )
    )
    max_position_amount = float(
        config.get(
            "backtest.max_position_amount",
            config.get("virtual_trade.max_position_amount", 20000),
        )
    )
    if account_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if max_positions <= 0:
        raise ValueError("max_positions must be positive")
    if max_position_amount <= 0:
        raise ValueError("max_position_amount must be positive")
    active_cash = min(account_cash, max_positions * max_position_amount)
    return CapitalPlan(
        account_initial_cash=account_cash,
        active_cash=active_cash,
        cash_reserve=account_cash - active_cash,
        max_positions=max_positions,
        max_position_amount=max_position_amount,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quick_check(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA quick_check").fetchone()
    return str(row[0]) if row else "missing"


def create_isolated_workspace(
    config_path: str,
    workspace: Path,
) -> tuple[Config, dict[str, Any]]:
    source_config = load_config(config_path)
    source_db = Path(source_config.database_path).expanduser().resolve()
    if not source_db.is_file():
        raise FileNotFoundError(f"source database not found: {source_db}")

    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    work_db = workspace / "backtest.sqlite3"
    work_config_path = workspace / "config.yaml"

    with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as source:
        source_check = _quick_check(source)
        if source_check != "ok":
            raise RuntimeError(
                f"source database quick_check failed: {source_check}"
            )
        with sqlite3.connect(work_db) as target:
            source.backup(target)
            target_check = _quick_check(target)
            if target_check != "ok":
                raise RuntimeError(
                    f"workspace database quick_check failed: {target_check}"
                )

    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    raw.setdefault("database", {})["path"] = str(work_db)
    work_config_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    provenance = {
        "source_database": str(source_db),
        "source_database_sha256": _sha256(source_db),
        "source_quick_check": source_check,
        "workspace_database": str(work_db),
        "workspace_database_sha256_before_run": _sha256(work_db),
        "workspace_quick_check": target_check,
    }
    return load_config(str(work_config_path)), provenance


def _register_scored_strategy(base_name: str, config: Config) -> str:
    if base_name not in StrategyRegistry.list_names():
        raise ValueError(f"unknown strategy: {base_name}")
    base_class = StrategyRegistry._strategies[base_name]
    run_name = f"validated_{base_name}"
    threshold = float(
        config.get(
            "backtest.score_threshold",
            config.get("virtual_trade.score_threshold_for_order", 70),
        )
    )

    class _ScoredStrategy(BaseStrategy):
        def __init__(self, runtime_config: Config):
            super().__init__(runtime_config)
            self.delegate = base_class(runtime_config)
            self.scorer = Scorer(runtime_config)
            self.strategy_name = run_name

        def evaluate(
            self,
            indicators,
            benchmark_returns: dict | None = None,
        ) -> StrategyResult:
            result = self.delegate.evaluate(indicators, benchmark_returns)
            scoring_signal = SignalResult(
                code=result.code,
                name=result.name,
                date=result.date,
                signal_type=result.signal_type,
                strategy_name=result.strategy_name,
                score=float(result.score or 0.0),
                reason=result.reason,
                risk_warnings=[str(item) for item in result.risk_warnings],
                price_at_signal=result.price_at_signal,
            )
            score = float(self.scorer.score(indicators, scoring_signal).total)
            result.score = score
            if result.signal_type == "BUY_CANDIDATE" and score < threshold:
                result.signal_type = "WATCH"
                result.reason = (
                    f"監視候補: score={score:.1f}が注文基準"
                    f"{threshold:.1f}未満; {result.reason}"
                )
            return result

    StrategyRegistry.register(run_name)(_ScoredStrategy)
    return run_name


def _date_bounds(db_path: str) -> tuple[str, str]:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT MIN(date), MAX(date) FROM daily_bars"
        ).fetchone()
    if not row or not row[0] or not row[1]:
        raise ValueError("daily_bars contains no historical data")
    return str(row[0]), str(row[1])


def _annualized_return(
    total_return_pct: float,
    first_date: str,
    last_date: str,
) -> float | None:
    days = (
        datetime.fromisoformat(last_date) - datetime.fromisoformat(first_date)
    ).days
    growth = 1.0 + total_return_pct / 100.0
    if days <= 0 or growth <= 0:
        return None
    return (growth ** (365.0 / days) - 1.0) * 100.0


def _evidence_status(
    *,
    trading_days: int,
    trade_count: int,
    total_return_pct: float,
    excess_vs_1306: float | None,
    profit_factor: float | None,
    max_drawdown_pct: float | None,
    min_trading_days: int,
    min_closed_trades: int,
) -> str:
    if trading_days < min_trading_days or trade_count < min_closed_trades:
        return "INSUFFICIENT_EVIDENCE"
    if (
        total_return_pct <= 0
        or excess_vs_1306 is None
        or excess_vs_1306 <= 0
    ):
        return "NO_EDGE_OBSERVED"
    if profit_factor is None or profit_factor < 1.2:
        return "WEAK_EDGE"
    if max_drawdown_pct is None or max_drawdown_pct > 20:
        return "WEAK_EDGE"
    return "PROMISING_BACKTEST_ONLY"


def _load_report(
    config: Config,
    run_id: int,
    plan: CapitalPlan,
    provenance: dict[str, Any],
    base_strategy: str,
    min_trading_days: int,
    min_closed_trades: int,
) -> dict[str, Any]:
    with sqlite3.connect(config.database_path) as connection:
        connection.row_factory = sqlite3.Row
        run = connection.execute(
            "SELECT * FROM backtest_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        curve = connection.execute(
            "SELECT date, total_equity FROM backtest_equity_curve "
            "WHERE run_id = ? ORDER BY date",
            (run_id,),
        ).fetchall()
        benchmark_rows = load_run_benchmark_results(connection, run_id)
    if run is None or not curve:
        raise RuntimeError("backtest result is incomplete")

    adjusted_curve = [
        (str(row["date"]), float(row["total_equity"]) + plan.cash_reserve)
        for row in curve
    ]
    peak = adjusted_curve[0][1]
    max_drawdown = 0.0
    for _, equity in adjusted_curve:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(
                max_drawdown,
                (peak - equity) / peak * 100.0,
            )

    final_equity = adjusted_curve[-1][1]
    total_return = (
        (final_equity - plan.account_initial_cash)
        / plan.account_initial_cash
        * 100.0
    )
    specs = load_benchmark_specs(config)
    benchmark_by_role = {row["role"]: row for row in benchmark_rows}
    primary_row = benchmark_by_role.get("primary")
    primary_return = primary_row["return_pct"] if primary_row else None
    excess_primary = (
        total_return - float(primary_return)
        if primary_return is not None
        else None
    )
    benchmark_payload: dict[str, Any] = {}
    for spec in specs.all():
        row = benchmark_by_role.get(spec.role)
        value = row["return_pct"] if row else None
        excess = total_return - float(value) if value is not None else None
        benchmark_payload[f"{spec.code}_return_pct"] = (
            float(value) if value is not None else None
        )
        benchmark_payload[f"excess_vs_{spec.code}_pct"] = excess
    trade_count = int(run["trade_count"] or 0)
    raw_profit_factor = run["profit_factor"]
    profit_factor = (
        float(raw_profit_factor)
        if raw_profit_factor is not None
        and math.isfinite(float(raw_profit_factor))
        else None
    )
    status = _evidence_status(
        trading_days=len(adjusted_curve),
        trade_count=trade_count,
        total_return_pct=total_return,
        excess_vs_1306=excess_primary,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown,
        min_trading_days=min_trading_days,
        min_closed_trades=min_closed_trades,
    )

    return {
        "status": status,
        "warning": "Historical simulation only; this is not a profit forecast.",
        "strategy": base_strategy,
        "engine_strategy": str(run["strategy_name"]),
        "period": {
            "requested_start": str(run["start_date"]),
            "requested_end": str(run["end_date"]),
            "first_equity_date": adjusted_curve[0][0],
            "last_equity_date": adjusted_curve[-1][0],
            "trading_days": len(adjusted_curve),
        },
        "capital": {
            "account_initial_cash": plan.account_initial_cash,
            "active_cash": plan.active_cash,
            "cash_reserve": plan.cash_reserve,
            "max_positions": plan.max_positions,
            "max_position_amount": plan.max_position_amount,
            "final_equity": final_equity,
            "historical_profit_yen": (
                final_equity - plan.account_initial_cash
            ),
        },
        "performance": {
            "total_return_pct": total_return,
            "annualized_return_pct": _annualized_return(
                total_return,
                adjusted_curve[0][0],
                adjusted_curve[-1][0],
            ),
            "max_drawdown_pct": max_drawdown,
            "win_rate_pct": (
                float(run["win_rate"])
                if run["win_rate"] is not None
                else None
            ),
            "profit_factor": profit_factor,
            "closed_trade_count": trade_count,
        },
        "benchmark_roles": {
            spec.role: spec.code for spec in specs.all()
        },
        "benchmarks": benchmark_payload,
        "evidence_requirements": {
            "minimum_trading_days": min_trading_days,
            "minimum_closed_trades": min_closed_trades,
        },
        "provenance": provenance,
    }


def _write_json(
    path: Path,
    payload: dict[str, Any],
    force: bool,
) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if force else "x"
    with path.open(mode, encoding="utf-8", newline="\n") as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安全な履歴バックテスト")
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--strategy", default="momentum")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--workspace")
    parser.add_argument("--output-json")
    parser.add_argument("--min-trading-days", type=int, default=120)
    parser.add_argument("--min-closed-trades", type=int, default=30)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--report",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    workspace = Path(args.workspace or f".runtime/backtests/{stamp}")
    try:
        config, provenance = create_isolated_workspace(
            args.config,
            workspace,
        )
        start_default, end_default = _date_bounds(config.database_path)
        start_date = args.from_date or start_default
        end_date = args.to_date or end_default
        if start_date > end_date:
            raise ValueError("--from must be on or before --to")

        plan = build_capital_plan(config)
        run_strategy = _register_scored_strategy(args.strategy, config)
        runner = BacktestRunner(config)
        runner.initial_cash = int(round(plan.active_cash))
        runner.cash = plan.active_cash
        runner.max_total_positions = plan.max_positions
        runner.max_position_amount = int(round(plan.max_position_amount))
        runner.slippage_bps = int(
            round(float(config.get("virtual_trade.slippage_bps", 10)))
        )
        runner.commission = int(
            round(float(config.get("virtual_trade.commission", 0)))
        )
        run_id = runner.run(run_strategy, start_date, end_date)
        provenance["workspace_database_sha256_after_run"] = _sha256(
            Path(config.database_path)
        )
        report = _load_report(
            config,
            run_id,
            plan,
            provenance,
            args.strategy,
            args.min_trading_days,
            args.min_closed_trades,
        )
        output = (
            Path(args.output_json)
            if args.output_json
            else workspace / "report.json"
        )
        _write_json(output, report, args.force)

        perf = report["performance"]
        capital = report["capital"]
        period = report["period"]
        print(f"status: {report['status']}")
        print(
            f"period: {period['first_equity_date']} - "
            f"{period['last_equity_date']}"
        )
        print(f"trading_days: {period['trading_days']}")
        print(f"closed_trades: {perf['closed_trade_count']}")
        print(
            "historical_profit_yen: "
            f"{capital['historical_profit_yen']:.0f}"
        )
        print(f"total_return_pct: {perf['total_return_pct']:.2f}")
        print(f"max_drawdown_pct: {perf['max_drawdown_pct']:.2f}")
        print(f"report: {output.resolve()}")
        return 0
    except (
        FileExistsError,
        FileNotFoundError,
        RuntimeError,
        ValueError,
    ) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
