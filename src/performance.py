"""
パフォーマンス評価モジュール

ファイルパス: src/performance.py
何をするか: 手動売買とベンチマークの比較を計算する
なぜ存在するか: 戦術の有効性を検証するため
関連ファイル: trade_log.py, benchmark.py, config.py
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """保有ポジション"""
    code: str
    name: Optional[str]
    quantity: int
    avg_price: float
    current_price: Optional[float]
    unrealized_pnl: Optional[float]
    unrealized_return: Optional[float]


@dataclass
class TradePerformance:
    """売買パフォーマンス"""
    code: str
    name: Optional[str]
    side: str
    quantity: int
    entry_price: float
    exit_price: Optional[float]
    pnl: Optional[float]
    return_pct: Optional[float]
    holding_days: Optional[int]


@dataclass
class PortfolioSummary:
    """ポートフォリオサマリー"""
    total_invested: float
    current_value: Optional[float]
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    win_count: int
    loss_count: int
    win_rate: Optional[float]
    avg_win: Optional[float]
    avg_loss: Optional[float]
    max_loss: Optional[float]
    benchmark_return: Optional[float]
    excess_return: Optional[float]


class PerformanceEvaluator:
    """パフォーマンス評価クラス"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)

        benchmark_config = config.get("benchmark", {})
        primary = benchmark_config.get("primary", {})
        self.primary_benchmark = primary.get("code", "JP.2559")

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得する"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_symbol_names(self) -> dict[str, str]:
        """銘柄名をまとめて取得する"""
        if not self.db_path.exists():
            return {}

        with self._get_connection() as conn:
            rows = conn.execute("SELECT code, name FROM symbols").fetchall()

        return {row["code"]: row["name"] for row in rows}

    def _get_trades(self) -> list[sqlite3.Row]:
        """手動売買記録を取得する"""
        if not self.db_path.exists():
            return []

        with self._get_connection() as conn:
            return conn.execute("SELECT * FROM trades_manual ORDER BY executed_at, id").fetchall()

    @staticmethod
    def _holding_days(entry_at: str, exit_at: str) -> Optional[int]:
        try:
            buy_date = datetime.strptime(entry_at[:10], "%Y-%m-%d")
            sell_date = datetime.strptime(exit_at[:10], "%Y-%m-%d")
            return (sell_date - buy_date).days
        except ValueError:
            return None

    def _build_fifo_state(self) -> tuple[dict[str, list[dict]], list[TradePerformance]]:
        """
        FIFOで保有ロットと実現損益履歴を構築する。

        SELL数量がBUY残数量を超える場合、超過分は警告して無視する。
        """
        trades = self._get_trades()
        symbol_names = self._get_symbol_names()

        lots_by_code: dict[str, list[dict]] = {}
        performances: list[TradePerformance] = []

        for trade in trades:
            code = trade["code"]
            side = str(trade["side"]).upper()
            quantity = int(trade["quantity"])
            price = float(trade["price"])
            executed_at = trade["executed_at"]

            lots = lots_by_code.setdefault(code, [])

            if side == "BUY":
                lots.append({
                    "quantity": quantity,
                    "price": price,
                    "executed_at": executed_at,
                })
                continue

            if side != "SELL":
                logger.warning("未知の売買方向を無視します: %s", side)
                continue

            remaining_sell = quantity
            while remaining_sell > 0 and lots:
                lot = lots[0]
                matched_qty = min(remaining_sell, int(lot["quantity"]))

                entry_price = float(lot["price"])
                pnl = (price - entry_price) * matched_qty
                return_pct = (price - entry_price) / entry_price * 100 if entry_price > 0 else None

                performances.append(TradePerformance(
                    code=code,
                    name=symbol_names.get(code),
                    side="SELL",
                    quantity=matched_qty,
                    entry_price=entry_price,
                    exit_price=price,
                    pnl=pnl,
                    return_pct=return_pct,
                    holding_days=self._holding_days(str(lot["executed_at"]), str(executed_at)),
                ))

                lot["quantity"] = int(lot["quantity"]) - matched_qty
                remaining_sell -= matched_qty

                if lot["quantity"] <= 0:
                    lots.pop(0)

            if remaining_sell > 0:
                logger.warning("SELL数量がBUY残数量を超えています: %s 超過%s株", code, remaining_sell)

        return lots_by_code, performances

    def get_positions(self) -> list[Position]:
        """現在の保有ポジションを計算する"""
        lots_by_code, _ = self._build_fifo_state()
        symbol_names = self._get_symbol_names()

        positions = []
        for code, lots in lots_by_code.items():
            quantity = sum(int(lot["quantity"]) for lot in lots)
            if quantity <= 0:
                continue

            total_cost = sum(int(lot["quantity"]) * float(lot["price"]) for lot in lots)
            avg_price = total_cost / quantity

            current_price = self._get_latest_price(code)
            unrealized_pnl = None
            unrealized_return = None
            if current_price is not None and avg_price > 0:
                unrealized_pnl = (current_price - avg_price) * quantity
                unrealized_return = (current_price - avg_price) / avg_price * 100

            positions.append(Position(
                code=code,
                name=symbol_names.get(code),
                quantity=quantity,
                avg_price=avg_price,
                current_price=current_price,
                unrealized_pnl=unrealized_pnl,
                unrealized_return=unrealized_return,
            ))

        return positions

    def _get_latest_price(self, code: str) -> Optional[float]:
        """最新の終値を取得する"""
        if not self.db_path.exists():
            return None

        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT close FROM daily_bars
                WHERE code = ?
                ORDER BY date DESC LIMIT 1
                """,
                (code,),
            ).fetchone()

        return row["close"] if row else None

    def get_trade_history(self) -> list[TradePerformance]:
        """売買履歴のパフォーマンスを計算する"""
        _, performances = self._build_fifo_state()
        return performances

    def get_summary(
        self,
        benchmark_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> PortfolioSummary:
        """ポートフォリオサマリーを計算する"""
        if benchmark_code is None:
            benchmark_code = self.primary_benchmark

        positions = self.get_positions()
        history = self.get_trade_history()

        total_invested = sum(p.avg_price * p.quantity for p in positions)
        current_value = sum(
            (p.current_price if p.current_price is not None else p.avg_price) * p.quantity
            for p in positions
        )

        unrealized_pnl = sum(p.unrealized_pnl for p in positions if p.unrealized_pnl is not None)
        realized_pnl = sum(h.pnl for h in history if h.pnl is not None)

        wins = [h for h in history if h.pnl is not None and h.pnl > 0]
        losses = [h for h in history if h.pnl is not None and h.pnl < 0]

        total_trades = len(wins) + len(losses)
        win_rate = len(wins) / total_trades * 100 if total_trades > 0 else None
        avg_win = sum(h.pnl for h in wins) / len(wins) if wins else None
        avg_loss = sum(h.pnl for h in losses) / len(losses) if losses else None
        max_loss = min((h.pnl for h in losses), default=None)

        benchmark_return = None
        excess_return = None

        if start_date and end_date:
            from .benchmark import BenchmarkManager
            benchmark_mgr = BenchmarkManager(self.config)
            benchmark_return = benchmark_mgr.get_benchmark_return(
                benchmark_code, start_date, end_date
            )

            if benchmark_return is not None and total_invested > 0:
                portfolio_return = (realized_pnl + unrealized_pnl) / total_invested * 100
                excess_return = portfolio_return - benchmark_return

        return PortfolioSummary(
            total_invested=total_invested,
            current_value=current_value,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_pnl=realized_pnl + unrealized_pnl,
            win_count=len(wins),
            loss_count=len(losses),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            max_loss=max_loss,
            benchmark_return=benchmark_return,
            excess_return=excess_return,
        )

    def backtest_signal(
        self,
        signal_id: int,
        code: str,
        signal_date: str,
        signal_price: float,
        horizon_days: int,
        benchmark_code: Optional[str] = None,
    ) -> Optional[dict]:
        """シグナルの事後検証を行う"""
        if benchmark_code is None:
            benchmark_code = self.primary_benchmark

        with self._get_connection() as conn:
            future_bars = conn.execute(
                """
                SELECT date, close FROM daily_bars
                WHERE code = ? AND date > ?
                ORDER BY date
                LIMIT ?
                """,
                (code, signal_date, horizon_days),
            ).fetchall()

        if not future_bars:
            return None

        future_price = future_bars[-1]["close"]
        future_date = future_bars[-1]["date"]

        if not signal_price:
            return None

        stock_return = (future_price - signal_price) / signal_price * 100

        from .benchmark import BenchmarkManager
        benchmark_mgr = BenchmarkManager(self.config)
        benchmark_return = benchmark_mgr.get_benchmark_return(
            benchmark_code, signal_date, future_date
        )

        excess_return = stock_return - benchmark_return if benchmark_return is not None else None

        prices = [bar["close"] for bar in future_bars]
        max_drawdown = None
        max_runup = None

        if prices:
            min_price = min(prices)
            max_price = max(prices)
            max_drawdown = (min_price - signal_price) / signal_price * 100
            max_runup = (max_price - signal_price) / signal_price * 100

        return {
            "signal_id": signal_id,
            "code": code,
            "signal_date": signal_date,
            "horizon_days": horizon_days,
            "signal_price": signal_price,
            "future_price": future_price,
            "stock_return": stock_return,
            "benchmark_code": benchmark_code,
            "benchmark_return": benchmark_return,
            "excess_return": excess_return,
            "max_drawdown": max_drawdown,
            "max_runup": max_runup,
        }

    def backtest_all_signals(
        self,
        horizons: list[int] | None = None,
    ) -> list[dict]:
        """全シグナルの事後検証を行う"""
        if horizons is None:
            horizons = [5, 20, 60]

        with self._get_connection() as conn:
            signals = conn.execute(
                "SELECT id, code, date, price_at_signal FROM signals"
            ).fetchall()

        results = []

        for signal in signals:
            for horizon in horizons:
                result = self.backtest_signal(
                    signal_id=signal["id"],
                    code=signal["code"],
                    signal_date=signal["date"],
                    signal_price=signal["price_at_signal"],
                    horizon_days=horizon,
                )

                if result:
                    results.append(result)
                    self._save_backtest_result(result)

        return results

    def _save_backtest_result(self, result: dict) -> None:
        """検証結果を保存する"""
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO signal_backtests
                    (signal_id, code, signal_date, horizon_days, signal_price,
                     future_price, stock_return, benchmark_code, benchmark_return,
                     excess_return, max_drawdown, max_runup, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.get("signal_id"),
                        result.get("code"),
                        result.get("signal_date"),
                        result.get("horizon_days"),
                        result.get("signal_price"),
                        result.get("future_price"),
                        result.get("stock_return"),
                        result.get("benchmark_code"),
                        result.get("benchmark_return"),
                        result.get("excess_return"),
                        result.get("max_drawdown"),
                        result.get("max_runup"),
                        now,
                    ),
                )
            except sqlite3.Error as e:
                logger.error("検証結果保存エラー: %s", e)
