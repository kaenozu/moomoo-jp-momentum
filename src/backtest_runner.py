"""
バックテストエンジン

ファイルパス: src/backtest_runner.py
何をするか: 過去日足データを使ってルックアヘッドなしのバックテストを実行する
なぜ存在するか: 戦略の過去パフォーマンスを検証するため
関連ファイル: models.py, indicators.py, strategies/*.py
"""

import logging
import sqlite3

from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Config
from .indicators import StockIndicators, add_cross_sectional_stats, calculate_indicators
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
        backtest_cfg = config.get("backtest", {})
        self.max_total_positions = backtest_cfg.get("max_positions", 5)
        self.stop_loss_pct = backtest_cfg.get("stop_loss_pct", 5.0)
        universe_cfg = config.get("universe", {})
        self.min_trade_price = universe_cfg.get("min_trade_price", 500)
        self.max_trade_price = universe_cfg.get("max_trade_price", 20000)

    def _benchmark_code(self) -> str:
        return self.config.get("signals.relative_strength.benchmark_code", "JP.1306")

    def _idle_cash_benchmark_code(self) -> Optional[str]:
        cfg = self.config.get("backtest.idle_cash_allocation", {})
        if cfg.get("enabled", False):
            return cfg.get("benchmark_code", self._benchmark_code())
        return None


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
                conn, params=[code, date, limit],
            )
        return df

    def _next_open(self, code: str, after_date: str) -> Optional[float]:
        next_bar = self._next_open_bar(code, after_date)
        return next_bar[1] if next_bar else None

    def _next_open_bar(self, code: str, after_date: str) -> Optional[tuple[str, float]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT date, open FROM daily_bars WHERE code = ? AND date > ? ORDER BY date LIMIT 1",
                (code, after_date),
            ).fetchone()
            return (str(row[0]), float(row[1])) if row and row[1] is not None else None

    def _is_etf(self, code: str) -> bool:
        return code.startswith("JP.13") or code.startswith("JP.25")

    def create_run(self, strategy_name: str, start_date: str, end_date: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO backtest_runs (strategy_name, start_date, end_date, initial_cash) VALUES (?, ?, ?, ?)",
                (strategy_name, start_date, end_date, self.initial_cash),
            )
            self.run_id = cur.lastrowid
        if self.run_id is None:
            raise RuntimeError("backtest_runs の作成に失敗しました")
        return self.run_id

    def run(self, strategy_name: str, start_date: str, end_date: str) -> int:
        """バックテスト実行"""
        self.strategy_name = strategy_name
        self.cash = self.initial_cash
        run_id = self.create_run(strategy_name, start_date, end_date)

        strategy = StrategyRegistry.get(strategy_name, self.config)
        days = self._trading_days(start_date, end_date)
        logger.info("バックテスト: %s %s〜%s (%d日, strategy=%s)", start_date, end_date, len(days), strategy_name)
        if not days:
            raise ValueError(f"バックテスト対象日のデータがありません: {start_date}〜{end_date}")

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
        peak_equity = self.initial_cash

        # Idle cash allocation tracking
        idle_code = self._idle_cash_benchmark_code()
        idle_bench_prev = None
        idle_cash_accum = 0.0  # cumulative PnL from idle cash bench allocation

        # Trailing stop tracking: code -> highest close seen
        trailing_highs: dict[str, float] = {}
        # Value-based sizing
        target_pos_value = self.initial_cash / self.max_total_positions

        for i, day in enumerate(days):
            if (i + 1) % 20 == 0:
                logger.info("  [%d/%d] %s ...", i + 1, total_days, day)

            candidates = trade_candidates
            if strategy_name == "etf_rotation":
                candidates = [r for r in rows if r["code"] in benchmark_codes]

            # 1. シグナル判定 Phase 1: 全銘柄の指標計算
            day_indicators: list[StockIndicators] = []
            valid_pairs = []
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

                day_indicators.append(ind)
                valid_pairs.append((sym, ind))

            # Phase 2: クロスセクション統計（volume percentile等）を追加
            if day_indicators:
                add_cross_sectional_stats(day_indicators)

            # Phase 3: 注文生成（enriched indicatorsを使用）
            for sym, ind in valid_pairs:
                code = sym["code"]

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
                next_open_bar = self._next_open_bar(code, day)
                if next_open_bar is None:
                    continue
                fill_date, next_open = next_open_bar

                fill_price = next_open * (1 + self.slippage_bps / 10000)
                # Value-based sizing: allocate target dollar amount per position
                qty = max(1, int(target_pos_value / fill_price))
                cost = fill_price * qty + self.commission
                if cost > self.cash:
                    continue

                # DB保存
                with self._conn() as conn:
                    cur = conn.execute(
                        "INSERT INTO backtest_orders (run_id, strategy_name, code, side, quantity, order_type, status, signal_date) VALUES (?,?,?,?,?,?,?,?)",
                        (self.run_id, strategy_name, code, "BUY", qty, "MARKET_SIM", "FILLED", day),
                    )
                    oid = cur.lastrowid
                    conn.execute(
                        "INSERT INTO backtest_fills (run_id, order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                        (self.run_id, oid, strategy_name, code, "BUY", qty, fill_price, fill_date, "next_day_open"),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO backtest_positions (run_id, strategy_name, code, quantity, avg_cost, realized_pl) VALUES (?,?,?,?,?,0)",
                        (self.run_id, strategy_name, code, qty, fill_price),
                    )

                # Initialize trailing stop high for new position
                trailing_highs[code] = fill_price

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
                qty = pos["quantity"]

                # Update trailing high
                if code in trailing_highs:
                    trailing_highs[code] = max(trailing_highs[code], current_price)
                else:
                    trailing_highs[code] = current_price

                exit_reason = None
                # Trailing stop: exit if close drops stop_loss_pct from highest seen
                if qty > 0:
                    trail_level = trailing_highs[code] * (1.0 - self.stop_loss_pct / 100.0)
                    if current_price <= trail_level:
                        exit_reason = "trailing_stop"

                if not exit_reason and len(df) >= 25:
                    ma25 = df["close"].iloc[:25].mean()
                    if current_price < ma25:
                        exit_reason = "ma25_cross"

                current_pos = self._get_position(code)
                if exit_reason and current_pos and current_pos["quantity"] > 0:
                    exit_qty = current_pos["quantity"]
                    next_open_bar = self._next_open_bar(code, day)
                    if next_open_bar:
                        fill_date, next_open = next_open_bar
                        fill_price_sell = next_open * (1 - self.slippage_bps / 10000)
                        with self._conn() as conn:
                            cur = conn.execute(
                                "INSERT INTO backtest_orders (run_id, strategy_name, code, side, quantity, order_type, status, signal_date, exit_reason) VALUES (?,?,?,?,?,?,?,?,?)",
                                (self.run_id, strategy_name, code, "SELL", exit_qty, "MARKET_SIM", "FILLED", day, exit_reason),
                            )
                            oid = cur.lastrowid
                            conn.execute(
                                "INSERT INTO backtest_fills (run_id, order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                                (self.run_id, oid, strategy_name, code, "SELL", exit_qty, fill_price_sell, fill_date, "exit"),
                            )
                            realized_pl = (fill_price_sell - avg_cost) * exit_qty
                            conn.execute(
                                "UPDATE backtest_positions SET quantity=0, realized_pl=realized_pl + ? WHERE run_id=? AND strategy_name=? AND code=?",
                                (realized_pl, self.run_id, strategy_name, code),
                            )
                        self.cash += fill_price_sell * exit_qty - self.commission
                        if code in trailing_highs:
                            del trailing_highs[code]
                        exits_generated += 1

            # 3. equity_curve更新
            pos_value = sum(
                (self._bars_up_to(p["code"], day).iloc[0]["close"] if not self._bars_up_to(p["code"], day).empty else 0) * p["quantity"]
                for p in self._get_all_positions() if p["quantity"] > 0
            )
            total_equity = self.cash + pos_value
            bm_code = self._benchmark_code()
            bm_primary = self._benchmark_value(bm_code, day)
            bm_secondary = None
            secondary_codes = self.config.get("benchmark.secondary", [])
            if isinstance(secondary_codes, list) and secondary_codes:
                bm_secondary = self._benchmark_value(secondary_codes[0]["code"], day)

            # Idle cash allocation: apply benchmark return to uninvested cash
            total_equity_with_idle = total_equity
            if idle_code:
                bm_today = self._benchmark_value(idle_code, day)
                if bm_today is not None and idle_bench_prev is not None:
                    daily_bm_ret = (bm_today - idle_bench_prev) / idle_bench_prev
                    idle_cash_accum += self.cash * daily_bm_ret
                idle_bench_prev = bm_today
                total_equity_with_idle = total_equity + idle_cash_accum

            peak_equity = max(peak_equity, total_equity_with_idle)
            drawdown = max(0, (peak_equity - total_equity_with_idle) / peak_equity * 100) if peak_equity else 0

            with self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO backtest_equity_curve (run_id, strategy_name, date, cash, position_value, total_equity, benchmark_2559_value, benchmark_1306_value, drawdown_pct) VALUES (?,?,?,?,?,?,?,?,?)",
                    (self.run_id, strategy_name, day, self.cash, pos_value, total_equity_with_idle, bm_primary, bm_secondary, drawdown),
                )

        # 4. 最終結果を保存
        final_equity = self.cash + sum(
            (self._bars_up_to(p["code"], days[-1]).iloc[0]["close"] if not self._bars_up_to(p["code"], days[-1]).empty else 0) * p["quantity"]
            for p in self._get_all_positions() if p["quantity"] > 0
        ) + idle_cash_accum
        total_return = (final_equity - self.initial_cash) / self.initial_cash * 100

        bm_code = self._benchmark_code()
        bm_start_val = self._benchmark_value(bm_code, start_date)
        bm_end_val = self._benchmark_value(bm_code, days[-1])
        bm_ret = (
            (bm_end_val - bm_start_val) / bm_start_val * 100
            if bm_start_val and bm_end_val is not None
            else None
        )
        stats = self._calculate_run_stats()

        with self._conn() as conn:
            conn.execute(
                """
                UPDATE backtest_runs
                SET final_equity=?, total_return_pct=?, max_drawdown_pct=?,
                    win_rate=?, profit_factor=?, trade_count=?,
                    benchmark_2559_return=?, excess_vs_2559=?,
                    benchmark_1306_return=?, excess_vs_1306=?
                WHERE id=?
                """,
                (final_equity, total_return, stats["max_drawdown_pct"],
                 stats["win_rate"], stats["profit_factor"], stats["trade_count"],
                 bm_ret, total_return - bm_ret if bm_ret else None,
                 None, None,
                 self.run_id),
            )

        logger.info("バックテスト完了: return=%.2f%%, orders=%d, fills=%d, exits=%d",
                     total_return, orders_created, fills_processed, exits_generated)
        return run_id

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
            return float(row[0]) if row and row[0] is not None else None

    def _calculate_run_stats(self) -> dict[str, Optional[float]]:
        with self._conn() as conn:
            dd_row = conn.execute(
                "SELECT MAX(drawdown_pct) FROM backtest_equity_curve WHERE run_id=?",
                (self.run_id,),
            ).fetchone()
            max_drawdown = dd_row[0] if dd_row and dd_row[0] is not None else 0.0

            rows = conn.execute(
                """
                SELECT code, side, price, quantity, filled_at
                FROM backtest_fills
                WHERE run_id=? AND strategy_name=?
                ORDER BY filled_at, id
                """,
                (self.run_id, self.strategy_name),
            ).fetchall()

        open_entries: dict[str, list[tuple[float, int]]] = {}
        realized: list[float] = []
        for row in rows:
            code = row["code"]
            side = row["side"]
            price = float(row["price"])
            qty = int(row["quantity"])
            if side == "BUY":
                open_entries.setdefault(code, []).append((price, qty))
                continue
            entries = open_entries.get(code) or []
            if not entries:
                continue
            entry_price, entry_qty = entries.pop(0)
            matched_qty = min(qty, entry_qty)
            realized.append((price - entry_price) * matched_qty)
            if entry_qty > matched_qty:
                entries.insert(0, (entry_price, entry_qty - matched_qty))

        trade_count = len(realized)
        wins = [p for p in realized if p > 0]
        losses = [p for p in realized if p < 0]
        win_rate = len(wins) / trade_count * 100 if trade_count else None
        total_win = sum(wins)
        total_loss = abs(sum(losses))
        if total_loss > 0:
            profit_factor = total_win / total_loss
        elif total_win > 0:
            profit_factor = float("inf")
        else:
            profit_factor = None

        return {
            "max_drawdown_pct": max_drawdown,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "trade_count": trade_count,
        }
