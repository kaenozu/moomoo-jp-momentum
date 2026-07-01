"""
バックテストスクリプト

ファイルパス: backtest.py
何をするか: 過去の日足データを使って一括バックテストを実行する
なぜ存在するか: 30〜60営業日分の検証を一気に行うため
関連ファイル: src/indicators.py, src/signals.py, src/virtual_trade.py

使い方:
    python backtest.py
    python backtest.py --from 2026-01-01 --to 2026-06-30
    python backtest.py --strategy momentum
    python backtest.py --report
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import sqlite3

from src.config import load_config
from src.data_store import DataStore
from src.indicators import calculate_indicators, indicators_to_dataframe
from src.signals import detect_signals_batch
from src.scoring import score_batch
from src.virtual_trade import VirtualTradeManager
from src.screener import Screener
from src.strategies import StrategyRegistry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def get_trading_days(conn, code: str = "JP.7203",
                     start_date: str = "2026-01-01",
                     end_date: str = "2026-06-30") -> list[str]:
    """取引日のリストを取得"""
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_bars WHERE code = ? AND date >= ? AND date <= ? ORDER BY date",
        (code, start_date, end_date),
    ).fetchall()
    return [r[0] for r in rows]


def get_bars_up_to(conn, code: str, date: str, limit: int = 250) -> pd.DataFrame:
    """指定日以前の日足を取得（ルックアヘッド防止）"""
    df = pd.read_sql_query(
        "SELECT * FROM daily_bars WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT ?",
        conn, params=[code, date, limit],
    )
    if not df.empty:
        df = df.rename(columns={"date": "time_key"})
    return df


def run_backtest(
    config,
    start_date: str = "2026-03-01",
    end_date: str = "2026-06-30",
    strategy_name: str = "momentum",
) -> dict:
    """バックテストを実行"""
    db_path = Path(config.database_path)
    conn = sqlite3.connect(str(db_path))

    symbols = DataStore(config).get_enabled_symbols()
    codes = [s.code for s in symbols]
    symbols_info = {s.code: s.name for s in symbols}
    trading_days = get_trading_days(conn, start_date=start_date, end_date=end_date)

    logger.info("バックテスト開始: %s〜%s (%d営業日, %d銘柄, 戦略=%s)",
                start_date, end_date, len(trading_days), len(codes), strategy_name)

    # 戦略を取得
    strategy = StrategyRegistry.get(strategy_name, config)

    manager = VirtualTradeManager(config)
    results = {
        "total_trading_days": len(trading_days),
        "total_signals": 0,
        "buy_signals": 0,
        "virtual_orders": 0,
        "fills": 0,
        "exits": 0,
        "processed_days": 0,
    }

    for i, day in enumerate(trading_days):
        logger.info("[%d/%d] %s 処理中...", i + 1, len(trading_days), day)

        for code in codes:
            df = get_bars_up_to(conn, code, day)
            if df.empty or len(df) < 25:
                continue

            ind = calculate_indicators(df, code, symbols_info.get(code))
            if ind is None:
                continue

            # 戦略で評価
            result = strategy.evaluate(ind)
            if result.signal_type == "BUY_CANDIDATE":
                results["buy_signals"] += 1
                results["total_signals"] += 1

                # 仮想注文
                order = manager.place_order(
                    strategy_name=strategy_name,
                    code=code,
                    side="BUY",
                    quantity=1,
                    order_type="MARKET_SIM",
                    submitted_at=day,
                )
                if order:
                    results["virtual_orders"] += 1

        # 約定処理（翌営業日用のデータで）
        fills = manager.process_fills(strategy_name, day)
        results["fills"] += len(fills)

        # 売却候補生成
        exits = manager.generate_exits(strategy_name, day)
        results["exits"] += len(exits)

        results["processed_days"] += 1

    conn.close()
    logger.info("バックテスト完了")
    return results


def display_results(results: dict):
    print()
    print("=" * 60)
    print("バックテスト結果")
    print("=" * 60)
    print(f"  対象期間: {results.get('start_date', '?')} 〜 {results.get('end_date', '?')}")
    print(f"  処理営業日: {results['processed_days']}日")
    print(f"  買いシグナル: {results['buy_signals']}件")
    print(f"  仮想注文: {results['virtual_orders']}件")
    print(f"  約定: {results['fills']}件")
    print(f"  売却: {results['exits']}件")


def main():
    parser = argparse.ArgumentParser(description="Moomoo バックテスト")
    parser.add_argument("--from", dest="from_date", default="2026-03-01", help="開始日")
    parser.add_argument("--to", dest="to_date", default="2026-06-30", help="終了日")
    parser.add_argument("--strategy", default="momentum", help="戦略名")
    parser.add_argument("--report", action="store_true", help="バックテスト後にレポート出力")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    # 既存の仮想データをリセット（バックテスト前に）
    import sqlite3
    db_path = config.database_path
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM virtual_orders")
        conn.execute("DELETE FROM virtual_fills")
        conn.execute("DELETE FROM virtual_positions")
        conn.execute("DELETE FROM virtual_equity_curve WHERE date < '2026-01-01'")
        conn.execute("INSERT OR IGNORE INTO virtual_equity_curve (strategy_name, date, cash, total_equity, daily_return) VALUES (?, '2026-03-01', 100000, 100000, 0)",
                     (args.strategy,))

    results = run_backtest(config, args.from_date, args.to_date, args.strategy)
    results["start_date"] = args.from_date
    results["end_date"] = args.to_date
    display_results(results)

    if args.report:
        from virtual_report import display_report, export_csv, export_html
        from src.virtual_report import VirtualReportGenerator
        from datetime import datetime as dt

        gen = VirtualReportGenerator(config)
        report = gen.generate(args.strategy, args.from_date, args.to_date)
        display_report(report)

        date_str = dt.now().strftime("%Y%m%d")
        export_csv(report, date_str=f"backtest_{date_str}")
        export_html(report, date_str=f"backtest_{date_str}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
