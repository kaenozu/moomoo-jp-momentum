"""
Metrics and verdict logic.

The verdict follows the instructions' decision criteria: REJECT /
RESEARCH_CONTINUE / PAPER_CANDIDATE. REAL_CANDIDATE is never issued.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .backtest import BacktestResult
from .benchmark import BenchmarkResult
from .config import GridConfig


@dataclass
class Verdict:
    label: str  # REJECT | RESEARCH_CONTINUE | PAPER_CANDIDATE
    reasons: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


def evaluate_verdict(
    grid: GridConfig,
    result: BacktestResult,
    bh: BenchmarkResult | None,
    cost_multiple: float = 1.0,
) -> Verdict:
    """Evaluate the backtest result against the decision criteria."""
    reasons: list[str] = []
    evidence = {
        "oos_net_cagr_pct": result.cagr_pct,
        "oos_total_return_jpy_pct": result.total_return_pct_jpy,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "cost_multiple": cost_multiple,
    }

    # REJECT triggers.
    if result.total_return_pct_jpy <= 0:
        reasons.append("OOS JPY net return is not positive")
    if bh is not None and result.total_return_pct_jpy < bh.total_return_pct_jpy:
        reasons.append("return below Buy & Hold despite at least comparable drawdown")
    if result.max_drawdown_pct > grid.risk.strategy_drawdown_limit_pct:
        reasons.append(
            f"max drawdown {result.max_drawdown_pct:.1f}% exceeds "
            f"strategy limit {grid.risk.strategy_drawdown_limit_pct:.1f}%"
        )
    if result.win_rate > 0 and result.profit_factor < 1.0:
        reasons.append(f"profit factor below 1.0 ({result.profit_factor:.2f})")

    if reasons:
        return Verdict("REJECT", reasons, evidence)

    # PAPER_CANDIDATE requires more evidence than a positive OOS.
    if result.total_return_pct_jpy > 0:
        if (
            result.sharpe >= 0.5
            and result.max_drawdown_pct <= grid.risk.portfolio_drawdown_limit_pct
            and (bh is None or result.total_return_pct_jpy >= bh.total_return_pct_jpy)
        ):
            reasons.append("positive OOS return with acceptable risk profile")
            return Verdict("PAPER_CANDIDATE", reasons, evidence)
        reasons.append("positive OOS return but limited risk-adjusted evidence")
        return Verdict("RESEARCH_CONTINUE", reasons, evidence)

    return Verdict("REJECT", reasons or ["no positive evidence"], evidence)
