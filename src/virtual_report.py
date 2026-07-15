"""
仮想トレードレポートモジュール

ファイルパス: src/virtual_report.py
何をするか: 仮想トレードの成績レポートを計算する
なぜ存在するか: 仮想トレード結果を正しく評価するため
関連ファイル: virtual_trade.py, benchmark.py, config.py
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Config
from .virtual_trade import VirtualTradeManager

logger = logging.getLogger(__name__)


@dataclass
class ClosedTrade:
    """クローズ済みトレード"""
    code: str
    strategy_name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    realized_pl: float
    return_pct: float
    holding_days: int
    exit_reason: str = "unknown"


@dataclass
class ExitReasonStats:
    """exit_reason別統計"""
    exit_reason: str
    count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: Optional[float] = None
    realized_pl: float = 0.0
    avg_pl: Optional[float] = None
    avg_holding_days: Optional[float] = None


@dataclass
class VirtualReport:
    """仮想トレードレポート"""
    # 全体成績
    initial_cash: float = 0.0
    final_cash: float = 0.0
    final_position_value: float = 0.0
    final_total_equity: float = 0.0
    total_return_pct: float = 0.0
    realized_pl: float = 0.0
    unrealized_pl: float = 0.0
    total_pl: float = 0.0

    # トレード件数
    trade_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    open_position_count: int = 0
    pending_order_count: int = 0
    closed_trade_count: int = 0

    # 勝率・損益
    win_count: int = 0
    loss_count: int = 0
    win_rate: Optional[float] = None
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    profit_factor: Optional[float] = None
    payoff_ratio: Optional[float] = None
    max_win: Optional[float] = None
    max_loss: Optional[float] = None

    # ドローダウン
    max_drawdown_pct: Optional[float] = None
    max_drawdown_amount: Optional[float] = None
    peak_equity_date: Optional[str] = None
    trough_equity_date: Optional[str] = None

    # 保有期間
    avg_holding_days: Optional[float] = None
    max_holding_days: Optional[int] = None
    min_holding_days: Optional[int] = None

    # ベンチマーク比較
    benchmark_2559_return: Optional[float] = None
    excess_vs_2559: Optional[float] = None
    benchmark_1306_return: Optional[float] = None
    excess_vs_1306: Optional[float] = None

    # exit_reason別
    exit_reason_stats: list[ExitReasonStats] = field(default_factory=list)
    # クローズ済みトレード
    closed_trades: list[ClosedTrade] = field(default_factory=list)


class VirtualReportGenerator:
    """仮想トレードレポート生成クラス"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)
        self.manager = VirtualTradeManager(config)

    def get_closed_trades(self, strategy_name: str = "default") -> list[ClosedTrade]:
        """BUY/SELLをFIFOで対応付けてクローズ済みトレードを生成"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT code, side, quantity, fill_price as price,
                       filled_at as filled_at, exit_reason
                FROM (
                    SELECT o.code, o.side, f.quantity, f.price as fill_price,
                           f.filled_at, o.exit_reason,
                           ROW_NUMBER() OVER (ORDER BY f.filled_at, f.id) as rn
                    FROM virtual_fills f
                    JOIN virtual_orders o ON f.order_id = o.id
                    WHERE o.strategy_name = ?
                )
                ORDER BY filled_at
                """,
                (strategy_name,),
            )
            rows = cursor.fetchall()

        buys: list[dict] = []
        closed: list[ClosedTrade] = []

        for row in rows:
            if row["side"] == "BUY":
                buys.append({
                    "code": row["code"],
                    "date": row["filled_at"][:10],
                    "price": row["price"],
                    "quantity": row["quantity"],
                })
            elif row["side"] == "SELL" and buys:
                buy = buys.pop(0)
                qty = min(buy["quantity"], row["quantity"])
                realized_pl = (row["price"] - buy["price"]) * qty
                return_pct = (row["price"] - buy["price"]) / buy["price"] * 100
                try:
                    entry = datetime.strptime(buy["date"], "%Y-%m-%d")
                    exit_dt = datetime.strptime(row["filled_at"][:10], "%Y-%m-%d")
                    holding_days = (exit_dt - entry).days
                except ValueError:
                    holding_days = 0

                closed.append(ClosedTrade(
                    code=row["code"],
                    strategy_name=strategy_name,
                    entry_date=buy["date"],
                    exit_date=row["filled_at"][:10],
                    entry_price=buy["price"],
                    exit_price=row["price"],
                    quantity=qty,
                    realized_pl=realized_pl,
                    return_pct=return_pct,
                    holding_days=holding_days,
                    exit_reason=row["exit_reason"] or "unknown",
                ))

                remaining = buy["quantity"] - qty
                if remaining > 0:
                    buys.insert(0, {**buy, "quantity": remaining})

        return closed

    def _calc_max_drawdown(self, equity_curve: list[dict]) -> tuple:
        """最大ドローダウンを計算"""
        if not equity_curve:
            return None, None, None, None

        peak = 0
        peak_date = None
        max_dd = 0
        max_dd_amount = 0
        trough_date = None

        for e in equity_curve:
            eq = e["total_equity"]
            if eq > peak:
                peak = eq
                peak_date = e["date"]
            dd = peak - eq
            dd_pct = dd / peak * 100 if peak > 0 else 0
            if dd_pct > max_dd:
                max_dd = dd_pct
                max_dd_amount = dd
                trough_date = e["date"]

        return max_dd, max_dd_amount, peak_date, trough_date

    def _get_benchmark_return(self, code: str, start_date: str, end_date: str) -> Optional[float]:
        """ベンチマークリターンを取得"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT date, close FROM benchmark_prices
                WHERE benchmark_code = ? AND date >= ? AND date <= ?
                ORDER BY date
                """,
                (code, start_date, end_date),
            )
            rows = cursor.fetchall()

        if len(rows) < 2:
            logger.warning("ベンチマーク %s のデータが不足しています (%s〜%s)", code, start_date, end_date)
            return None

        start_price = rows[0][1]  # close column
        end_price = rows[-1][1]
        if start_price and end_price and start_price > 0:
            return (end_price - start_price) / start_price * 100
        return None

    def _get_equity_curve_sorted(self, strategy_name: str) -> list[dict]:
        """ソート済みエクイティカーブ"""
        curve = self.manager.get_equity_curve(strategy_name, limit=500)
        curve.sort(key=lambda e: e["date"])
        return curve

    def generate(self, strategy_name: str = "default",
                 from_date: Optional[str] = None,
                 to_date: Optional[str] = None) -> VirtualReport:
        """レポートを生成"""
        report = VirtualReport()
        report.closed_trades = self.get_closed_trades(strategy_name)
        perf = self.manager.get_strategy_performance(strategy_name)

        # 全体成績
        report.initial_cash = perf["initial_cash"]
        report.final_cash = perf["cash"]
        report.final_position_value = perf["position_value"]
        report.final_total_equity = perf["total_equity"]
        report.total_return_pct = perf["return_pct"]
        report.realized_pl = perf["realized_pl"]
        report.unrealized_pl = perf["unrealized_pl"]
        report.total_pl = perf["total_pl"]
        report.open_position_count = perf["position_count"]

        # トレード件数
        closed = report.closed_trades
        report.closed_trade_count = len(closed)
        report.win_count = sum(1 for c in closed if c.realized_pl > 0)
        report.loss_count = sum(1 for c in closed if c.realized_pl < 0)
        report.win_rate = report.win_count / report.closed_trade_count * 100 if report.closed_trade_count > 0 else None

        # 平均損益
        wins = [c for c in closed if c.realized_pl > 0]
        losses = [c for c in closed if c.realized_pl < 0]
        report.avg_win = sum(c.realized_pl for c in wins) / len(wins) if wins else None
        report.avg_loss = sum(c.realized_pl for c in losses) / len(losses) if losses else None
        report.max_win = max((c.realized_pl for c in wins), default=None) if wins else None
        report.max_loss = min((c.realized_pl for c in losses), default=None) if losses else None

        total_win = sum(c.realized_pl for c in wins)
        total_loss = abs(sum(c.realized_pl for c in losses))
        report.profit_factor = total_win / total_loss if total_loss > 0 else (None if total_loss == 0 else float('inf'))

        if report.avg_win is not None and report.avg_loss is not None and report.avg_loss != 0:
            report.payoff_ratio = abs(report.avg_win / report.avg_loss)

        # 保有期間
        holding_days_list = [c.holding_days for c in closed if c.holding_days > 0]
        if holding_days_list:
            report.avg_holding_days = sum(holding_days_list) / len(holding_days_list)
            report.max_holding_days = max(holding_days_list)
            report.min_holding_days = min(holding_days_list)

        # ドローダウン
        equity_curve = self._get_equity_curve_sorted(strategy_name)
        dd_pct, dd_amount, peak_date, trough_date = self._calc_max_drawdown(equity_curve)
        report.max_drawdown_pct = dd_pct
        report.max_drawdown_amount = dd_amount
        report.peak_equity_date = peak_date
        report.trough_equity_date = trough_date

        # ベンチマーク比較
        if equity_curve:
            start_date = equity_curve[0]["date"]
            end_date = equity_curve[-1]["date"]
            report.benchmark_2559_return = self._get_benchmark_return("JP.2559", start_date, end_date)
            report.benchmark_1306_return = self._get_benchmark_return("JP.1306", start_date, end_date)
            if report.benchmark_2559_return is not None:
                report.excess_vs_2559 = report.total_return_pct - report.benchmark_2559_return
            if report.benchmark_1306_return is not None:
                report.excess_vs_1306 = report.total_return_pct - report.benchmark_1306_return

        # exit_reason別
        reason_groups: dict[str, list[ClosedTrade]] = {}
        for c in closed:
            r = c.exit_reason
            if r not in reason_groups:
                reason_groups[r] = []
            reason_groups[r].append(c)

        report.exit_reason_stats = []
        for reason, trades in sorted(reason_groups.items()):
            w = sum(1 for t in trades if t.realized_pl > 0)
            lo = sum(1 for t in trades if t.realized_pl < 0)
            total = len(trades)
            pl = sum(t.realized_pl for t in trades)
            days = [t.holding_days for t in trades if t.holding_days > 0]

            stat = ExitReasonStats(
                exit_reason=reason,
                count=total,
                win_count=w,
                loss_count=lo,
                win_rate=w / total * 100 if total > 0 else 0,
                realized_pl=pl,
                avg_pl=pl / total if total > 0 else 0,
                avg_holding_days=sum(days) / len(days) if days else 0,
            )
            report.exit_reason_stats.append(stat)

        return report

    def to_dataframe(self, report: VirtualReport) -> pd.DataFrame:
        """レポートをDataFrameに変換"""
        rows = [{
            "initial_cash": report.initial_cash,
            "final_total_equity": report.final_total_equity,
            "total_return_pct": report.total_return_pct,
            "realized_pl": report.realized_pl,
            "unrealized_pl": report.unrealized_pl,
            "total_pl": report.total_pl,
            "closed_trade_count": report.closed_trade_count,
            "win_rate": report.win_rate,
            "avg_win": report.avg_win,
            "avg_loss": report.avg_loss,
            "profit_factor": report.profit_factor,
            "max_drawdown_pct": report.max_drawdown_pct,
            "benchmark_2559_return": report.benchmark_2559_return,
            "excess_vs_2559": report.excess_vs_2559,
            "benchmark_1306_return": report.benchmark_1306_return,
            "excess_vs_1306": report.excess_vs_1306,
            "avg_holding_days": report.avg_holding_days,
        }]
        return pd.DataFrame(rows)

    def exit_reason_to_dataframe(self, report: VirtualReport) -> pd.DataFrame:
        """exit_reason別統計をDataFrameに変換"""
        rows = []
        for s in (report.exit_reason_stats or []):
            rows.append({
                "exit_reason": s.exit_reason,
                "count": s.count,
                "win_count": s.win_count,
                "loss_count": s.loss_count,
                "win_rate": s.win_rate,
                "realized_pl": s.realized_pl,
                "avg_pl": s.avg_pl,
                "avg_holding_days": s.avg_holding_days,
            })
        return pd.DataFrame(rows)

    def closed_trades_to_dataframe(self, report: VirtualReport) -> pd.DataFrame:
        """クローズドトレードをDataFrameに変換"""
        rows = [
            {
                "code": c.code,
                "entry_date": c.entry_date,
                "exit_date": c.exit_date,
                "entry_price": c.entry_price,
                "exit_price": c.exit_price,
                "quantity": c.quantity,
                "realized_pl": c.realized_pl,
                "return_pct": c.return_pct,
                "holding_days": c.holding_days,
                "exit_reason": c.exit_reason,
            }
            for c in (report.closed_trades or [])
        ]
        return pd.DataFrame(rows)
