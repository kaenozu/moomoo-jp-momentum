"""Run four counterfactual portfolio analyses without mutating source data.

Examples:
    python scripts/counterfactual_analysis.py \
        --from 2026-01-01 --to 2026-06-30 --strategy momentum \
        --run-id 156 --csv

The source SQLite database and config file are never modified.  Position-count
and supported sector-cap scenarios use an SQLite Online Backup copy and a
temporary YAML file.  Cash-allocation and beta-target scenarios are calculated
post-hoc from an existing run; sector-cap also falls back to post-hoc when the
installed BacktestRunner does not consume max_sector_weight.
"""

from __future__ import annotations

import argparse
import copy
import gc
import inspect
import math
import sqlite3
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from scripts.beta_analysis_common import (  # noqa: E402
    DEFAULT_BENCHMARK_CODE,
    AnalysisError,
    benchmark_close_series,
    build_daily_beta_decomposition,
    connect,
    connect_readonly,
    load_close_frame,
    load_equity_curve,
    load_fills,
    monthly_metrics,
    overlay_equity_curve,
    safe_slug,
    select_run,
    write_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BacktestRunnerと既存runのpost-hoc補正で4つの反実仮想を比較します。"
    )
    parser.add_argument("--from", dest="start_date", required=True)
    parser.add_argument("--to", dest="end_date", required=True)
    parser.add_argument("--strategy", default="momentum")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--run-id", type=int, help="post-hoc補正に使う既存backtest run ID")
    parser.add_argument("--benchmark-code", default=DEFAULT_BENCHMARK_CODE)
    parser.add_argument("--fixed-max-positions", type=int, default=20)
    parser.add_argument(
        "--fixed-min-trade-price",
        type=float,
        default=None,
        help="固定ポジション数シナリオで価格下限も変更する場合のみ指定",
    )
    parser.add_argument(
        "--max-sector-weight",
        type=float,
        default=0.25,
        help="0.25または25の形式。BacktestRunnerのbacktest.max_sector_weightへ設定",
    )
    parser.add_argument("--target-beta", type=float, default=1.0)
    parser.add_argument("--beta-lookback", type=int, default=60)
    parser.add_argument("--csv", action="store_true")
    parser.add_argument("--output", help="月次結果CSVの出力先。--csvを暗黙に有効化")
    parser.add_argument("--output-dir", default="reports")
    return parser


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise AnalysisError(f"Config root must be a mapping: {path}")
    return loaded


def _set_nested(config: dict[str, Any], dotted_key: str, value: Any) -> None:
    current = config
    keys = dotted_key.split(".")
    for key in keys[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[keys[-1]] = value


def _get_nested(config: Mapping[str, Any], dotted_key: str, default: Any = None) -> Any:
    current: Any = config
    for key in dotted_key.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def _online_backup(source: Path, destination: Path) -> None:
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source_conn:
        with closing(sqlite3.connect(str(destination))) as destination_conn:
            source_conn.backup(destination_conn)
            integrity = destination_conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or str(integrity[0]).lower() != "ok":
                raise AnalysisError(
                    f"Temporary SQLite backup failed integrity_check: {integrity}"
                )


def _ensure_backtest_equity_curve_idle_value(database_path: Path) -> None:
    """Add the BacktestRunner-required column to an isolated database copy."""
    with closing(sqlite3.connect(str(database_path))) as conn:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(backtest_equity_curve)")
        }
        if "idle_value" not in columns:
            conn.execute(
                "ALTER TABLE backtest_equity_curve ADD COLUMN idle_value REAL"
            )
            conn.commit()


def _write_temp_config(
    config_data: dict[str, Any],
    *,
    database_path: Path,
    directory: Path,
    name: str,
) -> Path:
    payload = copy.deepcopy(config_data)
    _set_nested(payload, "database.path", str(database_path))
    path = directory / f"{name}.yaml"
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
    return path


@contextmanager
def _closing_sqlite_connection(
    database_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Commit or rollback, then deterministically release the SQLite handle."""
    conn = sqlite3.connect(str(database_path))
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _run_backtest_scenario(
    *,
    source_db: Path,
    base_config: dict[str, Any],
    overrides: Mapping[str, Any],
    scenario: str,
    strategy: str,
    start_date: str,
    end_date: str,
    benchmark_code: str,
) -> tuple[pd.DataFrame, int]:
    from src.backtest_runner import BacktestRunner

    class _TemporaryBacktestRunner(BacktestRunner):
        def _conn(self):
            return _closing_sqlite_connection(self.db_path)

    runner: BacktestRunner | None = None
    with tempfile.TemporaryDirectory(
        prefix=f"moomoo-{safe_slug(scenario)}-",
        ignore_cleanup_errors=True,
    ) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        try:
            temp_db = temp_dir / "moomoo.db"
            _online_backup(source_db, temp_db)
            _ensure_backtest_equity_curve_idle_value(temp_db)
            scenario_config = copy.deepcopy(base_config)
            for key, value in overrides.items():
                _set_nested(scenario_config, key, value)
            config_path = _write_temp_config(
                scenario_config,
                database_path=temp_db,
                directory=temp_dir,
                name="config",
            )
            runner = _TemporaryBacktestRunner(load_config(str(config_path)))
            run_id = runner.run(strategy, start_date, end_date)
            with closing(connect(temp_db)) as conn:
                run_info = select_run(
                    conn,
                    strategy_name=strategy,
                    start_date=start_date,
                    end_date=end_date,
                    run_id=run_id,
                )
                equity = load_equity_curve(conn, run_id).set_index("date")[
                    "total_equity"
                ]
                benchmark = benchmark_close_series(
                    conn,
                    benchmark_code=benchmark_code,
                    start_date=start_date,
                    end_date=end_date,
                )
                metrics = monthly_metrics(
                    equity_series=equity,
                    benchmark_series=benchmark,
                    initial_equity=run_info.initial_cash,
                    scenario=scenario,
                    implementation="BacktestRunner",
                )
            return metrics, run_id
        finally:
            runner = None
            gc.collect()
            time.sleep(1)


def _runner_supports_sector_cap() -> bool:
    """Return whether BacktestRunner actually consumes max_sector_weight."""
    from src.backtest_runner import BacktestRunner

    try:
        source = "\n".join(
            (
                inspect.getsource(BacktestRunner.__init__),
                inspect.getsource(BacktestRunner.run),
            )
        )
    except (OSError, TypeError):
        return False
    return "max_sector_weight" in source


def _finite_float(value: Any) -> float | None:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _ratio(numerator: Any, denominator: Any) -> float:
    numerator_value = _finite_float(numerator)
    denominator_value = _finite_float(denominator)
    if numerator_value is None or denominator_value is None or denominator_value <= 0:
        return 0.0
    return min(1.0, max(0.0, numerator_value / denominator_value))


def _posthoc_cash_to_benchmark(
    *,
    decomposition: pd.DataFrame,
    initial_equity: float,
) -> tuple[pd.Series, pd.DataFrame]:
    """Apply benchmark return to the prior day's idle-cash weight.

    The original strategy return is retained and only the return that the prior
    close's cash allocation would have earned in the benchmark is added.  Using
    the prior day's weight avoids look-ahead bias.
    """
    if decomposition.empty:
        raise AnalysisError("Cannot build cash counterfactual from empty data")

    counterfactual_equity = float(initial_equity)
    previous_source_equity = float(initial_equity)
    previous_cash_weight = 1.0
    equity_points: dict[str, float] = {}
    diagnostics: list[dict[str, Any]] = []

    for row in decomposition.sort_values("date").itertuples(index=False):
        day = str(row.date)
        source_equity = _finite_float(row.total_equity)
        if source_equity is None or source_equity <= 0:
            raise AnalysisError(f"Invalid total_equity for {day}: {row.total_equity}")
        source_return = source_equity / previous_source_equity - 1.0
        benchmark_return = _finite_float(row.benchmark_return_daily)
        cash_adjustment = (
            previous_cash_weight * benchmark_return
            if benchmark_return is not None
            else 0.0
        )
        counterfactual_return = source_return + cash_adjustment
        if counterfactual_return <= -1.0:
            raise AnalysisError(
                f"Cash counterfactual return is <= -100% on {day}: "
                f"{counterfactual_return}"
            )
        counterfactual_equity *= 1.0 + counterfactual_return
        equity_points[day] = counterfactual_equity

        current_cash_weight = _ratio(row.cash, row.total_equity)
        diagnostics.append(
            {
                "date": day,
                "scenario": "idle_cash_to_2559",
                "source_run_id": int(row.run_id),
                "source_return_daily": source_return,
                "benchmark_return_daily": benchmark_return,
                "prior_cash_weight": previous_cash_weight,
                "adjustment_return_daily": cash_adjustment,
                "counterfactual_return_daily": counterfactual_return,
                "counterfactual_equity": counterfactual_equity,
                "next_cash_weight": current_cash_weight,
            }
        )
        previous_cash_weight = current_cash_weight
        previous_source_equity = source_equity

    return pd.Series(equity_points, name="total_equity", dtype=float), pd.DataFrame(
        diagnostics
    )


def _posthoc_beta_target(
    *,
    decomposition: pd.DataFrame,
    initial_equity: float,
    target_beta: float,
) -> tuple[pd.Series, pd.DataFrame]:
    """Adjust only benchmark exposure so the invested sleeve targets beta.

    At each close, the next day's return receives a benchmark overlay equal to
    ``invested_ratio * (target_beta - holdings_implied_beta)``.  This preserves
    the source run's realized alpha, cash drag, fills, and transaction timing;
    only estimated market-beta exposure changes.
    """
    if decomposition.empty:
        raise AnalysisError("Cannot build beta counterfactual from empty data")

    counterfactual_equity = float(initial_equity)
    previous_source_equity = float(initial_equity)
    previous_holdings_beta: float | None = None
    previous_invested_ratio = 0.0
    previous_beta_coverage: float | None = None
    equity_points: dict[str, float] = {}
    diagnostics: list[dict[str, Any]] = []

    for row in decomposition.sort_values("date").itertuples(index=False):
        day = str(row.date)
        source_equity = _finite_float(row.total_equity)
        if source_equity is None or source_equity <= 0:
            raise AnalysisError(f"Invalid total_equity for {day}: {row.total_equity}")
        source_return = source_equity / previous_source_equity - 1.0
        benchmark_return = _finite_float(row.benchmark_return_daily)

        applied = previous_holdings_beta is not None and benchmark_return is not None
        beta_delta = (
            previous_invested_ratio * (float(target_beta) - previous_holdings_beta)
            if applied
            else 0.0
        )
        beta_adjustment = beta_delta * benchmark_return if applied else 0.0
        counterfactual_return = source_return + beta_adjustment
        if counterfactual_return <= -1.0:
            raise AnalysisError(
                f"Beta counterfactual return is <= -100% on {day}: "
                f"{counterfactual_return}"
            )
        counterfactual_equity *= 1.0 + counterfactual_return
        equity_points[day] = counterfactual_equity

        current_holdings_beta = _finite_float(row.holdings_implied_beta)
        current_invested_ratio = _ratio(row.position_value, row.total_equity)
        current_beta_coverage = _finite_float(row.beta_coverage_pct)
        diagnostics.append(
            {
                "date": day,
                "scenario": "target_holdings_beta",
                "source_run_id": int(row.run_id),
                "source_return_daily": source_return,
                "benchmark_return_daily": benchmark_return,
                "prior_holdings_implied_beta": previous_holdings_beta,
                "prior_invested_ratio": previous_invested_ratio,
                "prior_beta_coverage_pct": previous_beta_coverage,
                "target_holdings_beta": float(target_beta),
                "portfolio_beta_delta": beta_delta,
                "adjustment_return_daily": beta_adjustment,
                "counterfactual_return_daily": counterfactual_return,
                "counterfactual_equity": counterfactual_equity,
                "adjustment_applied": applied,
                "next_holdings_implied_beta": current_holdings_beta,
                "next_invested_ratio": current_invested_ratio,
            }
        )
        previous_holdings_beta = current_holdings_beta
        previous_invested_ratio = current_invested_ratio
        previous_beta_coverage = current_beta_coverage
        previous_source_equity = source_equity

    return pd.Series(equity_points, name="total_equity", dtype=float), pd.DataFrame(
        diagnostics
    )


def _window_equity(
    equity: pd.Series,
    *,
    start_date: str,
    end_date: str,
    default_initial_equity: float,
) -> tuple[pd.Series, float]:
    ordered = equity.dropna().astype(float).sort_index()
    prior = ordered[ordered.index < start_date]
    initial_equity = (
        float(prior.iloc[-1]) if not prior.empty else float(default_initial_equity)
    )
    window = ordered[(ordered.index >= start_date) & (ordered.index <= end_date)]
    if window.empty:
        raise AnalysisError(f"No counterfactual equity in {start_date}..{end_date}")
    return window, initial_equity


def _run_posthoc_scenarios(
    *,
    source_db: Path,
    strategy: str,
    start_date: str,
    end_date: str,
    run_id: int | None,
    benchmark_code: str,
    target_beta: float,
    beta_lookback: int,
    max_sector_weight: float,
    include_sector_fallback: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    close_frame = pd.DataFrame()
    with closing(connect_readonly(source_db)) as conn:
        run_info = select_run(
            conn,
            strategy_name=strategy,
            start_date=start_date,
            end_date=end_date,
            run_id=run_id,
        )
        # Build from the run start so the requested window's first observation
        # still has the correct prior-day cash and holdings beta exposure.
        decomposition = build_daily_beta_decomposition(
            conn,
            run_info=run_info,
            start_date=run_info.start_date,
            end_date=end_date,
            benchmark_code=benchmark_code,
            beta_lookback_days=beta_lookback,
        )
        benchmark = benchmark_close_series(
            conn,
            benchmark_code=benchmark_code,
            start_date=start_date,
            end_date=end_date,
        )
        if include_sector_fallback:
            fills = load_fills(conn, run_info.run_id)
            codes = set(fills["code"].astype(str)) if not fills.empty else set()
            codes.add(benchmark_code)
            close_frame = load_close_frame(conn, codes=codes, end_date=end_date)

    cash_equity_all, cash_diagnostics = _posthoc_cash_to_benchmark(
        decomposition=decomposition,
        initial_equity=run_info.initial_cash,
    )
    beta_equity_all, beta_diagnostics = _posthoc_beta_target(
        decomposition=decomposition,
        initial_equity=run_info.initial_cash,
        target_beta=target_beta,
    )
    cash_equity, cash_initial = _window_equity(
        cash_equity_all,
        start_date=start_date,
        end_date=end_date,
        default_initial_equity=run_info.initial_cash,
    )
    beta_equity, beta_initial = _window_equity(
        beta_equity_all,
        start_date=start_date,
        end_date=end_date,
        default_initial_equity=run_info.initial_cash,
    )

    metrics = [
        monthly_metrics(
            equity_series=cash_equity,
            benchmark_series=benchmark,
            initial_equity=cash_initial,
            scenario="idle_cash_to_2559",
            implementation="Existing run + post-hoc prior-cash benchmark return",
        )
    ]
    diagnostic_frames = [cash_diagnostics]

    if include_sector_fallback:
        sector_equity_all, sector_diagnostics = overlay_equity_curve(
            decomposition=decomposition,
            close_frame=close_frame,
            initial_equity=run_info.initial_cash,
            mode="sector_cap",
            max_sector_weight=max_sector_weight,
        )
        sector_equity, sector_initial = _window_equity(
            sector_equity_all,
            start_date=start_date,
            end_date=end_date,
            default_initial_equity=run_info.initial_cash,
        )
        metrics.append(
            monthly_metrics(
                equity_series=sector_equity,
                benchmark_series=benchmark,
                initial_equity=sector_initial,
                scenario="sector_weight_cap",
                implementation="Existing run + post-hoc sector-cap weight overlay",
            )
        )
        diagnostic_frames.append(
            sector_diagnostics.assign(
                scenario="sector_weight_cap",
                source_run_id=run_info.run_id,
            )
        )

    metrics.append(
        monthly_metrics(
            equity_series=beta_equity,
            benchmark_series=benchmark,
            initial_equity=beta_initial,
            scenario="target_holdings_beta",
            implementation="Existing run + post-hoc beta exposure scaling",
        )
    )
    diagnostic_frames.append(beta_diagnostics)

    diagnostics = pd.concat(
        diagnostic_frames, ignore_index=True, sort=False
    )
    diagnostics = diagnostics[
        (diagnostics["date"] >= start_date)
        & (diagnostics["date"] <= end_date)
    ].reset_index(drop=True)
    return pd.concat(metrics, ignore_index=True), diagnostics, run_info.run_id


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.start_date > args.end_date:
        raise SystemExit("--from must be on or before --to")
    if args.run_id is not None and args.run_id <= 0:
        raise SystemExit("--run-id must be > 0")
    if args.fixed_max_positions <= 0:
        raise SystemExit("--fixed-max-positions must be > 0")
    if args.fixed_min_trade_price is not None and args.fixed_min_trade_price < 0:
        raise SystemExit("--fixed-min-trade-price must be >= 0")
    if args.beta_lookback < 2:
        raise SystemExit("--beta-lookback must be >= 2")
    if not math.isfinite(args.target_beta) or args.target_beta < 0:
        raise SystemExit("--target-beta must be a finite value >= 0")
    sector_weight = (
        args.max_sector_weight / 100.0
        if args.max_sector_weight > 1
        else args.max_sector_weight
    )
    if not 0 < sector_weight <= 1:
        raise SystemExit("--max-sector-weight must be in (0,1] or (0,100]")

    config_path = Path(args.config).expanduser().resolve()
    try:
        base_config = _read_yaml(config_path)
        database_value = _get_nested(base_config, "database.path", "data/moomoo.db")
        source_db = Path(str(database_value)).expanduser()
        if not source_db.is_absolute():
            source_db = (config_path.parent / source_db).resolve()
        if not source_db.exists():
            raise FileNotFoundError(f"SQLite database not found: {source_db}")

        sector_runner_supported = _runner_supports_sector_cap()
        posthoc_metrics, posthoc_diagnostics, source_run_id = _run_posthoc_scenarios(
            source_db=source_db,
            strategy=args.strategy,
            start_date=args.start_date,
            end_date=args.end_date,
            run_id=args.run_id,
            benchmark_code=args.benchmark_code,
            target_beta=args.target_beta,
            beta_lookback=args.beta_lookback,
            max_sector_weight=sector_weight,
            include_sector_fallback=not sector_runner_supported,
        )

        position_overrides: dict[str, Any] = {
            "backtest.max_positions": args.fixed_max_positions,
            # Keep direct runner scenarios limited to their requested variable;
            # idle-cash allocation is analyzed separately by the post-hoc scenario.
            "backtest.idle_cash_allocation.enabled": False,
        }
        if args.fixed_min_trade_price is not None:
            position_overrides["universe.min_trade_price"] = (
                args.fixed_min_trade_price
            )
        position_metrics, position_run_id = _run_backtest_scenario(
            source_db=source_db,
            base_config=base_config,
            overrides=position_overrides,
            scenario="fixed_position_count",
            strategy=args.strategy,
            start_date=args.start_date,
            end_date=args.end_date,
            benchmark_code=args.benchmark_code,
        )
        if sector_runner_supported:
            sector_metrics, sector_run_id = _run_backtest_scenario(
                source_db=source_db,
                base_config=base_config,
                overrides={
                    "backtest.max_sector_weight": sector_weight,
                    "backtest.idle_cash_allocation.enabled": False,
                },
                scenario="sector_weight_cap",
                strategy=args.strategy,
                start_date=args.start_date,
                end_date=args.end_date,
                benchmark_code=args.benchmark_code,
            )
        else:
            print(
                "[WARN] BacktestRunner does not consume "
                "backtest.max_sector_weight; using post-hoc sector overlay.",
                file=sys.stderr,
            )
            sector_metrics = posthoc_metrics[
                posthoc_metrics["scenario"] == "sector_weight_cap"
            ].copy()
            sector_run_id = source_run_id
    except (
        AnalysisError,
        FileNotFoundError,
        OSError,
        ValueError,
        sqlite3.Error,
    ) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    posthoc_metrics["engine_run_id"] = source_run_id
    position_metrics["engine_run_id"] = position_run_id
    sector_metrics["engine_run_id"] = sector_run_id
    cash_metrics = posthoc_metrics[
        posthoc_metrics["scenario"] == "idle_cash_to_2559"
    ]
    beta_metrics = posthoc_metrics[
        posthoc_metrics["scenario"] == "target_holdings_beta"
    ]
    result = pd.concat(
        [cash_metrics, position_metrics, sector_metrics, beta_metrics],
        ignore_index=True,
    )
    result["strategy"] = args.strategy
    result["from"] = args.start_date
    result["to"] = args.end_date
    result["benchmark_code"] = args.benchmark_code
    result["source_database_mutated"] = False

    print(
        result[
            [
                "scenario",
                "month",
                "monthly_return_pct",
                "benchmark_return_pct",
                "active_return_pct",
                "realized_beta",
            ]
        ].to_string(index=False, float_format=lambda value: f"{value:.3f}")
    )

    if args.csv or args.output:
        output_dir = Path(args.output_dir)
        output = (
            Path(args.output)
            if args.output
            else output_dir
            / (
                "counterfactual_analysis_"
                f"{safe_slug(args.strategy)}_{args.start_date}_{args.end_date}.csv"
            )
        )
        diagnostics_path = output.with_name(
            f"{output.stem}_posthoc_diagnostics.csv"
        )
        write_csv(result, output)
        write_csv(posthoc_diagnostics, diagnostics_path)
        print(f"[OK] CSV: {output}")
        print(f"[OK] post-hoc diagnostics: {diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
