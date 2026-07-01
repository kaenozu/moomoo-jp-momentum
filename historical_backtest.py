"""
履歴バックテストCLI

ファイルパス: historical_backtest.py
何をするか: 過去データを使ったバックテストを実行する
なぜ存在するか: ルックアヘッドなしで戦略の過去パフォーマンスを検証するため
関連ファイル: src/backtest_runner.py, src/config.py

使い方:
    python historical_backtest.py --from 2026-03-01 --to 2026-06-30 --strategy momentum
    python historical_backtest.py --from 2026-03-01 --to 2026-06-30 --strategy all
    python historical_backtest.py --report --run-id 1
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import sqlite3

from src.config import load_config
from src.backtest_runner import BacktestRunner
from src.strategies import StrategyRegistry


def display_run_summary(conn, run_id: int):
    row = conn.execute("SELECT * FROM backtest_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        print("Run not found")
        return
    print()
    print("=" * 60)
    print("バックテスト結果")
    print("=" * 60)
    print(f"  戦略: {row['strategy_name']}")
    print(f"  期間: {row['start_date']}〜{row['end_date']}")
    print(f"  初期資金: {row['initial_cash']:,.0f}円")
    print(f"  最終資産: {row['final_equity']:,.0f}円" if row['final_equity'] else "")
    print(f"  総リターン: {row['total_return_pct']:.2f}%" if row['total_return_pct'] else "")
    if row['benchmark_2559_return']:
        print(f"  2559リターン: {row['benchmark_2559_return']:.2f}%")
        print(f"  2559超過: {row['excess_vs_2559']:.2f}%")
    if row['benchmark_1306_return']:
        print(f"  1306リターン: {row['benchmark_1306_return']:.2f}%")
        print(f"  1306超過: {row['excess_vs_1306']:.2f}%")

    # trade count
    cnt = conn.execute("SELECT COUNT(*) FROM backtest_fills WHERE run_id=? AND side='SELL'", (run_id,)).fetchone()[0]
    print(f"  クローズドトレード: {cnt}件")


def run_backtest(config, strategy_name: str, start_date: str, end_date: str) -> int:
    runner = BacktestRunner(config)
    run_id = runner.run(strategy_name, start_date, end_date)
    return run_id


def export_results(config, run_id: int, output_dir: str = "reports"):
    import sqlite3
    from datetime import datetime as dt

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.database_path))
    conn.row_factory = sqlite3.Row
    date_str = dt.now().strftime("%Y%m%d")
    run = conn.execute("SELECT * FROM backtest_runs WHERE id=?", (run_id,)).fetchone()

    if run:
        pd.DataFrame([dict(zip(run.keys(), run))]).to_csv(f"{output_dir}/backtest_summary_{date_str}.csv", index=False, encoding="utf-8-sig")

    trades = conn.execute("""
        SELECT o.code, o.side, o.signal_date, o.exit_reason, f.price, f.filled_at
        FROM backtest_fills f
        JOIN backtest_orders o ON f.order_id = o.id
        WHERE f.run_id=?
    """, (run_id,)).fetchall()
    if trades:
        pd.DataFrame(trades, columns=["code", "side", "signal_date", "exit_reason", "price", "filled_at"]).to_csv(
            f"{output_dir}/backtest_trades_{date_str}.csv", index=False, encoding="utf-8-sig")

    eq = conn.execute("SELECT date, cash, position_value, total_equity, drawdown_pct, benchmark_2559_value, benchmark_1306_value FROM backtest_equity_curve WHERE run_id=? ORDER BY date", (run_id,)).fetchall()
    if eq:
        pd.DataFrame(eq, columns=["date", "cash", "position_value", "total_equity", "drawdown_pct", "benchmark_2559_value", "benchmark_1306_value"]).to_csv(
            f"{output_dir}/backtest_equity_{date_str}.csv", index=False, encoding="utf-8-sig")

    conn.close()
    print(f"[OK] CSV出力完了: {output_dir}/backtest_summary_{date_str}.csv")


def main():
    parser = argparse.ArgumentParser(description="Moomoo 履歴バックテスト")
    parser.add_argument("--from", dest="from_date", default="2026-03-01", help="開始日")
    parser.add_argument("--to", dest="to_date", default="2026-06-30", help="終了日")
    parser.add_argument("--strategy", default="momentum", help="戦略名（または all）")
    parser.add_argument("--report", action="store_true", help="レポート出力")
    parser.add_argument("--run-id", type=int, help="既存runの結果表示")
    parser.add_argument("--csv", action="store_true", help="CSV出力")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    # 既存runの表示
    if args.run_id:
        with sqlite3.connect(str(config.database_path)) as conn:
            conn.row_factory = sqlite3.Row
            display_run_summary(conn, args.run_id)
            if args.csv:
                export_results(config, args.run_id)
        return 0

    strategies = StrategyRegistry.list_names() if args.strategy == "all" else [args.strategy]

    for s in strategies:
        print(f"\n{'='*60}")
        print(f"バックテスト: {s}")
        print(f"{'='*60}")
        run_id = run_backtest(config, s, args.from_date, args.to_date)
        with sqlite3.connect(str(config.database_path)) as conn:
            conn.row_factory = sqlite3.Row
            display_run_summary(conn, run_id)
        if args.csv:
            export_results(config, run_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
