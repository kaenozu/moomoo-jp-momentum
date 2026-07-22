"""
バックテストエンジン

ファイルパス: src/backtest_runner.py
何をするか: 過去日足データを使ってルックアヘッドなしのバックテストを実行する
なぜ存在するか: 戦略の過去パフォーマンスを検証するため
関連ファイル: models.py, indicators.py, strategies/*.py

時系列整合性:
  1日ループは ①約定処理 → ②exit判定 → ③新規シグナル → ④idle cash → ⑤equity の順。
  BUY注文はsignal_dayに作成し、翌営業日openで約定(pending)。
  約定日前にexit判定の対象にはならない。
"""

import logging
import sqlite3
from dataclasses import dataclass

from pathlib import Path
from typing import Optional, Protocol

import pandas as pd

from .config import Config
from .benchmarking import (
    adjusted_price,
    ensure_benchmark_schema,
    load_benchmark_specs,
    save_benchmark_equity,
    save_benchmark_result,
    seed_configured_actions,
)
from .indicators import StockIndicators, add_cross_sectional_stats, calculate_indicators
from .strategies import StrategyRegistry, StrategyResult
from .strategies import momentum, quality_low_risk, etf_rotation  # noqa: F401 - register strategies

logger = logging.getLogger(__name__)


@dataclass
class _PendingOrder:
    """バックテスト用pending注文（メモリ上）"""
    code: str
    side: str  # "BUY" or "SELL"
    quantity: int
    fill_price: float
    fill_date: str
    signal_date: str
    exit_reason: Optional[str] = None
    avg_cost: float = 0.0  # SELL時のみ使用


class _StrategyEvaluator(Protocol):
    """Minimal strategy interface required for candidate ranking."""

    def evaluate(
        self,
        indicators: StockIndicators,
        benchmark_returns: Optional[dict] = None,
    ) -> StrategyResult:
        ...


def _rank_buy_candidates(
    valid_pairs: list[tuple[str, StockIndicators]],
    strategy: _StrategyEvaluator,
) -> list[tuple[str, StockIndicators]]:
    """Evaluate every candidate, then rank BUY signals deterministically.

    Database insertion order must never decide which symbols consume limited
    position slots. Higher strategy scores rank first; symbol code is the stable
    tie-breaker. WATCH/EXCLUDE results are removed before slot allocation.
    """
    evaluated: list[tuple[str, StockIndicators, StrategyResult]] = []
    for code, indicators in valid_pairs:
        result = strategy.evaluate(indicators)
        if result.signal_type == "BUY_CANDIDATE":
            evaluated.append((code, indicators, result))

    evaluated.sort(key=lambda item: (-(item[2].score or 0.0), item[0]))
    return [(code, indicators) for code, indicators, _ in evaluated]



class BacktestRunner:
    """バックテスト実行エンジン"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)
        self.run_id: Optional[int] = None
        self.strategy_name = ""
        self.initial_cash = 100000
        self.cash = 100000
        self.reserved_cash = 0.0  # pending BUY予約額
        self.slippage_bps = 10
        self.commission = 0
        self.max_position_amount = 20000
        backtest_cfg = config.get("backtest", {})
        self.max_total_positions = backtest_cfg.get("max_positions", 5)
        self.stop_loss_pct = backtest_cfg.get("stop_loss_pct", 5.0)
        universe_cfg = config.get("universe", {})
        self.min_trade_price = universe_cfg.get("min_trade_price", 500)
        self.max_trade_price = universe_cfg.get("max_trade_price", 20000)
        self.benchmarks = load_benchmark_specs(config)
        with self._conn() as connection:
            ensure_benchmark_schema(connection)
            seed_configured_actions(connection, config)

    def _benchmark_code(self) -> str:
        return self.config.get(
            "signals.relative_strength.benchmark_code",
            self.benchmarks.primary.code,
        )

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
        """バックテスト実行

        1日ループの順序:
          ① pending約定処理（前日に作成した注文の約定）
          ② exit判定（既存ポジションのみ）
          ③ 新規シグナル評価 → pending注文作成
          ④ idle cash allocation
          ⑤ equity curve更新
        """
        self.strategy_name = strategy_name
        self.cash = self.initial_cash
        self.reserved_cash = 0.0  # pending BUY予約額（signal日で増加、fill日で解放）
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

        # pending注文（メモリ管理）
        pending_orders: list[_PendingOrder] = []

        # Idle cash allocation tracking
        idle_code = self._idle_cash_benchmark_code()
        idle_bench_prev = None

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

            # ── Phase 1: pending約定処理 ──
            today_fills = [o for o in pending_orders if o.fill_date == day]
            pending_orders = [o for o in pending_orders if o.fill_date != day]

            for order in today_fills:
                if order.side == "BUY":
                    cost = order.fill_price * order.quantity + self.commission
                    with self._conn() as conn:
                        cur = conn.execute(
                            "INSERT INTO backtest_orders (run_id, strategy_name, code, side, quantity, order_type, status, signal_date) VALUES (?,?,?,?,?,?,?,?)",
                            (self.run_id, strategy_name, order.code, "BUY", order.quantity, "MARKET_SIM", "FILLED", order.signal_date),
                        )
                        oid = cur.lastrowid
                        conn.execute(
                            "INSERT INTO backtest_fills (run_id, order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                            (self.run_id, oid, strategy_name, order.code, "BUY", order.quantity, order.fill_price, day, "next_day_open"),
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO backtest_positions (run_id, strategy_name, code, quantity, avg_cost, realized_pl) VALUES (?,?,?,?,?,0)",
                            (self.run_id, strategy_name, order.code, order.quantity, order.fill_price),
                        )
                    trailing_highs[order.code] = order.fill_price
                    self.cash -= cost
                    self.reserved_cash = max(0.0, self.reserved_cash - cost)
                    fills_processed += 1

                elif order.side == "SELL":
                    with self._conn() as conn:
                        cur = conn.execute(
                            "INSERT INTO backtest_orders (run_id, strategy_name, code, side, quantity, order_type, status, signal_date, exit_reason) VALUES (?,?,?,?,?,?,?,?,?)",
                            (self.run_id, strategy_name, order.code, "SELL", order.quantity, "MARKET_SIM", "FILLED", order.signal_date, order.exit_reason),
                        )
                        oid = cur.lastrowid
                        conn.execute(
                            "INSERT INTO backtest_fills (run_id, order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                            (self.run_id, oid, strategy_name, order.code, "SELL", order.quantity, order.fill_price, day, "exit"),
                        )
                        realized_pl = (order.fill_price - order.avg_cost) * order.quantity
                        conn.execute(
                            "UPDATE backtest_positions SET quantity=0, realized_pl=realized_pl + ? WHERE run_id=? AND strategy_name=? AND code=?",
                            (realized_pl, self.run_id, strategy_name, order.code),
                        )
                    self.cash += order.fill_price * order.quantity - self.commission
                    trailing_highs.pop(order.code, None)
                    exits_generated += 1
                    fills_processed += 1

            # ── Phase 2: exit判定（既存ポジションのみ） ──
            positions = self._get_all_positions()
            held_codes = {p["code"] for p in positions if p["quantity"] > 0}

            for pos in positions:
                code = pos["code"]
                if pos["quantity"] <= 0:
                    continue
                # pending SELLがあればスキップ
                if any(o.code == code and o.side == "SELL" for o in pending_orders):
                    continue

                df = self._bars_up_to(code, day)
                if df.empty or len(df) < 2:
                    continue
                current_price = float(df.iloc[0]["close"])
                avg_cost = pos["avg_cost"]

                # trailing high更新
                if code in trailing_highs:
                    trailing_highs[code] = max(trailing_highs[code], current_price)
                else:
                    trailing_highs[code] = current_price

                exit_reason = None
                # trailing stop
                trail_level = trailing_highs[code] * (1.0 - self.stop_loss_pct / 100.0)
                if current_price <= trail_level:
                    exit_reason = "trailing_stop"

                # MA25クロス
                if not exit_reason and len(df) >= 25:
                    ma25 = df["close"].iloc[:25].mean()
                    if current_price < ma25:
                        exit_reason = "ma25_cross"

                if exit_reason:
                    next_bar = self._next_open_bar(code, day)
                    if next_bar:
                        fill_date, next_open = next_bar
                        fill_price_sell = next_open * (1 - self.slippage_bps / 10000)
                        pending_orders.append(_PendingOrder(
                            code=code,
                            side="SELL",
                            quantity=pos["quantity"],
                            fill_price=fill_price_sell,
                            fill_date=fill_date,
                            signal_date=day,
                            exit_reason=exit_reason,
                            avg_cost=avg_cost,
                        ))

            # ── Phase 3: 新規シグナル評価 ──
            day_indicators: list[StockIndicators] = []
            valid_pairs: list[tuple[str, StockIndicators]] = []
            for sym in candidates:
                code = sym["code"]
                # 既にポジション or pending BUY があればスキップ
                if code in held_codes:
                    continue
                if any(o.code == code and o.side == "BUY" for o in pending_orders):
                    continue

                df = self._bars_up_to(code, day)
                if df.empty or len(df) < 25:
                    continue

                ind = calculate_indicators(df, code, sym["name"])
                if ind is None or ind.ma25 is None:
                    continue
                if ind.close is None or ind.close > self.max_trade_price or ind.close < self.min_trade_price:
                    continue

                day_indicators.append(ind)
                valid_pairs.append((code, ind))

            if day_indicators:
                add_cross_sectional_stats(day_indicators)

            # 注文可能数 = max_total_positions - (約定済みポジション数 + pending BUY数)
            current_pos_count = self._count_positions()
            pending_buy_count = sum(1 for o in pending_orders if o.side == "BUY")
            slots_available = self.max_total_positions - current_pos_count - pending_buy_count

            ranked_candidates = _rank_buy_candidates(valid_pairs, strategy)
            for code, ind in ranked_candidates:
                if slots_available <= 0:
                    break

                available_cash = self.cash - self.reserved_cash
                if ind.close and ind.close > available_cash:
                    continue

                next_bar = self._next_open_bar(code, day)
                if next_bar is None:
                    continue
                fill_date, next_open = next_bar

                fill_price = next_open * (1 + self.slippage_bps / 10000)
                qty = max(1, int(target_pos_value / fill_price))
                cost = fill_price * qty + self.commission
                if cost > available_cash:
                    continue

                pending_orders.append(_PendingOrder(
                    code=code,
                    side="BUY",
                    quantity=qty,
                    fill_price=fill_price,
                    fill_date=fill_date,
                    signal_date=day,
                ))
                self.reserved_cash += cost
                slots_available -= 1
                orders_created += 1

            # ── Phase 4: idle cash allocation ──
            if idle_code:
                bm_today = self._benchmark_value(idle_code, day)
                if bm_today is not None and idle_bench_prev is not None:
                    daily_bm_ret = (bm_today - idle_bench_prev) / idle_bench_prev
                    self.cash = self.cash * (1 + daily_bm_ret)
                idle_bench_prev = bm_today

            # ── Phase 5: equity curve更新 ──
            pos_value = sum(
                (self._bars_up_to(p["code"], day).iloc[0]["close"] if not self._bars_up_to(p["code"], day).empty else 0) * p["quantity"]
                for p in self._get_all_positions() if p["quantity"] > 0
            )
            total_equity = self.cash + pos_value

            benchmark_values = {
                spec.role: self._benchmark_value(spec.code, day)
                for spec in self.benchmarks.all()
            }

            peak_equity = max(peak_equity, total_equity)
            drawdown = max(0, (peak_equity - total_equity) / peak_equity * 100) if peak_equity else 0

            with self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO backtest_equity_curve (run_id, strategy_name, date, cash, position_value, total_equity, benchmark_2559_value, benchmark_1306_value, drawdown_pct) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        self.run_id, strategy_name, day, self.cash, pos_value,
                        total_equity, benchmark_values["reference"],
                        benchmark_values["primary"], drawdown,
                    ),
                )
                for spec in self.benchmarks.all():
                    save_benchmark_equity(
                        conn,
                        run_id=run_id,
                        strategy_name=strategy_name,
                        date=day,
                        spec=spec,
                        adjusted_close=benchmark_values[spec.role],
                    )

        # ── 最終結果を保存 ──
        final_equity = self.cash + sum(
            (self._bars_up_to(p["code"], days[-1]).iloc[0]["close"] if not self._bars_up_to(p["code"], days[-1]).empty else 0) * p["quantity"]
            for p in self._get_all_positions() if p["quantity"] > 0
        )
        total_return = (final_equity - self.initial_cash) / self.initial_cash * 100

        stats = self._calculate_run_stats()

        with self._conn() as conn:
            returns = {
                spec.role: save_benchmark_result(
                    conn,
                    run_id=run_id,
                    spec=spec,
                    start_date=start_date,
                    end_date=days[-1],
                    strategy_return_pct=total_return,
                )
                for spec in self.benchmarks.all()
            }
            primary_return = returns["primary"]
            reference_return = returns["reference"]
            conn.execute(
                """
                UPDATE backtest_runs
                SET final_equity=?, total_return_pct=?, max_drawdown_pct=?,
                    win_rate=?, profit_factor=?, trade_count=?,
                    benchmark_2559_return=?, excess_vs_2559=?,
                    benchmark_1306_return=?, excess_vs_1306=?
                WHERE id=?
                """,
                (
                    final_equity, total_return, stats["max_drawdown_pct"],
                    stats["win_rate"], stats["profit_factor"], stats["trade_count"],
                    reference_return,
                    total_return - reference_return if reference_return is not None else None,
                    primary_return,
                    total_return - primary_return if primary_return is not None else None,
                    self.run_id,
                ),
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
            return adjusted_price(conn, code, date)

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
