"""
パフォーマンス評価モジュール

ファイルパス: src/performance.py
何をするか: 手動売買とベンチマークの比較を計算する
なぜ存在するか: 戦術の有術の有効性を検証するため
関連ファイル: trade_log.py, benchmark.py, config.py

計算する指標:
- 実現損益
- 未実現損益
- 銘柄別損益
- 保有中一覧
- 勝率
- 平均利益
- 平均損失
- 最大損失
- 累計リターン
- ベンチマーク同日購入比較
- 超過リターン
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

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
        """
        Args:
            config: 設定オブジェクト
        """
        self.config = config
        self.db_path = Path(config.database_path)

        # ベンチマークコード
        benchmark_config = config.get("benchmark", {})
        primary = benchmark_config.get("primary", {})
        self.primary_benchmark = primary.get("code", "JP.2559")

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得する"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_positions(self) -> list[Position]:
        """
        現在の保有ポジションを計算する

        Returns:
            list[Position]: 保有ポジションのリスト
        """
        with self._get_connection() as conn:
            # 売買記録を取得
            cursor = conn.execute(
                "SELECT * FROM trades_manual ORDER BY executed_at"
            )
            trades = cursor.fetchall()

        # ポジションを計算
        positions_dict: dict[str, dict] = {}

        for trade in trades:
            code = trade["code"]
            side = trade["side"]
            quantity = trade["quantity"]
            price = trade["price"]

            if code not in positions_dict:
                # 銘柄名を取得
                cursor = conn.execute(
                    "SELECT name FROM symbols WHERE code = ?",
                    (code,),
                )
                row = cursor.fetchone()
                name = row["name"] if row else None

                positions_dict[code] = {
                    "code": code,
                    "name": name,
                    "quantity": 0,
                    "total_cost": 0,
                }

            pos = positions_dict[code]

            if side == "BUY":
                pos["quantity"] += quantity
                pos["total_cost"] += price * quantity
            elif side == "SELL":
                pos["quantity"] -= quantity
                pos["total_cost"] -= price * quantity

        # 現在価格を取得
        positions = []
        for code, pos in positions_dict.items():
            if pos["quantity"] <= 0:
                continue

            avg_price = pos["total_cost"] / pos["quantity"]

            # 現在価格を取得（最新の終値）
            current_price = self._get_latest_price(code)

            # 評価損益
            unrealized_pnl = None
            unrealized_return = None
            if current_price:
                unrealized_pnl = (current_price - avg_price) * pos["quantity"]
                unrealized_return = (current_price - avg_price) / avg_price * 100

            positions.append(Position(
                code=code,
                name=pos["name"],
                quantity=pos["quantity"],
                avg_price=avg_price,
                current_price=current_price,
                unrealized_pnl=unrealized_pnl,
                unrealized_return=unrealized_return,
            ))

        return positions

    def _get_latest_price(self, code: str) -> Optional[float]:
        """最新の終値を取得する"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT close FROM daily_bars
                WHERE code = ?
                ORDER BY date DESC LIMIT 1
                """,
                (code,),
            )
            row = cursor.fetchone()
            return row["close"] if row else None

    def get_trade_history(self) -> list[TradePerformance]:
        """
        売買履歴のパフォーマンスを計算する

        Returns:
            list[TradePerformance]: 売買パフォーマンスのリスト
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM trades_manual ORDER BY executed_at"
            )
            trades = cursor.fetchall()

        # 銘柄ごとにグループ化
        trades_by_code: dict[str, list] = {}
        for trade in trades:
            code = trade["code"]
            if code not in trades_by_code:
                trades_by_code[code] = []
            trades_by_code[code].append(trade)

        # 売買パフォーマンスを計算
        performances = []

        for code, code_trades in trades_by_code.items():
            # 銘柄名を取得
            cursor = conn.execute(
                "SELECT name FROM symbols WHERE code = ?",
                (code,),
            )
            row = cursor.fetchone()
            name = row["name"] if row else None

            # 買いと売りをマッチング
            buy_queue = []
            for trade in code_trades:
                if trade["side"] == "BUY":
                    buy_queue.append({
                        "quantity": trade["quantity"],
                        "price": trade["price"],
                        "date": trade["executed_at"],
                    })
                elif trade["side"] == "SELL" and buy_queue:
                    buy = buy_queue.pop(0)
                    pnl = (trade["price"] - buy["price"]) * min(
                        trade["quantity"], buy["quantity"]
                    )
                    return_pct = (trade["price"] - buy["price"]) / buy["price"] * 100

                    # 保有日数
                    try:
                        buy_date = datetime.strptime(buy["date"][:10], "%Y-%m-%d")
                        sell_date = datetime.strptime(trade["executed_at"][:10], "%Y-%m-%d")
                        holding_days = (sell_date - buy_date).days
                    except ValueError:
                        holding_days = None

                    performances.append(TradePerformance(
                        code=code,
                        name=name,
                        side="SELL",
                        quantity=trade["quantity"],
                        entry_price=buy["price"],
                        exit_price=trade["price"],
                        pnl=pnl,
                        return_pct=return_pct,
                        holding_days=holding_days,
                    ))

        return performances

    def get_summary(
        self,
        benchmark_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> PortfolioSummary:
        """
        ポートフォリオサマリーを計算する

        Args:
            benchmark_code: 比較ベンチマークコード
            start_date: 開始日
            end_date: 終了日

        Returns:
            PortfolioSummary: サマリー
        """
        if benchmark_code is None:
            benchmark_code = self.primary_benchmark

        # 保有ポジション
        positions = self.get_positions()

        # 売買履歴
        history = self.get_trade_history()

        # 投資総額
        total_invested = sum(
            p.avg_price * p.quantity for p in positions
        )

        # 評価損益
        unrealized_pnl = sum(
            p.unrealized_pnl for p in positions if p.unrealized_pnl
        )

        # 実現損益
        realized_pnl = sum(h.pnl for h in history if h.pnl)

        # 勝敗
        wins = [h for h in history if h.pnl and h.pnl > 0]
        losses = [h for h in history if h.pnl and h.pnl < 0]

        win_rate = None
        avg_win = None
        avg_loss = None
        max_loss = None

        if wins or losses:
            total_trades = len(wins) + len(losses)
            win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0

        if wins:
            avg_win = sum(h.pnl for h in wins) / len(wins)

        if losses:
            avg_loss = sum(h.pnl for h in losses) / len(losses)
            max_loss = min(h.pnl for h in losses)

        # ベンチマーク比較
        benchmark_return = None
        excess_return = None

        if start_date and end_date:
            from .benchmark import BenchmarkManager
            benchmark_mgr = BenchmarkManager(self.config)
            benchmark_return = benchmark_mgr.get_benchmark_return(
                benchmark_code, start_date, end_date
            )

            if benchmark_return is not None and avg_win:
                # 簡易的な超過リターン
                portfolio_return = (realized_pnl + unrealized_pnl) / total_invested * 100 if total_invested > 0 else 0
                excess_return = portfolio_return - benchmark_return

        return PortfolioSummary(
            total_invested=total_invested,
            current_value=total_invested + unrealized_pnl,
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
        """
        シグナルの事後検証を行う

        Args:
            signal_id: シグナルID
            code: 銘柄コード
            signal_date: シグナル発生日
            signal_price: シグナル価格
            horizon_days: 検証期間（営業日）
            benchmark_code: ベンチマークコード

        Returns:
            dict: 検証結果
        """
        if benchmark_code is None:
            benchmark_code = self.primary_benchmark

        with self._get_connection() as conn:
            # シグナル後の価格を取得
            cursor = conn.execute(
                """
                SELECT date, close FROM daily_bars
                WHERE code = ? AND date > ?
                ORDER BY date
                LIMIT ?
                """,
                (code, signal_date, horizon_days),
            )
            future_bars = cursor.fetchall()

        if not future_bars:
            return None

        # 将来の価格
        future_price = future_bars[-1]["close"]
        future_date = future_bars[-1]["date"]

        # ストックリターン
        stock_return = (future_price - signal_price) / signal_price * 100

        # ベンチマークリターン
        from .benchmark import BenchmarkManager
        benchmark_mgr = BenchmarkManager(self.config)
        benchmark_return = benchmark_mgr.get_benchmark_return(
            benchmark_code, signal_date, future_date
        )

        # 超過リターン
        excess_return = None
        if benchmark_return is not None:
            excess_return = stock_return - benchmark_return

        # 最大下落率・最大上昇率
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
        horizons: list[int] = None,
    ) -> list[dict]:
        """
        全シグナルの事後検証を行う

        Args:
            horizons: 検証期間のリスト（デフォルト: [5, 20, 60]）

        Returns:
            list[dict]: 検証結果のリスト
        """
        if horizons is None:
            horizons = [5, 20, 60]

        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, code, date, price_at_signal FROM signals"
            )
            signals = cursor.fetchall()

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

                    # signal_backtestsテーブルに保存
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
                logger.error(f"検証結果保存エラー: {e}")
