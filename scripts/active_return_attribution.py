"""
アクティブリターン要因分解CLI。

ファイルパス: scripts/active_return_attribution.py
何をするか:
    バックテストの日次equity/fillと銘柄別日足を使い、2559に対する
    アクティブリターンを月別に分解する。
関連ファイル:
    src/backtest_runner.py, historical_backtest.py, src/models.py

実行例:
    python scripts/active_return_attribution.py \
        --from 2026-01-01 --to 2026-06-30 \
        --strategy momentum --csv

出力列（リターン・寄与度は百分率、betaは無次元）:
    strategy_return
    benchmark_return
    strategy_cash_drag
    strategy_portfolio_beta
    strategy_sector_allocation
    strategy_within_sector

方法:
- strategy_return: backtest_equity_curve.total_equityの日次リターンを月次複利化。
- benchmark_return: daily_barsのJP.2559終値リターンを月次複利化。
- cash drag: 前日現金比率 × (2559リターン - idle cash投資先リターン)。
  idle cash allocationが無効なら投資先リターンは0、JP.2559ならdragは0となる。
- portfolio beta: 戦略日次リターンと2559日次リターンのローリングbeta。
  月次CSVには各月末時点のbetaを出力する。
- sector allocation / within-sector selection: 日次Brinson型要因分解。
  2559自体の構成銘柄・業種ウェイトはDBにないため、enabledかつtradableな
  trade_candidateを等ウェイト業種ベンチマークとして使用し、業種間の相対
  リターンを2559の日次リターンへアンカーする。
- 月次寄与度: 日次寄与度をCarino linkingで月次複利リターンへ接続する。
- strategy_residual: 売買日のopen約定、スリッページ、欠損データ等による残差。

注意:
    このスクリプトは分析時のDB接続をread-onlyで開く。完全一致する
    backtest_runが存在しない場合だけ、既定ではBacktestRunnerを実行して
    runを作成する。DB変更を避けたい場合は--no-auto-runを指定する。
"""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence
from urllib.parse import quote

import pandas as pd
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = "JP.2559"
UNKNOWN_SECTOR = "未分類"
EPSILON = 1e-12


@dataclass(frozen=True)
class RunInfo:
    run_id: int
    strategy_name: str
    start_date: str
    end_date: str
    initial_cash: float


@dataclass(frozen=True)
class IdleCashPolicy:
    enabled: bool
    benchmark_code: str | None


class AttributionError(RuntimeError):
    """入力データまたはDB状態が要因分解に不適切な場合の例外。"""


def _parse_iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"日付はYYYY-MM-DD形式で指定してください: {value}"
        ) from exc


def _resolve_path(path: str | Path, *, base: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base / candidate).resolve()


def _load_yaml_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    with config_path.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise AttributionError(f"設定ファイルのルートはmappingである必要があります: {config_path}")
    return loaded


def _nested_get(mapping: Mapping, dotted_key: str, default=None):
    current = mapping
    for key in dotted_key.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def _database_path(config: Mapping, config_path: Path, explicit_db: str | None) -> Path:
    if explicit_db:
        return _resolve_path(explicit_db, base=Path.cwd())
    configured = _nested_get(config, "database.path", "data/moomoo.db")
    return _resolve_path(str(configured), base=config_path.parent)


def _idle_cash_policy(config: Mapping) -> IdleCashPolicy:
    allocation = _nested_get(config, "backtest.idle_cash_allocation", {})
    if not isinstance(allocation, Mapping):
        return IdleCashPolicy(enabled=False, benchmark_code=None)
    enabled = bool(allocation.get("enabled", False))
    code = allocation.get("benchmark_code") if enabled else None
    return IdleCashPolicy(enabled=enabled, benchmark_code=str(code) if code else None)


def _read_only_connection(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise AttributionError(f"SQLite DBが見つかりません: {db_path}")
    normalized = db_path.resolve().as_posix()
    uri = f"file:{quote(normalized, safe='/:')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _validate_schema(conn: sqlite3.Connection) -> None:
    required: dict[str, set[str]] = {
        "symbols": {"code", "sector", "role", "tradable", "enabled"},
        "daily_bars": {"code", "date", "close"},
        "backtest_runs": {
            "id",
            "strategy_name",
            "start_date",
            "end_date",
            "initial_cash",
        },
        "backtest_fills": {
            "id",
            "run_id",
            "code",
            "side",
            "quantity",
            "filled_at",
        },
        "backtest_equity_curve": {
            "run_id",
            "date",
            "cash",
            "position_value",
            "total_equity",
        },
    }
    existing_tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing_tables = sorted(set(required) - existing_tables)
    if missing_tables:
        raise AttributionError(f"必要なテーブルがありません: {', '.join(missing_tables)}")

    missing_columns: list[str] = []
    for table, columns in required.items():
        actual = _table_columns(conn, table)
        for column in sorted(columns - actual):
            missing_columns.append(f"{table}.{column}")
    if missing_columns:
        raise AttributionError(f"必要なカラムがありません: {', '.join(missing_columns)}")


def _find_exact_run(
    conn: sqlite3.Connection,
    strategy: str,
    from_date: str,
    to_date: str,
) -> RunInfo | None:
    row = conn.execute(
        """
        SELECT id, strategy_name, start_date, end_date, initial_cash
        FROM backtest_runs
        WHERE strategy_name = ? AND start_date = ? AND end_date = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (strategy, from_date, to_date),
    ).fetchone()
    if row is None:
        return None
    return RunInfo(
        run_id=int(row["id"]),
        strategy_name=str(row["strategy_name"]),
        start_date=str(row["start_date"]),
        end_date=str(row["end_date"]),
        initial_cash=float(row["initial_cash"]),
    )


def _load_run_by_id(conn: sqlite3.Connection, run_id: int) -> RunInfo:
    row = conn.execute(
        """
        SELECT id, strategy_name, start_date, end_date, initial_cash
        FROM backtest_runs
        WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise AttributionError(f"backtest runが見つかりません: run_id={run_id}")
    return RunInfo(
        run_id=int(row["id"]),
        strategy_name=str(row["strategy_name"]),
        start_date=str(row["start_date"]),
        end_date=str(row["end_date"]),
        initial_cash=float(row["initial_cash"]),
    )


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _create_backtest_run(config_path: Path, strategy: str, from_date: str, to_date: str) -> int:
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from src.backtest_runner import BacktestRunner
        from src.config import load_config
    except ImportError as exc:
        raise AttributionError(
            "BacktestRunnerをimportできません。リポジトリルートで依存関係を導入してください"
        ) from exc

    with _working_directory(config_path.parent):
        config = load_config(str(config_path))
        runner = BacktestRunner(config)
        return int(runner.run(strategy, from_date, to_date))


def _ensure_run(
    db_path: Path,
    config_path: Path,
    strategy: str,
    from_date: str,
    to_date: str,
    run_id: int | None,
    auto_run: bool,
    explicit_db: bool,
) -> RunInfo:
    with _read_only_connection(db_path) as conn:
        _validate_schema(conn)
        if run_id is not None:
            run = _load_run_by_id(conn, run_id)
            if run.strategy_name != strategy:
                raise AttributionError(
                    f"run_id={run_id}のstrategyは{run.strategy_name}です（指定: {strategy}）"
                )
            if from_date < run.start_date or to_date > run.end_date:
                raise AttributionError(
                    f"指定期間{from_date}〜{to_date}はrun期間"
                    f"{run.start_date}〜{run.end_date}の範囲外です"
                )
            return run

        exact = _find_exact_run(conn, strategy, from_date, to_date)
        if exact is not None:
            return exact

    if not auto_run:
        raise AttributionError(
            "完全一致するbacktest runがありません。historical_backtest.pyを先に実行するか、"
            "--auto-runを有効にしてください"
        )
    if explicit_db:
        raise AttributionError(
            "--db指定時は安全のためbacktestを自動作成しません。"
            "対象DBでhistorical_backtest.pyを実行後、--no-auto-runで再実行してください"
        )
    if not config_path.exists():
        raise AttributionError(f"backtest自動実行に必要な設定ファイルがありません: {config_path}")

    print("[INFO] 完全一致するrunがないため、BacktestRunnerを実行します")
    created_run_id = _create_backtest_run(config_path, strategy, from_date, to_date)
    with _read_only_connection(db_path) as conn:
        _validate_schema(conn)
        return _load_run_by_id(conn, created_run_id)


def _load_equity_curve(
    conn: sqlite3.Connection,
    run: RunInfo,
    to_date: str,
) -> pd.DataFrame:
    frame = pd.read_sql_query(
        """
        SELECT date, cash, position_value, total_equity
        FROM backtest_equity_curve
        WHERE run_id = ? AND date <= ?
        ORDER BY date
        """,
        conn,
        params=[run.run_id, to_date],
    )
    if frame.empty:
        raise AttributionError(f"equity curveがありません: run_id={run.run_id}")
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame = frame.drop_duplicates(subset=["date"], keep="last").set_index("date").sort_index()
    for column in ("cash", "position_value", "total_equity"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame["total_equity"].isna().any() or (frame["total_equity"] <= 0).any():
        raise AttributionError("total_equityにNULLまたは0以下の値があります")
    return frame


def _load_fills(conn: sqlite3.Connection, run_id: int, to_date: str) -> pd.DataFrame:
    fills = pd.read_sql_query(
        """
        SELECT id, code, side, quantity, filled_at
        FROM backtest_fills
        WHERE run_id = ? AND substr(filled_at, 1, 10) <= ?
        ORDER BY substr(filled_at, 1, 10), id
        """,
        conn,
        params=[run_id, to_date],
    )
    if fills.empty:
        return pd.DataFrame(columns=["id", "code", "side", "quantity", "filled_at", "date"])
    fills["date"] = pd.to_datetime(fills["filled_at"].astype(str).str[:10], errors="raise")
    fills["quantity"] = pd.to_numeric(fills["quantity"], errors="raise").astype(int)
    fills["side"] = fills["side"].astype(str).str.upper()
    invalid_sides = sorted(set(fills["side"]) - {"BUY", "SELL"})
    if invalid_sides:
        raise AttributionError(f"未対応のfill sideがあります: {', '.join(invalid_sides)}")
    return fills


def _load_symbols(conn: sqlite3.Connection) -> pd.DataFrame:
    symbols = pd.read_sql_query(
        """
        SELECT code, sector, role, tradable, enabled
        FROM symbols
        WHERE enabled = 1
        """,
        conn,
    )
    if symbols.empty:
        raise AttributionError("symbolsにenabled銘柄がありません")
    symbols["code"] = symbols["code"].astype(str)
    symbols["sector_raw"] = symbols["sector"]
    symbols["sector"] = (
        symbols["sector"]
        .fillna(UNKNOWN_SECTOR)
        .astype(str)
        .str.strip()
        .replace("", UNKNOWN_SECTOR)
    )
    return symbols.drop_duplicates(subset=["code"], keep="last")


def _chunks(values: Sequence[str], size: int = 500) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _load_close_prices(
    conn: sqlite3.Connection,
    codes: Sequence[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    unique_codes = sorted(set(codes))
    if not unique_codes:
        raise AttributionError("価格取得対象コードが空です")

    frames: list[pd.DataFrame] = []
    for code_chunk in _chunks(unique_codes):
        placeholders = ",".join("?" for _ in code_chunk)
        query = f"""
            SELECT code, date, close
            FROM daily_bars
            WHERE code IN ({placeholders})
              AND date >= ?
              AND date <= ?
              AND close IS NOT NULL
            ORDER BY date, code
        """
        params = [*code_chunk, start_date, end_date]
        frames.append(pd.read_sql_query(query, conn, params=params))

    prices = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if prices.empty:
        raise AttributionError(f"daily_barsに価格がありません: {start_date}〜{end_date}")
    prices["date"] = pd.to_datetime(prices["date"], errors="raise")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["close"])
    pivot = prices.pivot_table(index="date", columns="code", values="close", aggfunc="last")
    return pivot.sort_index().ffill()


def _position_snapshots(
    dates: pd.DatetimeIndex,
    fills: pd.DataFrame,
) -> dict[pd.Timestamp, dict[str, int]]:
    grouped: dict[pd.Timestamp, list[tuple[str, str, int]]] = defaultdict(list)
    for row in fills.itertuples(index=False):
        grouped[pd.Timestamp(row.date)].append((str(row.code), str(row.side), int(row.quantity)))

    quantities: dict[str, int] = defaultdict(int)
    snapshots: dict[pd.Timestamp, dict[str, int]] = {}
    for day in dates:
        for code, side, quantity in grouped.get(pd.Timestamp(day), []):
            quantities[code] += quantity if side == "BUY" else -quantity
            if quantities[code] < 0:
                raise AttributionError(
                    f"fill再構築で保有数量が負になりました: {code} {day.date()} qty={quantities[code]}"
                )
            if quantities[code] == 0:
                quantities.pop(code, None)
        snapshots[pd.Timestamp(day)] = dict(quantities)
    return snapshots


def _safe_return(current: float, previous: float) -> float:
    if not math.isfinite(current) or not math.isfinite(previous) or previous <= 0:
        return math.nan
    return current / previous - 1.0


def _rolling_beta(strategy_returns: pd.Series, benchmark_returns: pd.Series, window: int) -> pd.Series:
    min_periods = max(5, min(window, max(10, window // 2)))
    covariance = strategy_returns.rolling(window, min_periods=min_periods).cov(benchmark_returns)
    variance = benchmark_returns.rolling(window, min_periods=min_periods).var()
    return covariance / variance.where(variance.abs() > EPSILON)


def _daily_sector_effects(
    day: pd.Timestamp,
    previous_day: pd.Timestamp | None,
    previous_holdings: Mapping[str, int],
    previous_invested_weight: float,
    benchmark_return: float,
    price_matrix: pd.DataFrame,
    return_matrix: pd.DataFrame,
    sector_by_code: Mapping[str, str],
    has_known_sector: Mapping[str, bool],
    universe_codes: Sequence[str],
) -> tuple[float, float, float, float]:
    """allocation, selection(+interaction), portfolio coverage, universe coverage。"""
    if previous_day is None or not math.isfinite(benchmark_return):
        return 0.0, 0.0, 1.0, math.nan
    if previous_day not in price_matrix.index or day not in return_matrix.index:
        return 0.0, 0.0, 1.0, math.nan

    day_returns = return_matrix.loc[day]
    previous_prices = price_matrix.loc[previous_day]

    valid_universe = [
        code
        for code in universe_codes
        if code in day_returns.index and pd.notna(day_returns.get(code))
    ]
    if not valid_universe:
        return 0.0, 0.0, 1.0, math.nan

    universe_frame = pd.DataFrame(
        {
            "code": valid_universe,
            "return": [float(day_returns[code]) for code in valid_universe],
            "sector": [sector_by_code.get(code, UNKNOWN_SECTOR) for code in valid_universe],
            "known_sector": [bool(has_known_sector.get(code, False)) for code in valid_universe],
        }
    )
    raw_market_return = float(universe_frame["return"].mean())
    sector_group = universe_frame.groupby("sector", sort=False)
    benchmark_sector_return = sector_group["return"].mean()
    benchmark_sector_weight = sector_group.size().astype(float) / len(universe_frame)
    # 業種間スプレッドはuniverseから取得し、全体水準は2559へ合わせる。
    benchmark_sector_return = benchmark_sector_return - raw_market_return + benchmark_return
    universe_coverage = float(universe_frame["known_sector"].mean())

    holding_rows: list[dict] = []
    for code, quantity in previous_holdings.items():
        price = previous_prices.get(code)
        stock_return = day_returns.get(code)
        if quantity <= 0 or pd.isna(price) or pd.isna(stock_return):
            continue
        value = float(price) * int(quantity)
        if value <= 0:
            continue
        holding_rows.append(
            {
                "code": code,
                "value": value,
                "return": float(stock_return),
                "sector": sector_by_code.get(code, UNKNOWN_SECTOR),
                "known_sector": bool(has_known_sector.get(code, False)),
            }
        )

    if not holding_rows or previous_invested_weight <= EPSILON:
        return 0.0, 0.0, 1.0, universe_coverage

    holdings = pd.DataFrame(holding_rows)
    total_value = float(holdings["value"].sum())
    if total_value <= 0:
        return 0.0, 0.0, 1.0, universe_coverage
    portfolio_coverage = float(
        holdings.loc[holdings["known_sector"], "value"].sum() / total_value
    )

    holdings["weighted_return"] = holdings["value"] * holdings["return"]
    portfolio_group = holdings.groupby("sector", sort=False)
    portfolio_sector_value = portfolio_group["value"].sum()
    portfolio_sector_weight = portfolio_sector_value / total_value
    portfolio_sector_return = portfolio_group["weighted_return"].sum() / portfolio_sector_value

    sectors = sorted(
        set(benchmark_sector_weight.index.astype(str))
        | set(portfolio_sector_weight.index.astype(str))
    )
    allocation = 0.0
    selection = 0.0
    for sector in sectors:
        portfolio_weight = float(portfolio_sector_weight.get(sector, 0.0))
        benchmark_weight = float(benchmark_sector_weight.get(sector, 0.0))
        sector_benchmark_return = float(
            benchmark_sector_return.get(sector, benchmark_return)
        )
        sector_portfolio_return = float(
            portfolio_sector_return.get(sector, sector_benchmark_return)
        )
        allocation += (
            portfolio_weight - benchmark_weight
        ) * (sector_benchmark_return - benchmark_return)
        # selectionとinteractionを一体化し、portfolio実ウェイトで寄与を測る。
        selection += portfolio_weight * (
            sector_portfolio_return - sector_benchmark_return
        )

    invested_weight = max(0.0, float(previous_invested_weight))
    return (
        invested_weight * allocation,
        invested_weight * selection,
        portfolio_coverage,
        universe_coverage,
    )


def _build_daily_attribution(
    equity: pd.DataFrame,
    fills: pd.DataFrame,
    symbols: pd.DataFrame,
    price_matrix: pd.DataFrame,
    run: RunInfo,
    from_date: str,
    to_date: str,
    benchmark_code: str,
    idle_policy: IdleCashPolicy,
    beta_window: int,
) -> pd.DataFrame:
    if benchmark_code not in price_matrix.columns:
        raise AttributionError(f"ベンチマーク価格がありません: {benchmark_code}")

    full_dates = equity.index
    snapshots = _position_snapshots(full_dates, fills)
    price_matrix = price_matrix.reindex(price_matrix.index.union(full_dates)).sort_index().ffill()
    returns = price_matrix.pct_change(fill_method=None)

    equity = equity.copy()
    previous_equity = equity["total_equity"].shift(1)
    previous_cash = equity["cash"].shift(1)
    previous_positions = equity["position_value"].shift(1)
    previous_equity.iloc[0] = run.initial_cash
    previous_cash.iloc[0] = run.initial_cash
    previous_positions.iloc[0] = 0.0

    equity["strategy_daily_return"] = equity["total_equity"] / previous_equity - 1.0
    equity["previous_cash_weight"] = previous_cash / previous_equity
    equity["previous_invested_weight"] = previous_positions / previous_equity

    benchmark_returns = returns[benchmark_code].reindex(full_dates)
    equity["benchmark_daily_return"] = benchmark_returns

    if idle_policy.enabled and idle_policy.benchmark_code:
        idle_code = idle_policy.benchmark_code
        if idle_code not in price_matrix.columns:
            raise AttributionError(
                f"idle cash benchmarkの価格がありません: {idle_code}"
            )
        idle_returns = returns[idle_code].reindex(full_dates)
    else:
        idle_returns = pd.Series(0.0, index=full_dates)
    equity["idle_daily_return"] = idle_returns.fillna(0.0)
    equity["cash_drag_daily"] = equity["previous_cash_weight"] * (
        equity["benchmark_daily_return"] - equity["idle_daily_return"]
    )

    beta = _rolling_beta(
        equity["strategy_daily_return"],
        equity["benchmark_daily_return"],
        beta_window,
    )
    equity["portfolio_beta"] = beta

    symbol_index = symbols.set_index("code")
    sector_by_code = symbol_index["sector"].astype(str).to_dict()
    has_known_sector = (
        symbol_index["sector_raw"].notna()
        & symbol_index["sector_raw"].astype(str).str.strip().ne("")
    ).to_dict()
    universe = symbols[
        (symbols["role"] == "trade_candidate")
        & (pd.to_numeric(symbols["tradable"], errors="coerce").fillna(0).astype(int) == 1)
    ]
    universe_codes = [
        code for code in universe["code"].astype(str).tolist() if code in price_matrix.columns
    ]
    if not universe_codes:
        raise AttributionError("業種ベンチマークを構成できるtrade_candidate価格がありません")

    allocations: list[float] = []
    selections: list[float] = []
    portfolio_coverages: list[float] = []
    universe_coverages: list[float] = []
    date_positions = {day: index for index, day in enumerate(full_dates)}

    for day in full_dates:
        index = date_positions[day]
        previous_day = full_dates[index - 1] if index > 0 else None
        previous_holdings = snapshots.get(previous_day, {}) if previous_day is not None else {}
        allocation, selection, portfolio_coverage, universe_coverage = _daily_sector_effects(
            day=day,
            previous_day=previous_day,
            previous_holdings=previous_holdings,
            previous_invested_weight=float(equity.at[day, "previous_invested_weight"]),
            benchmark_return=float(equity.at[day, "benchmark_daily_return"]),
            price_matrix=price_matrix,
            return_matrix=returns,
            sector_by_code=sector_by_code,
            has_known_sector=has_known_sector,
            universe_codes=universe_codes,
        )
        allocations.append(allocation)
        selections.append(selection)
        portfolio_coverages.append(portfolio_coverage)
        universe_coverages.append(universe_coverage)

    equity["sector_allocation_daily"] = allocations
    equity["within_sector_daily"] = selections
    equity["portfolio_sector_coverage"] = portfolio_coverages
    equity["benchmark_sector_coverage"] = universe_coverages

    # active = allocation + selection - cash_drag + residual
    equity["active_daily_return"] = (
        equity["strategy_daily_return"] - equity["benchmark_daily_return"]
    )
    equity["residual_daily"] = (
        equity["active_daily_return"]
        - equity["sector_allocation_daily"]
        - equity["within_sector_daily"]
        + equity["cash_drag_daily"]
    )

    result = equity.loc[pd.Timestamp(from_date) : pd.Timestamp(to_date)].copy()
    if result.empty:
        raise AttributionError(f"指定期間にequity curveがありません: {from_date}〜{to_date}")
    required_returns = result[["strategy_daily_return", "benchmark_daily_return"]]
    if required_returns.isna().any().any():
        missing_days = required_returns[required_returns.isna().any(axis=1)].index.strftime("%Y-%m-%d")
        preview = ", ".join(missing_days[:5])
        raise AttributionError(
            f"戦略またはベンチマーク日次リターンを計算できない日があります: {preview}"
        )
    return result


def _geometric_return(series: pd.Series) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return math.nan
    if (clean <= -1.0).any():
        raise AttributionError("-100%以下の日次リターンがあるため複利計算できません")
    return float((1.0 + clean).prod() - 1.0)


def _log_return_ratio(portfolio_return: float, benchmark_return: float) -> float:
    if portfolio_return <= -1.0 or benchmark_return <= -1.0:
        return math.nan
    difference = portfolio_return - benchmark_return
    if abs(difference) <= EPSILON:
        return 1.0 / (1.0 + (portfolio_return + benchmark_return) / 2.0)
    return (math.log1p(portfolio_return) - math.log1p(benchmark_return)) / difference


def _carino_link(
    group: pd.DataFrame,
    contribution_column: str,
    monthly_strategy_return: float,
    monthly_benchmark_return: float,
) -> float:
    monthly_factor = _log_return_ratio(monthly_strategy_return, monthly_benchmark_return)
    if not math.isfinite(monthly_factor) or abs(monthly_factor) <= EPSILON:
        return float(group[contribution_column].sum())

    total = 0.0
    for row in group.itertuples():
        daily_strategy = float(row.strategy_daily_return)
        daily_benchmark = float(row.benchmark_daily_return)
        daily_factor = _log_return_ratio(daily_strategy, daily_benchmark)
        contribution = float(getattr(row, contribution_column))
        if math.isfinite(daily_factor) and math.isfinite(contribution):
            total += contribution * daily_factor / monthly_factor
    return total


def _last_finite(series: pd.Series) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(clean.iloc[-1]) if not clean.empty else math.nan


def _aggregate_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    grouped = daily.groupby(daily.index.to_period("M"), sort=True)
    for period, group in grouped:
        strategy_return = _geometric_return(group["strategy_daily_return"])
        benchmark_return = _geometric_return(group["benchmark_daily_return"])
        active_return = strategy_return - benchmark_return

        # cash_dragは正の値をheadwindとして表示するため、寄与度は一度負符号でlinkする。
        linked_cash_contribution = _carino_link(
            group.assign(cash_contribution_daily=-group["cash_drag_daily"]),
            "cash_contribution_daily",
            strategy_return,
            benchmark_return,
        )
        sector_allocation = _carino_link(
            group,
            "sector_allocation_daily",
            strategy_return,
            benchmark_return,
        )
        within_sector = _carino_link(
            group,
            "within_sector_daily",
            strategy_return,
            benchmark_return,
        )
        residual = _carino_link(
            group,
            "residual_daily",
            strategy_return,
            benchmark_return,
        )
        cash_drag = -linked_cash_contribution
        reconciliation_error = active_return - (
            sector_allocation + within_sector - cash_drag + residual
        )

        rows.append(
            {
                "month": str(period),
                "strategy_return": strategy_return * 100.0,
                "benchmark_return": benchmark_return * 100.0,
                "active_return": active_return * 100.0,
                "strategy_cash_drag": cash_drag * 100.0,
                "strategy_portfolio_beta": _last_finite(group["portfolio_beta"]),
                "strategy_sector_allocation": sector_allocation * 100.0,
                "strategy_within_sector": within_sector * 100.0,
                "strategy_residual": residual * 100.0,
                "portfolio_sector_coverage": float(
                    pd.to_numeric(group["portfolio_sector_coverage"], errors="coerce").mean()
                    * 100.0
                ),
                "benchmark_sector_coverage": float(
                    pd.to_numeric(group["benchmark_sector_coverage"], errors="coerce").mean()
                    * 100.0
                ),
                "attribution_reconciliation_error": reconciliation_error * 100.0,
            }
        )
    return pd.DataFrame(rows)


def _display_table(monthly: pd.DataFrame, run: RunInfo, benchmark_code: str) -> None:
    print("\n" + "=" * 118)
    print(
        f"アクティブリターン要因分解 | run_id={run.run_id} | "
        f"strategy={run.strategy_name} | benchmark={benchmark_code}"
    )
    print("単位: return/effect/coverage=%、beta=無次元。cash dragは正値が機会損失。")
    print("=" * 118)
    display_columns = [
        "month",
        "strategy_return",
        "benchmark_return",
        "active_return",
        "strategy_cash_drag",
        "strategy_portfolio_beta",
        "strategy_sector_allocation",
        "strategy_within_sector",
        "strategy_residual",
    ]
    table = monthly[display_columns].copy()
    numeric_columns = [column for column in display_columns if column != "month"]
    table[numeric_columns] = table[numeric_columns].round(4)
    print(table.to_string(index=False, na_rep="N/A"))

    max_error = monthly["attribution_reconciliation_error"].abs().max()
    print(f"\n最大reconciliation error: {max_error:.10f} percentage points")
    low_portfolio_coverage = monthly["portfolio_sector_coverage"].min()
    low_benchmark_coverage = monthly["benchmark_sector_coverage"].min()
    print(
        "平均業種データcoverageの月次最低値: "
        f"portfolio={low_portfolio_coverage:.2f}% / universe={low_benchmark_coverage:.2f}%"
    )


def _export_csv(
    monthly: pd.DataFrame,
    strategy: str,
    from_date: str,
    to_date: str,
    output_dir: Path,
    output_path: str | None,
) -> Path:
    if output_path:
        destination = _resolve_path(output_path, base=Path.cwd())
    else:
        destination = output_dir / (
            f"active_return_attribution_{strategy}_{from_date}_{to_date}.csv"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(destination, index=False, encoding="utf-8-sig", float_format="%.10f")
    return destination


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="2559対比のアクティブリターンを月別に要因分解する"
    )
    parser.add_argument("--from", dest="from_date", required=True, type=_parse_iso_date)
    parser.add_argument("--to", dest="to_date", required=True, type=_parse_iso_date)
    parser.add_argument("--strategy", default="momentum")
    parser.add_argument("--benchmark", default=DEFAULT_BENCHMARK)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--db", help="configのdatabase.pathを上書きするSQLite DB")
    parser.add_argument("--run-id", type=int, help="既存backtest runを明示指定")
    parser.add_argument(
        "--auto-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="完全一致runがない場合にBacktestRunnerを実行する（既定: 有効）",
    )
    parser.add_argument(
        "--beta-window",
        type=int,
        default=20,
        help="ローリングbetaの営業日window（既定: 20）",
    )
    parser.add_argument("--csv", action="store_true", help="月次CSVを出力")
    parser.add_argument("--output", help="CSV出力先を明示指定")
    parser.add_argument("--output-dir", default="reports")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    from_date = args.from_date.isoformat()
    to_date = args.to_date.isoformat()
    if args.from_date > args.to_date:
        parser.error("--fromは--to以前の日付にしてください")
    if args.beta_window < 5:
        parser.error("--beta-windowは5以上にしてください")

    config_path = _resolve_path(args.config, base=Path.cwd())
    if not config_path.exists() and not Path(args.config).is_absolute():
        repository_candidate = _resolve_path(args.config, base=REPO_ROOT)
        if repository_candidate.exists():
            config_path = repository_candidate
    config = _load_yaml_config(config_path)
    db_path = _database_path(config, config_path, args.db)
    idle_policy = _idle_cash_policy(config)

    try:
        run = _ensure_run(
            db_path=db_path,
            config_path=config_path,
            strategy=args.strategy,
            from_date=from_date,
            to_date=to_date,
            run_id=args.run_id,
            auto_run=bool(args.auto_run),
            explicit_db=args.db is not None,
        )

        with _read_only_connection(db_path) as conn:
            _validate_schema(conn)
            equity = _load_equity_curve(conn, run, to_date)
            fills = _load_fills(conn, run.run_id, to_date)
            symbols = _load_symbols(conn)

            requested_start = pd.Timestamp(from_date)
            curve_start = equity.index.min()
            if requested_start < curve_start:
                raise AttributionError(
                    f"equity curve開始日より前は分析できません: curve={curve_start.date()}"
                )

            held_codes = sorted(set(fills["code"].astype(str))) if not fills.empty else []
            universe_codes = symbols.loc[
                (symbols["role"] == "trade_candidate")
                & (pd.to_numeric(symbols["tradable"], errors="coerce").fillna(0).astype(int) == 1),
                "code",
            ].astype(str).tolist()
            price_codes = set(universe_codes) | set(held_codes) | {args.benchmark}
            if idle_policy.enabled and idle_policy.benchmark_code:
                price_codes.add(idle_policy.benchmark_code)

            # 最初の分析日の日次リターン用に十分な直前価格を取得する。
            lookback_start = (min(curve_start.date(), args.from_date) - timedelta(days=60)).isoformat()
            price_matrix = _load_close_prices(
                conn,
                sorted(price_codes),
                lookback_start,
                to_date,
            )

        daily = _build_daily_attribution(
            equity=equity,
            fills=fills,
            symbols=symbols,
            price_matrix=price_matrix,
            run=run,
            from_date=from_date,
            to_date=to_date,
            benchmark_code=args.benchmark,
            idle_policy=idle_policy,
            beta_window=args.beta_window,
        )
        monthly = _aggregate_monthly(daily)
        _display_table(monthly, run, args.benchmark)

        if args.csv:
            output_dir = _resolve_path(args.output_dir, base=REPO_ROOT)
            csv_path = _export_csv(
                monthly=monthly,
                strategy=args.strategy,
                from_date=from_date,
                to_date=to_date,
                output_dir=output_dir,
                output_path=args.output,
            )
            print(f"[OK] CSV: {csv_path}")
        return 0
    except (AttributionError, sqlite3.Error, OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
