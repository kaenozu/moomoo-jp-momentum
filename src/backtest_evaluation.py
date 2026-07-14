"""Pure evaluation helpers for historical and walk-forward backtests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class WalkForwardFold:
    """One rolling-origin train/test split."""

    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_days: int
    test_days: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParameterSet:
    """Parameters selected only from a fold's training interval."""

    score_threshold: float
    max_positions: int
    cash_reserve_ratio: float
    stop_loss_pct: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "score_threshold": self.score_threshold,
            "max_positions": self.max_positions,
            "cash_reserve_ratio": self.cash_reserve_ratio,
            "stop_loss_pct": self.stop_loss_pct,
        }


def cash_matched_benchmark_return(
    full_investment_return_pct: float | None,
    active_cash: float,
    account_initial_cash: float,
) -> float | None:
    """Return benchmark performance with the strategy's cash reserve matched."""

    if full_investment_return_pct is None:
        return None
    if account_initial_cash <= 0:
        raise ValueError("account_initial_cash must be positive")
    if active_cash < 0 or active_cash > account_initial_cash:
        raise ValueError("active_cash must be within the account balance")
    return full_investment_return_pct * active_cash / account_initial_cash


def build_walk_forward_folds(
    trading_days: Sequence[str],
    *,
    train_days: int,
    test_days: int,
    step_days: int,
) -> list[WalkForwardFold]:
    """Build complete rolling-origin folds without train/test overlap."""

    if train_days <= 0 or test_days <= 0 or step_days <= 0:
        raise ValueError("train_days, test_days and step_days must be positive")
    if step_days < test_days:
        raise ValueError(
            "step_days must be greater than or equal to test_days "
            "so out-of-sample folds do not overlap"
        )
    normalized = list(trading_days)
    if normalized != sorted(normalized):
        raise ValueError("trading_days must be sorted")
    if len(set(normalized)) != len(normalized):
        raise ValueError("trading_days must not contain duplicates")

    folds: list[WalkForwardFold] = []
    train_start_index = 0
    fold_index = 1
    while True:
        train_end_index = train_start_index + train_days - 1
        test_start_index = train_end_index + 1
        test_end_index = test_start_index + test_days - 1
        if test_end_index >= len(normalized):
            break
        folds.append(
            WalkForwardFold(
                index=fold_index,
                train_start=normalized[train_start_index],
                train_end=normalized[train_end_index],
                test_start=normalized[test_start_index],
                test_end=normalized[test_end_index],
                train_days=train_days,
                test_days=test_days,
            )
        )
        train_start_index += step_days
        fold_index += 1
    return folds


def realized_trade_pnls(
    rows: Iterable[Mapping[str, Any]],
) -> list[float]:
    """Match BUY and SELL fills FIFO and return gross realized P/L per close."""

    open_entries: dict[str, list[tuple[float, int]]] = {}
    realized: list[float] = []
    for row in rows:
        code = str(row["code"])
        side = str(row["side"]).upper()
        price = float(row["price"])
        quantity_remaining = int(row["quantity"])
        if quantity_remaining <= 0:
            continue
        if side == "BUY":
            open_entries.setdefault(code, []).append((price, quantity_remaining))
            continue
        if side != "SELL":
            continue

        entries = open_entries.get(code, [])
        trade_pnl = 0.0
        matched = 0
        while quantity_remaining > 0 and entries:
            entry_price, entry_quantity = entries.pop(0)
            matched_quantity = min(quantity_remaining, entry_quantity)
            trade_pnl += (price - entry_price) * matched_quantity
            matched += matched_quantity
            quantity_remaining -= matched_quantity
            if entry_quantity > matched_quantity:
                entries.insert(0, (entry_price, entry_quantity - matched_quantity))
        if matched > 0:
            realized.append(trade_pnl)
    return realized


def trade_distribution(realized_pnls: Sequence[float]) -> dict[str, Any]:
    """Calculate concentration and payoff diagnostics from realized trades."""

    pnls = [float(value) for value in realized_pnls]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    top_five_profit = sum(sorted(wins, reverse=True)[:5])
    top_five_share = (
        top_five_profit / gross_profit * 100.0 if gross_profit > 0 else None
    )
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    profit_factor_unbounded = gross_profit > 0 and gross_loss == 0
    return {
        "closed_trade_count": len(pnls),
        "winning_trade_count": len(wins),
        "losing_trade_count": len(losses),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_realized_profit": sum(pnls),
        "average_trade": sum(pnls) / len(pnls) if pnls else None,
        "median_trade": median(pnls) if pnls else None,
        "average_win": sum(wins) / len(wins) if wins else None,
        "average_loss": sum(losses) / len(losses) if losses else None,
        "largest_win": max(wins) if wins else None,
        "largest_loss": min(losses) if losses else None,
        "profit_factor": profit_factor,
        "profit_factor_unbounded": profit_factor_unbounded,
        "top_5_gross_profit_share_pct": top_five_share,
    }


def training_selection_key(
    report: Mapping[str, Any],
    *,
    min_closed_trades: int,
) -> tuple[int, float, float, float, float]:
    """Return a deterministic key for training-only parameter selection."""

    performance = report["performance"]
    benchmarks = report["benchmarks"]
    closed_trades = int(performance["closed_trade_count"])
    total_return = float(performance["total_return_pct"])
    excess_raw = benchmarks.get("excess_vs_JP.1306_cash_matched_pct")
    excess = float(excess_raw) if excess_raw is not None else -1.0e100
    pf_raw = performance.get("profit_factor")
    if bool(performance.get("profit_factor_unbounded", False)):
        profit_factor = 1.0e100
    else:
        profit_factor = float(pf_raw) if pf_raw is not None else -1.0e100
    drawdown = float(performance["max_drawdown_pct"])
    eligible = int(
        closed_trades >= min_closed_trades
        and total_return > 0
        and excess > 0
    )
    return (eligible, excess, profit_factor, -drawdown, total_return)


def summarize_walk_forward(
    fold_reports: Sequence[Mapping[str, Any]],
    stress_reports: Sequence[Mapping[str, Any]],
    realized_pnls: Sequence[float],
    *,
    minimum_folds: int = 2,
    minimum_closed_trades: int = 30,
    minimum_positive_fold_ratio: float = 2.0 / 3.0,
    minimum_profit_factor: float = 1.2,
    maximum_drawdown_pct: float = 20.0,
    maximum_top_5_profit_share_pct: float = 50.0,
) -> dict[str, Any]:
    """Aggregate out-of-sample fold evidence and assign a fail-closed status."""

    if len(fold_reports) != len(stress_reports):
        raise ValueError("base and stress fold counts must match")
    if not 0 < minimum_positive_fold_ratio <= 1:
        raise ValueError("minimum_positive_fold_ratio must be within (0, 1]")

    def _excess(report: Mapping[str, Any]) -> float | None:
        value = report["benchmarks"].get(
            "excess_vs_JP.1306_cash_matched_pct"
        )
        return float(value) if value is not None else None

    base_excesses = [_excess(report) for report in fold_reports]
    stress_excesses = [_excess(report) for report in stress_reports]
    known_base = [value for value in base_excesses if value is not None]
    known_stress = [value for value in stress_excesses if value is not None]
    base_positive = sum(value > 0 for value in known_base)
    stress_positive = sum(value > 0 for value in known_stress)
    base_ratio = base_positive / len(fold_reports) if fold_reports else 0.0
    stress_ratio = stress_positive / len(stress_reports) if stress_reports else 0.0
    median_excess = median(known_base) if known_base else None
    median_stress_excess = median(known_stress) if known_stress else None
    worst_drawdown = max(
        (
            float(report["performance"]["max_drawdown_pct"])
            for report in fold_reports
        ),
        default=None,
    )
    distribution = trade_distribution(realized_pnls)
    total_trades = int(distribution["closed_trade_count"])
    pf_raw = distribution["profit_factor"]
    aggregate_pf = float(pf_raw) if pf_raw is not None else None
    aggregate_pf_unbounded = bool(distribution["profit_factor_unbounded"])
    top_share_raw = distribution["top_5_gross_profit_share_pct"]
    top_share = float(top_share_raw) if top_share_raw is not None else None

    if len(fold_reports) < minimum_folds or total_trades < minimum_closed_trades:
        status = "INSUFFICIENT_WALK_FORWARD_EVIDENCE"
    elif (
        median_excess is None
        or median_excess <= 0
        or base_ratio < minimum_positive_fold_ratio
    ):
        status = "NO_STABLE_EDGE"
    elif (
        median_stress_excess is None
        or median_stress_excess <= 0
        or stress_ratio < minimum_positive_fold_ratio
    ):
        status = "FRAGILE_TO_SLIPPAGE"
    elif (
        (aggregate_pf is None and not aggregate_pf_unbounded)
        or (
            aggregate_pf is not None
            and aggregate_pf < minimum_profit_factor
        )
        or worst_drawdown is None
        or worst_drawdown > maximum_drawdown_pct
        or top_share is None
        or top_share >= maximum_top_5_profit_share_pct
    ):
        status = "WEAK_EDGE"
    else:
        status = "PROMISING_WALK_FORWARD_ONLY"

    return {
        "status": status,
        "fold_count": len(fold_reports),
        "closed_trade_count": total_trades,
        "median_cash_matched_excess_vs_JP.1306_pct": median_excess,
        "positive_excess_fold_count": base_positive,
        "positive_excess_fold_ratio": base_ratio,
        "stress_median_cash_matched_excess_vs_JP.1306_pct": (
            median_stress_excess
        ),
        "stress_positive_excess_fold_count": stress_positive,
        "stress_positive_excess_fold_ratio": stress_ratio,
        "worst_fold_max_drawdown_pct": worst_drawdown,
        "aggregate_trade_distribution": distribution,
        "requirements": {
            "minimum_folds": minimum_folds,
            "minimum_closed_trades": minimum_closed_trades,
            "minimum_positive_fold_ratio": minimum_positive_fold_ratio,
            "minimum_profit_factor": minimum_profit_factor,
            "maximum_drawdown_pct": maximum_drawdown_pct,
            "maximum_top_5_gross_profit_share_pct": (
                maximum_top_5_profit_share_pct
            ),
        },
    }
