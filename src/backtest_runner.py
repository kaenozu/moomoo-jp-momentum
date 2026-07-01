"""
バックテストエンジン

ファイルパス: src/backtest_runner.py
何をするか: 過去日足データを使ってルックアヘッドなしのバックテストを実行する
なぜ存在するか: 戦略の過去パフォーマンスを検証するため
関連ファイル: models.py, indicators.py, strategies/*.py
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Config
from .indicators import calculate_indicators
from .strategies import StrategyRegistry
from .strategies import momentum, quality_low_risk, etf_rotation  # noqa: F401 - register strategies

logger = logging.getLogger(__name__)


class BacktestRunner:
    """バックテスト実行エンジン"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)
        self.run_id: Optional[int] = None
        self.strategy_name = ""
        self.initial_cash = 100000
        self.cash = 100000
        self.slippage_bps = 10
        self.commission = 0
        self.max_position_amount = 20000
        self.max_total_positions = 5
        self.min_trade_price = 500
        self.max_trade_price = 20000

    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _trading_days(self, start: str, end: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM daily_bars WHERE date >= ? AND date <= ? ORDER BY date",
                (start, end),
            ).fetchall()
            return [r[0] for r in rows]

    def _bars_up_to(self, code: str, date: str, limit: int = 250) -> pd.DataFrame:
        with self._conn() as conn:
            df = pd.read_sql_query(
                "SELECT code, date as time_key, open, high, low, close, volume, turnover "
                "FROM daily_bars WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT ?",
                conn, params=(code, date, limit),
            )
        return df

    def _next_open(self, code: str, after_date: str) -> Optional[float]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT open FROM daily_bars WHERE code = ? AND date > ? ORDER BY date LIMIT 1",
                (code, after_date),
            ).fetchone()
            return row[0] if row else None

    def _is_etf(self, code: str) -> bool:
        return code.startswith("JP.13") or code.startswith("JP.25")

    def create_run(self, strategy_name: str, start_date: str, end_date: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO backtest_runs (strategy_name, start_date, end_date, initial_cash) VALUES (?, ?, ?, ?)",
                (strategy_name, start_date, end_date, self.initial_cash),
            )
            self.run_id = cur.lastrowid
        return self.run_id

    def run(self, strategy_name: str, start_date: str, end_date: str) -> int:
        """バックテスト実行"""
        self.strategy_name = strategy_name
        self.cash = self.initial_cash
        self.create_run(strategy_name, start_date, end_date)

        strategy = StrategyRegistry.get(strategy_name, self.config)
        days = self._trading_days(start_date, end_date)
        logger.info("バックテスト: %s %s〜%s (%d日, strategy=%s)", start_date, end_date, len(days), strategy_name)

        # benchmark データ準備
        benchmark_2559_start = self._benchmark_value("JP.2559", start_date)
        benchmark_1306_start = self._benchmark_value("JP.1306", start_date)

        # symbols 取得
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT code, name, role, tradable FROM symbols WHERE enabled=1"
            ).fetchall()

        trade_candidates = [
            r for r in rows
            if r["role"] == "trade_candidate" and r["tradable"]
        ]
        benchmark_codes = {
            r["code"] for r in rows if r["role"] == "benchmark"
        } if strategy_name == "etf_rotation" else set()

        total_days = len(days)
        orders_created = 0
        fills_processed = 0
        exits_generated = 0

        for i, day in enumerate(days):
            if (i + 1) % 20 == 0:
                logger.info("  [%d/%d] %s ...", i + 1, total_days, day)

            candidates = trade_candidates
            if strategy_name == "etf_rotation":
                candidates = [r for r in rows if r["code"] in benchmark_codes]

            # 1. シグナル判定
            for sym in candidates:
                code = sym["code"]
                df = self._bars_up_to(code, day)
                if df.empty or len(df) < 25:
                    continue

                ind = calculate_indicators(df, code, sym["name"])
                if ind is None or ind.ma25 is None:
                    continue
                if ind.close is None or ind.close > self.max_trade_price or ind.close < self.min_trade_price:
                    continue

                # 注文可能チェック
                pos = self._get_position(code)
                if pos and pos["quantity"] > 0:
                    continue
                if ind.close and ind.close * 1 > self.cash:
                    continue
                if self._count_positions() >= self.max_total_positions:
                    break

                result = strategy.evaluate(ind)
                if result.signal_type != "BUY_CANDIDATE":
                    continue

                # 注文：翌営業日openで約定
                next_open = self._next_open(code, day)
                if next_open is None:
                    continue

                fill_price = next_open * (1 + self.slippage_bps / 10000)
                cost = fill_price * 1 + self.commission
                if cost > self.cash:
                    continue

                # DB保存
                with self._conn() as conn:
                    cur = conn.execute(
                        "INSERT INTO backtest_orders (run_id, strategy_name, code, side, quantity, order_type, status, signal_date) VALUES (?,?,?,?,?,?,?,?)",
                        (self.run_id, strategy_name, code, "BUY", 1, "MARKET_SIM", "FILLED", day),
                    )
                    oid = cur.lastrowid
                    conn.execute(
                        "INSERT INTO backtest_fills (run_id, order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                        (self.run_id, oid, strategy_name, code, "BUY", 1, fill_price, day, "next_day_open"),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO backtest_positions (run_id, strategy_name, code, quantity, avg_cost, realized_pl) VALUES (?,?,?,?,?,0)",
                        (self.run_id, strategy_name, code, 1, fill_price),
                    )

                self.cash -= cost
                orders_created += 1
                fills_processed += 1

            # 2. 出口判定
            positions = self._get_all_positions()
            for pos in positions:
                code = pos["code"]
                df = self._bars_up_to(code, day)
                if df.empty or len(df) < 2:
                    continue
                current_price = float(df.iloc[0]["close"])
                avg_cost = pos["avg_cost"]

                exit_reason = None
                if current_price <= avg_cost * 0.95:
                    exit_reason = "stop_loss"
                elif len(df) >= 25:
                    ma25 = df["close"].iloc[:25].mean()
                    if current_price < ma25:
                        exit_reason = "ma25_cross"

                if exit_reason and self._get_position(code) and self._get_position(code)["quantity"] > 0:
                    next_open = self._next_open(code, day)
                    if next_open:
                        fill_price_sell = next_open * (1 - self.slippage_bps / 10000)
                        with self._conn() as conn:
                            cur = conn.execute(
                                "INSERT INTO backtest_orders (run_id, strategy_name, code, side, quantity, order_type, status, signal_date, exit_reason) VALUES (?,?,?,?,?,?,?,?,?)",
                                (self.run_id, strategy_name, code, "SELL", 1, "MARKET_SIM", "FILLED", day, exit_reason),
                            )
                            oid = cur.lastrowid
                            conn.execute(
                                "INSERT INTO backtest_fills (run_id, order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                                (self.run_id, oid, strategy_name, code, "SELL", 1, fill_price_sell, day, "exit"),
                            )
                            realized_pl = (fill_price_sell - avg_cost) * 1
                            conn.execute(
                                "UPDATE backtest_positions SET quantity=0, realized_pl=realized_pl + ? WHERE run_id=? AND strategy_name=? AND code=?",
                                (realized_pl, self.run_id, strategy_name, code),
                            )
                        self.cash += fill_price_sell * 1 - self.commission
                        exits_generated += 1

            # 3. equity_curve更新
            pos_value = sum(
                (self._bars_up_to(p["code"], day).iloc[0]["close"] if not self._bars_up_to(p["code"], day).empty else 0) * p["quantity"]
                for p in self._get_all_positions() if p["quantity"] > 0
            )
            total_equity = self.cash + pos_value
            bm_2559 = self._benchmark_value("JP.2559", day)
            bm_1306 = self._benchmark_value("JP.1306", day)
            drawdown = max(0, (self.initial_cash - total_equity) / self.initial_cash * 100)

            with self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO backtest_equity_curve (run_id, strategy_name, date, cash, position_value, total_equity, benchmark_2559_value, benchmark_1306_value, drawdown_pct) VALUES (?,?,?,?,?,?,?,?,?)",
                    (self.run_id, strategy_name, day, self.cash, pos_value, total_equity, bm_2559, bm_1306, drawdown),
                )

        # 4. 最終結果を保存
        final_equity = self.cash + sum(
            (self._bars_up_to(p["code"], days[-1]).iloc[0]["close"] if not self._bars_up_to(p["code"], days[-1]).empty else 0) * p["quantity"]
            for p in self._get_all_positions() if p["quantity"] > 0
        )
        total_return = (final_equity - self.initial_cash) / self.initial_cash * 100

        bm_2559_start_val = self._benchmark_value("JP.2559", start_date)
        bm_2559_end_val = self._benchmark_value("JP.2559", days[-1])
        bm_2559_ret = (bm_2559_end_val - bm_2559_start_val) / bm_2559_start_val * 100 if bm_2559_start_val else None

        bm_1306_start_val = self._benchmark_value("JP.1306", start_date)
        bm_1306_end_val = self._benchmark_value("JP.1306", days[-1])
        bm_1306_ret = (bm_1306_end_val - bm_1306_start_val) / bm_1306_start_val * 100 if bm_1306_start_val else None

        with self._conn() as conn:
            conn.execute(
                "UPDATE backtest_runs SET final_equity=?, total_return_pct=?, benchmark_2559_return=?, excess_vs_2559=?, benchmark_1306_return=?, excess_vs_1306=? WHERE id=?",
                (final_equity, total_return,
                 bm_2559_ret, total_return - bm_2559_ret if bm_2559_ret else None,
                 bm_1306_ret, total_return - bm_1306_ret if bm_1306_ret else None,
                 self.run_id),
            )

        logger.info("バックテスト完了: return=%.2f%%, orders=%d, fills=%d, exits=%d",
                     total_return, orders_created, fills_processed, exits_generated)
        return self.run_id

    def _get_position(self, code: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM backtest_positions WHERE run_id=? AND strategy_name=? AND code=?",
                (self.run_id, self.strategy_name, code),
            ).fetchone()
            return dict(row) if row else None

    def _get_all_positions(self):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM backtest_positions WHERE run_id=? AND strategy_name=? AND quantity>0",
                (self.run_id, self.strategy_name),
            ).fetchall()
            return [dict(r) for r in rows]

    def _count_positions(self) -> int:
        return len(self._get_all_positions())

    def _benchmark_value(self, code: str, date: str) -> Optional[float]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT close FROM daily_bars WHERE code=? AND date <= ? ORDER BY date DESC LIMIT 1",
                (code, date),
            ).fetchone()
            return row[0] if row else None
