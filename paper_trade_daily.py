"""
US株ペーパートレード日次実行スクリプト

ファイルパス: paper_trade_daily.py
何をするか: 日次でUS株ペーパートレード（仮想取引）を実行する
  Phase 1: 既存ポジションの出口判定（トレーリングストップ / MA25クロス）
  Phase 2: 未約定注文の約定処理
  Phase 3: 新規買い注文（スクリーナー結果から）
  Phase 4: ポジション時価更新
  Phase 5: エクイティカーブ更新（SPYベンチマーク比較）
なぜ存在するか: バックテストと同じトレーリングストップロジックで
  ペーパートレードを日次自動実行するため
関連ファイル: src/virtual_trade.py, src/screener.py, config.yaml
"""

import argparse
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import sys

sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.virtual_trade import VirtualTradeManager

logger = logging.getLogger(__name__)

TRAILING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS paper_trade_trailing (
    strategy_name TEXT NOT NULL,
    code TEXT NOT NULL,
    highest_close REAL NOT NULL,
    entry_price REAL NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (strategy_name, code)
);
"""

BENCHMARK_IDS = {"US.SPY", "US.QQQ", "US.IWM", "US.VTI"}

# US株プレフィックス（JP株を除外するため）
US_MARKET_PREFIX = "US."


class PaperTradeDaily:
    """US株ペーパートレード日次実行"""

    def __init__(self, config, strategy_name: str = "momentum"):
        self.config = config
        self.strategy_name = strategy_name
        self.vtm = VirtualTradeManager(config)
        self.db_path = Path(config.database_path)

        bt = config.get("backtest", {})
        self.stop_loss_pct = float(bt.get("stop_loss_pct", 8.0))

        vt = config.get("virtual_trade", {})
        self.initial_cash = float(vt.get("initial_cash", 100000))
        self.max_positions = int(vt.get("max_total_positions", 10))
        self.target_pos_value = self.initial_cash / self.max_positions

        idle = bt.get("idle_cash_allocation", {})
        self.benchmark_code = idle.get("benchmark_code", "US.SPY")

        uni = config.get("universe", {})
        self.min_trade_price = float(uni.get("min_trade_price", 1))
        self.max_trade_price = float(uni.get("max_trade_price", 500000))

        self._ensure_tables()

    def _ensure_tables(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(TRAILING_TABLE_SQL)

    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _today(self) -> str:
        with self._conn() as conn:
            # ベンチマーク（US.SPY）の最新日付を優先
            row = conn.execute(
                "SELECT date FROM daily_bars WHERE code = ? ORDER BY date DESC LIMIT 1",
                (self.benchmark_code,),
            ).fetchone()
            if row:
                return row[0]
            # フォールバック: US株全体の最新日付
            row = conn.execute(
                "SELECT date FROM daily_bars WHERE code LIKE 'US.%' ORDER BY date DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else datetime.now().strftime("%Y-%m-%d")

    def _latest_close(self, code: str, target_date: str) -> Optional[float]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT close FROM daily_bars WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                (code, target_date),
            ).fetchone()
            return float(row[0]) if row and row[0] is not None else None

    def _get_trailing_high(self, code: str) -> Optional[float]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT highest_close FROM paper_trade_trailing WHERE strategy_name=? AND code=?",
                (self.strategy_name, code),
            ).fetchone()
            return float(row[0]) if row else None

    def _set_trailing_high(self, code: str, highest_close: float, entry_price: float):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO paper_trade_trailing "
                "(strategy_name, code, highest_close, entry_price) VALUES (?, ?, ?, ?)",
                (self.strategy_name, code, highest_close, entry_price),
            )

    def _clear_trailing_high(self, code: str):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM paper_trade_trailing WHERE strategy_name=? AND code=?",
                (self.strategy_name, code),
            )

    def check_exits(self, target_date: str) -> int:
        positions = self.vtm.get_positions(self.strategy_name)
        exits = 0
        for pos in positions:
            code = pos.code
            current_price = self._latest_close(code, target_date)
            if current_price is None:
                logger.warning("  exit skipped (no price): %s", code)
                continue

            trailing_high = self._get_trailing_high(code)
            if trailing_high is None:
                trailing_high = pos.avg_cost
            new_high = max(trailing_high, current_price)
            self._set_trailing_high(code, new_high, pos.avg_cost)

            should_exit = False
            exit_reason = ""

            trail_level = new_high * (1.0 - self.stop_loss_pct / 100.0)
            if current_price <= trail_level:
                should_exit = True
                exit_reason = "trailing_stop"

            if not should_exit:
                df = self._bars_up_to(code, target_date, 25)
                if len(df) >= 25:
                    ma25 = df["close"].head(25).mean()
                    if current_price < ma25:
                        should_exit = True
                        exit_reason = "ma25_cross"

            if should_exit:
                order = self.vtm.place_order(
                    strategy_name=self.strategy_name,
                    code=code,
                    side="SELL",
                    quantity=pos.quantity,
                    order_type="MARKET_SIM",
                    submitted_at=target_date,
                    exit_reason=exit_reason,
                )
                if order:
                    exits += 1
                    logger.info(
                        "  EXIT %s: %d shares @%.2f (%s)",
                        code, pos.quantity, current_price, exit_reason,
                    )
        return exits

    def _bars_up_to(self, code: str, target_date: str, limit: int = 250):
        import pandas as pd
        with self._conn() as conn:
            return pd.read_sql_query(
                "SELECT date, open, high, low, close, volume "
                "FROM daily_bars WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT ?",
                conn, params=[code, target_date, limit],
            )

    def process_fills_sells_first(self, target_date: str) -> int:
        fills = self.vtm.process_fills(self.strategy_name, target_date)
        for f in fills:
            logger.info(
                "  FILL %s: %s %d @%.2f (%s)",
                f.code, f.side, f.quantity, f.price, f.filled_at,
            )
            if f.side == "BUY":
                self._set_trailing_high(f.code, f.price, f.price)
            else:
                self._clear_trailing_high(f.code)
        return len(fills)

    def check_buys(self, target_date: str) -> int:
        positions = self.vtm.get_positions(self.strategy_name)
        current_count = len(positions)
        slots = self.max_positions - current_count
        if slots <= 0:
            logger.info(
                "  max positions (%d/%d), skipping buys",
                current_count, self.max_positions,
            )
            return 0

        with self._conn() as conn:
            held_codes = {p.code for p in positions}
            # Use latest signals date <= target_date that has US signals
            sign_date_row = conn.execute(
                "SELECT MAX(date) FROM signals WHERE date <= ? AND code LIKE 'US.%'",
                (target_date,),
            ).fetchone()
            sign_date = sign_date_row[0] if sign_date_row and sign_date_row[0] else target_date
            rows = conn.execute(
                """
                SELECT s.code, s.score, s.price_at_signal, s.reason
                FROM signals s
                WHERE s.date = ?
                  AND s.signal_type = 'BUY_CANDIDATE'
                  AND s.strategy_name = ?
                ORDER BY s.score DESC
                LIMIT ?
                """,
                (sign_date, self.strategy_name, slots + len(held_codes)),
            ).fetchall()

        remaining_cash = self.vtm.get_cash(self.strategy_name)
        buys = 0
        for row in rows:
            code = row["code"]
            if not code.startswith(US_MARKET_PREFIX):
                continue
            if code in held_codes or code in BENCHMARK_IDS:
                continue
            if buys >= slots:
                break

            price = self._latest_close(code, target_date)
            if price is None:
                logger.warning("  buy skipped (no price): %s", code)
                continue
            if not (self.min_trade_price <= price <= self.max_trade_price):
                logger.info("  buy skipped (price %.2f out of range): %s", price, code)
                continue

            qty = max(1, int(self.target_pos_value / price))
            cost = price * qty
            if cost > remaining_cash:
                logger.info("  buy skipped (remaining %.0f < cost %.0f): %s", remaining_cash, cost, code)
                continue

            order = self.vtm.place_order(
                strategy_name=self.strategy_name,
                code=code,
                side="BUY",
                quantity=qty,
                order_type="MARKET_SIM",
                submitted_at=target_date,
            )
            if order:
                buys += 1
                remaining_cash -= cost
                held_codes.add(code)
                logger.info(
                    "  BUY %s: %d shares @%.2f (score=%.0f)",
                    code, qty, price, row["score"],
                )
        return buys

    def update_prices(self, target_date: str) -> int:
        return self.vtm.update_market_prices(self.strategy_name, target_date)

    def save_equity(self, target_date: str) -> dict:
        return self.vtm.save_equity_curve(
            self.strategy_name, target_date, self.benchmark_code,
        )

    def cleanup_trailing(self):
        positions = {p.code for p in self.vtm.get_positions(self.strategy_name)}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT code FROM paper_trade_trailing WHERE strategy_name=?",
                (self.strategy_name,),
            ).fetchall()
            for row in rows:
                if row["code"] not in positions:
                    conn.execute(
                        "DELETE FROM paper_trade_trailing WHERE strategy_name=? AND code=?",
                        (self.strategy_name, row["code"]),
                    )

    def show_positions(self, target_date: str):
        positions = self.vtm.get_positions(self.strategy_name)
        if not positions:
            print("  ポジションなし")
            return
        print(f"  {'コード':<12} {'数量':>5} {'取得単価':>10} {'現在値':>10} {'評価損益':>10} {'含み益%':>8}")
        print(f"  {'-'*55}")
        total_value = 0
        total_pl = 0
        for p in positions:
            mp = self._latest_close(p.code, target_date) or p.market_price or p.avg_cost
            value = mp * p.quantity
            pl = (mp - p.avg_cost) * p.quantity
            pl_pct = (mp - p.avg_cost) / p.avg_cost * 100
            print(
                f"  {p.code:<12} {p.quantity:>5} {p.avg_cost:>10.2f} {mp:>10.2f} "
                f"{pl:>+10.0f} {pl_pct:>+7.2f}%"
            )
            total_value += value
            total_pl += pl
        print(f"  {'-'*55}")
        print(f"  合計: 評価額={total_value:,.0f} 損益={total_pl:+,.0f}")

    def run(self, target_date: Optional[str] = None):
        if target_date is None:
            target_date = self._today()

        logger.info("=" * 60)
        logger.info("Paper Trade Daily Run: %s  strategy=%s", target_date, self.strategy_name)
        logger.info("=" * 60)

        positions = self.vtm.get_positions(self.strategy_name)
        pending = self.vtm.get_pending_orders(self.strategy_name)
        cash = self.vtm.get_cash(self.strategy_name)
        logger.info(
            "Status: %d positions, %d pending, cash=%.0f",
            len(positions), len(pending), cash,
        )

        exits = self.check_exits(target_date)
        logger.info("Phase 1 exits: %d", exits)

        fills = self.process_fills_sells_first(target_date)
        logger.info("Phase 2 fills: %d", fills)

        buys = self.check_buys(target_date)
        logger.info("Phase 3 buys: %d", buys)

        self.update_prices(target_date)

        result = self.save_equity(target_date)
        if result:
            logger.info(
                "Equity: cash=%.0f pos=%.0f total=%.0f",
                result.get("cash", 0), result.get("position_value", 0),
                result.get("total_equity", 0),
            )

        self.cleanup_trailing()

        perf = self.vtm.get_strategy_performance(self.strategy_name)
        logger.info(
            "Summary: equity=%.0f return=%.2f%% positions=%d",
            perf["total_equity"], perf["return_pct"], perf["position_count"],
        )
        return perf


def main():
    parser = argparse.ArgumentParser(description="US株ペーパートレード日次実行")
    parser.add_argument("--date", type=str, default=None, help="対象日 (YYYY-MM-DD)")
    parser.add_argument("--strategy", type=str, default="momentum", help="戦略名")
    parser.add_argument("--show", action="store_true", help="ポジション一覧を表示")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = load_config()
    pt = PaperTradeDaily(config, strategy_name=args.strategy)
    pt.run(target_date=args.date)

    if args.show:
        target = args.date or pt._today()
        print("\n現在のポジション:")
        pt.show_positions(target)

    perf = pt.vtm.get_strategy_performance(args.strategy)
    print("\nパフォーマンスサマリー:")
    print(f"  総資産: ${perf['total_equity']:,.0f}")
    print(f"  現金: ${perf['cash']:,.0f}")
    print(f"  ポジション評価額: ${perf['position_value']:,.0f}")
    print(f"  純損益: ${perf['total_pl']:+,.0f}")
    print(f"  リターン: {perf['return_pct']:+.2f}%")
    print(f"  ポジション数: {perf['position_count']}")


if __name__ == "__main__":
    main()
