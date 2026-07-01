"""
履歴バックテストCLI

ファイルパス: historical_backtest.py
何をするか: 過去データを使ったバックテストを実行・比較する
関連ファイル: src/backtest_runner.py, src/config.py

使い方:
    python historical_backtest.py --from 2026-03-01 --to 2026-06-30 --strategy momentum
    python historical_backtest.py --from 2026-03-01 --to 2026-06-30 --strategy all --csv
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


def _benchmark_return(conn, code: str, start: str, end: str):
    rows = conn.execute(
        "SELECT close FROM daily_bars WHERE code = ? AND date >= ? AND date <= ? ORDER BY date",
        (code, start, end),
    ).fetchall()
    if len(rows) < 2:
        return None
    return (rows[-1][0] - rows[0][0]) / rows[0][0] * 100


def _exit_reason_stats(conn, run_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT exit_reason, COUNT(*) as cnt, SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END) as sells FROM backtest_orders WHERE run_id=? AND exit_reason IS NOT NULL GROUP BY exit_reason",
        (run_id,),
    ).fetchall()
    return [{"exit_reason": r[0], "count": r[1]} for r in rows]


def display_run_summary(conn, run_id: int):
    conn.row_factory = sqlite3.Row
    run = conn.execute("SELECT * FROM backtest_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        print("Run not found")
        return

    print(f"\n  戦略: {run['strategy_name']}")
    print(f"  期間: {run['start_date']}〜{run['end_date']}")
    print(f"  初期資金: {run['initial_cash']:,.0f}円")
    print(f"  最終資産: {run['final_equity']:,.0f}円")
    print(f"  総リターン: {run['total_return_pct']:.2f}%")
    if run['benchmark_2559_return']:
        print(f"  2559リターン: {run['benchmark_2559_return']:.2f}%")
        print(f"  2559超過: {run['excess_vs_2559']:.2f}%")
    if run['benchmark_1306_return']:
        print(f"  1306リターン: {run['benchmark_1306_return']:.2f}%")
        print(f"  1306超過: {run['excess_vs_1306']:.2f}%")

    cnt = conn.execute("SELECT COUNT(*) FROM backtest_fills WHERE run_id=? AND side='SELL'", (run_id,)).fetchone()[0]
    print(f"  クローズドトレード: {cnt}件")

    reasons = _exit_reason_stats(conn, run_id)
    if reasons:
        for r in reasons:
            print(f"    exit_reason={r['exit_reason']}: {r['count']}件")


def export_results(config, run_id: int, label: str = ""):
    import sqlite3
    from datetime import datetime as dt

    output_dir = Path("reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.database_path))
    date_str = dt.now().strftime("%Y%m%d")
    suffix = f"_{label}" if label else ""
    run = conn.execute("SELECT * FROM backtest_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        conn.close()
        return

    summary_file = output_dir / f"backtest_summary_{date_str}{suffix}.csv"
    pd.DataFrame([{
        "strategy_name": run[2], "start_date": run[3], "end_date": run[4],
        "initial_cash": run[5], "final_equity": run[6], "total_return_pct": run[7],
        "max_drawdown_pct": run[8], "win_rate": run[9], "profit_factor": run[10],
        "trade_count": run[11], "benchmark_2559_return": run[12], "excess_vs_2559": run[13],
        "benchmark_1306_return": run[14], "excess_vs_1306": run[15],
    }]).to_csv(summary_file, index=False, encoding="utf-8-sig")
    print(f"[OK] {summary_file}")

    trades = conn.execute("""
        SELECT o.code, o.side, o.signal_date, o.exit_reason, f.price, f.filled_at
        FROM backtest_fills f JOIN backtest_orders o ON f.order_id = o.id WHERE f.run_id=?
    """, (run_id,)).fetchall()
    if trades:
        trade_file = output_dir / f"backtest_trades_{date_str}{suffix}.csv"
        pd.DataFrame(trades, columns=["code", "side", "signal_date", "exit_reason", "price", "filled_at"]).to_csv(
            trade_file, index=False, encoding="utf-8-sig")
        print(f"[OK] {trade_file}")

    eq = conn.execute("SELECT date, total_equity, drawdown_pct FROM backtest_equity_curve WHERE run_id=? ORDER BY date", (run_id,)).fetchall()
    if eq:
        eq_file = output_dir / f"backtest_equity_{date_str}{suffix}.csv"
        pd.DataFrame(eq, columns=["date", "total_equity", "drawdown_pct"]).to_csv(eq_file, index=False, encoding="utf-8-sig")
        print(f"[OK] {eq_file}")

    conn.close()


def export_combined(conn, run_ids: list[int], label: str = "all"):
    output_dir = Path("reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")

    rows = []
    for rid in run_ids:
        r = conn.execute("SELECT * FROM backtest_runs WHERE id=?", (rid,)).fetchone()
        if r:
            rows.append({
                "strategy_name": r[2], "start_date": r[3], "end_date": r[4],
                "initial_cash": r[5], "final_equity": r[6], "total_return_pct": r[7],
                "max_drawdown_pct": r[8], "trade_count": r[11],
                "benchmark_2559_return": r[12], "excess_vs_2559": r[13],
                "benchmark_1306_return": r[14], "excess_vs_1306": r[15],
            })

    if rows:
        filepath = output_dir / f"backtest_summary_{date_str}_{label}.csv"
        pd.DataFrame(rows).to_csv(filepath, index=False, encoding="utf-8-sig")
        print(f"[OK] {filepath}")

    # 戦略別exit_reason集計
    all_reasons = []
    for rid in run_ids:
        r = conn.execute("SELECT strategy_name FROM backtest_runs WHERE id=?", (rid,)).fetchone()
        if not r:
            continue
        sn = r[0]
        for row in conn.execute(
            "SELECT exit_reason, COUNT(*) as cnt FROM backtest_orders WHERE run_id=? AND exit_reason IS NOT NULL GROUP BY exit_reason", (rid,)
        ).fetchall():
            all_reasons.append({"strategy_name": sn, "exit_reason": row[0], "count": row[1]})

    if all_reasons:
        reason_file = output_dir / f"backtest_exit_reason_{date_str}_{label}.csv"
        pd.DataFrame(all_reasons).to_csv(reason_file, index=False, encoding="utf-8-sig")
        print(f"[OK] {reason_file}")

    # 戦略別equity curve比較
    eq_rows = []
    for rid in run_ids:
        r = conn.execute("SELECT strategy_name FROM backtest_runs WHERE id=?", (rid,)).fetchone()
        if not r:
            continue
        sn = r[0]
        for row in conn.execute(
            "SELECT date, total_equity FROM backtest_equity_curve WHERE run_id=? ORDER BY date", (rid,)
        ).fetchall():
            eq_rows.append({"strategy_name": sn, "date": row[0], "total_equity": row[1]})
    if eq_rows:
        eq_file = output_dir / f"backtest_equity_{date_str}_{label}.csv"
        pd.DataFrame(eq_rows).to_csv(eq_file, index=False, encoding="utf-8-sig")
        print(f"[OK] {eq_file}")


def run_backtest(config, strategy_name: str, start_date: str, end_date: str) -> int:
    runner = BacktestRunner(config)
    return runner.run(strategy_name, start_date, end_date)


def main():
    parser = argparse.ArgumentParser(description="Moomoo 履歴バックテスト")
    parser.add_argument("--from", dest="from_date", default="2026-03-01", help="開始日")
    parser.add_argument("--to", dest="to_date", default="2026-06-30", help="終了日")
    parser.add_argument("--strategy", default="momentum", help="戦略名（または all）")
    parser.add_argument("--csv", action="store_true", help="CSV出力")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    strategies = StrategyRegistry.list_names() if args.strategy == "all" else [args.strategy]
    run_ids = []

    for s in strategies:
        print(f"\n{'='*60}")
        print(f"バックテスト: {s}")
        print(f"{'='*60}")
        run_id = run_backtest(config, s, args.from_date, args.to_date)
        run_ids.append(run_id)

        conn = sqlite3.connect(str(config.database_path))
        display_run_summary(conn, run_id)
        conn.close()

        if args.csv and args.strategy != "all":
            export_results(config, run_id, label=s)

    if args.csv and args.strategy == "all":
        conn = sqlite3.connect(str(config.database_path))
        export_combined(conn, run_ids)
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
