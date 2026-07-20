"""
保有銘柄ベータ下限制御。

各保有銘柄と JP.2559 の日次リターンからローリングβを計算し、
保有時価で加重した holdings_implied_beta を投資比率へ変換する。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

import pandas as pd

from .config import Config


BETA_BENCHMARK_CODE = "JP.2559"


@dataclass(frozen=True)
class PortfolioBetaSnapshot:
    """指定日時点の保有銘柄βとβ下限による投資比率。"""

    as_of_date: str
    holdings_implied_beta: Optional[float]
    target_investment_ratio: float
    covered_weight: float
    symbol_betas: dict[str, float]
    missing_codes: tuple[str, ...]


class HoldingsBetaFloor:
    """保有銘柄βから翌営業日の目標投資比率を算出する。"""

    def __init__(
        self,
        config: Config,
        db_path: str | Path | None = None,
        *,
        enabled_override: Optional[bool] = None,
    ) -> None:
        self.db_path = Path(db_path or config.database_path)
        self.min_portfolio_beta = float(
            config.get("risk_controls.min_portfolio_beta", 0.0) or 0.0
        )
        self.lookback = int(
            config.get("risk_controls.min_portfolio_beta_holdings_lookback", 60)
        )
        if self.lookback < 2:
            raise ValueError(
                "risk_controls.min_portfolio_beta_holdings_lookback は2以上が必要です"
            )

        configured_enabled = self.min_portfolio_beta > 0.0
        self.enabled = (
            configured_enabled
            if enabled_override is None
            else bool(enabled_override) and configured_enabled
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _close_series(
        self,
        conn: sqlite3.Connection,
        code: str,
        as_of_date: str,
    ) -> pd.Series:
        # 銘柄側の欠損日があっても60リターンを揃えられるよう余裕を持って取得する。
        limit = self.lookback * 2 + 10
        rows = conn.execute(
            """
            SELECT date, close
            FROM daily_bars
            WHERE code = ? AND date <= ? AND close IS NOT NULL
            ORDER BY date DESC
            LIMIT ?
            """,
            (code, as_of_date, limit),
        ).fetchall()
        if not rows:
            return pd.Series(dtype="float64", name=code)

        frame = pd.DataFrame(rows, columns=["date", "close"])
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["close"]).sort_values("date")
        return frame.set_index("date")["close"].rename(code)

    def rolling_beta(
        self,
        conn: sqlite3.Connection,
        code: str,
        as_of_date: str,
    ) -> Optional[float]:
        """指定銘柄の直近lookback日次リターンβを返す。"""
        asset = self._close_series(conn, code, as_of_date)
        benchmark = self._close_series(conn, BETA_BENCHMARK_CODE, as_of_date)
        if asset.empty or benchmark.empty:
            return None

        prices = pd.concat([asset, benchmark], axis=1, join="inner").dropna()
        if len(prices) < self.lookback + 1:
            return None

        returns = prices.pct_change(fill_method=None).dropna().tail(self.lookback)
        if len(returns) < self.lookback:
            return None

        benchmark_returns = returns[BETA_BENCHMARK_CODE]
        variance = float(benchmark_returns.var(ddof=1))
        if not pd.notna(variance) or variance <= 0.0:
            return None

        covariance = float(returns[code].cov(benchmark_returns))
        beta = covariance / variance
        return float(beta) if pd.notna(beta) else None

    def target_ratio(self, holdings_implied_beta: Optional[float]) -> float:
        """β下限ルールに基づく0〜1の目標投資比率。"""
        if not self.enabled or holdings_implied_beta is None:
            return 1.0
        if holdings_implied_beta >= self.min_portfolio_beta:
            return 1.0
        return max(
            0.0,
            min(1.0, holdings_implied_beta / self.min_portfolio_beta),
        )

    def evaluate(
        self,
        position_values: Mapping[str, float],
        as_of_date: str,
    ) -> PortfolioBetaSnapshot:
        """保有時価で加重したβと翌営業日の目標投資比率を返す。

        60日βを計算できない保有銘柄が1つでもある場合は、部分データによる
        過小評価を避けるためフェイルオープン（投資比率1.0）とする。
        """
        positive_values = {
            code: float(value)
            for code, value in position_values.items()
            if float(value) > 0.0
        }
        total_value = sum(positive_values.values())
        if total_value <= 0.0:
            return PortfolioBetaSnapshot(
                as_of_date=as_of_date,
                holdings_implied_beta=None,
                target_investment_ratio=1.0,
                covered_weight=1.0,
                symbol_betas={},
                missing_codes=(),
            )

        symbol_betas: dict[str, float] = {}
        missing_codes: list[str] = []
        covered_value = 0.0
        weighted_beta = 0.0

        with self._connect() as conn:
            for code, market_value in positive_values.items():
                beta = self.rolling_beta(conn, code, as_of_date)
                if beta is None:
                    missing_codes.append(code)
                    continue
                symbol_betas[code] = beta
                covered_value += market_value
                weighted_beta += beta * market_value

        covered_weight = covered_value / total_value
        implied_beta: Optional[float]
        if missing_codes or covered_value <= 0.0:
            implied_beta = None
        else:
            implied_beta = weighted_beta / covered_value

        return PortfolioBetaSnapshot(
            as_of_date=as_of_date,
            holdings_implied_beta=implied_beta,
            target_investment_ratio=self.target_ratio(implied_beta),
            covered_weight=covered_weight,
            symbol_betas=symbol_betas,
            missing_codes=tuple(sorted(missing_codes)),
        )
