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
        print(f"  ベンチマークリターン: {run['benchmark_2559_return']:.2f}%")
        print(f"  ベンチマーク超過: {run['excess_vs_2559']:.2f}%")
    if run['benchmark_1306_return']:
        print(f"  副ベンチマーク: {run['benchmark_1306_return']:.2f}%")

    cnt = conn.execute("SELECT COUNT(*) FROM backtest_fills WHERE run_id=? AND side='SELL'", (run_id,)).fetchone()[0]
    print(f"  クローズドトレード: {cnt}件")

    reasons = _exit_reason_stats(conn, run_id)
    if reasons:
        for r in reasons:
            print(f"    exit_reason={r['exit_reason']}: {r['count']}件")


def export_trade_diagnostics(config, run_id: int, label: str = ""):
    """クローズドトレードのentry指標付き詳細CSVと集計"""
    import sqlite3
    from datetime import datetime as dt

    output_dir = Path("reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.database_path))
    date_str = dt.now().strftime("%Y%m%d")
    suffix = f"_{label}" if label else ""

    # BUY fillsとそれに対応するSELL fillsをペアリング
    buys = conn.execute("""
        SELECT f.id, f.order_id, f.code, f.side, f.quantity, f.price, f.filled_at, o.signal_date, o.exit_reason
        FROM backtest_fills f JOIN backtest_orders o ON f.order_id = o.id
        WHERE f.run_id = ? AND f.side = 'BUY' ORDER BY f.filled_at
    """, (run_id,)).fetchall()

    sells = conn.execute("""
        SELECT f.id, f.order_id, f.code, f.side, f.quantity, f.price, f.filled_at, o.exit_reason
        FROM backtest_fills f JOIN backtest_orders o ON f.order_id = o.id
        WHERE f.run_id = ? AND f.side = 'SELL' ORDER BY f.filled_at
    """, (run_id,)).fetchall()

    trades = []
    sell_idx = 0
    for buy in buys:
        if sell_idx >= len(sells):
            break
        sell = sells[sell_idx]
        if buy[2] != sell[2]:
            continue
        sell_idx += 1

        code = buy[2]
        entry_date = buy[6][:10]
        exit_date = sell[6][:10]
        entry_price = buy[5]
        exit_price = sell[5]
        exit_reason = sell[7] or "unknown"  # SELL orderのexit_reason

        try:
            holding_days = (dt.strptime(exit_date, "%Y-%m-%d") - dt.strptime(entry_date, "%Y-%m-%d")).days
        except ValueError:
            holding_days = 0

        return_pct = (exit_price - entry_price) / entry_price * 100

        # entry時点の指標をdaily_barsから計算
        entry_bars = conn.execute(
            "SELECT close, volume FROM daily_bars WHERE code=? AND date <= ? ORDER BY date DESC LIMIT 25",
            (code, entry_date),
        ).fetchall()

        entry_close = entry_bars[0][0] if entry_bars else None
        entry_volume = entry_bars[0][1] if entry_bars else None
        close_20d = [r[0] for r in entry_bars[:20]] if len(entry_bars) >= 20 else []
        vol_20d = [r[1] for r in entry_bars[:20]] if len(entry_bars) >= 20 else []
        ma25 = sum(r[0] for r in entry_bars[:25]) / 25 if len(entry_bars) >= 25 else None

        high_20d = max(close_20d) if close_20d else None
        avg_vol_20d = sum(vol_20d) / len(vol_20d) if vol_20d else None
        vol_ratio = entry_volume / avg_vol_20d if entry_volume and avg_vol_20d else None
        high_20d_dist = (entry_close - high_20d) / high_20d * 100 if entry_close and high_20d else None
        close_vs_ma25 = entry_close - ma25 if entry_close and ma25 else None

        # 5日前/20日前のclose
        close_5d_ago = entry_bars[4][0] if len(entry_bars) >= 5 else None
        close_20d_ago = entry_bars[19][0] if len(entry_bars) >= 20 else None
        ret_5d = (entry_close - close_5d_ago) / close_5d_ago * 100 if entry_close and close_5d_ago else None
        ret_20d = (entry_close - close_20d_ago) / close_20d_ago * 100 if entry_close and close_20d_ago else None

        # benchmark比較（configのprimary benchmarkを使用）
        import yaml
        with open('config.yaml', encoding='utf-8') as f:
            _cfg = yaml.safe_load(f)
        _bm_code = _cfg.get('signals', {}).get('relative_strength', {}).get('benchmark_code', 'US.SPY')
        bm_5d_ago = conn.execute(
            "SELECT close FROM daily_bars WHERE code=? AND date <= ? ORDER BY date DESC LIMIT 5",
            (_bm_code, entry_date),
        ).fetchall()
        bm_close_5d = bm_5d_ago[-1][0] if len(bm_5d_ago) >= 5 else None
        bm_close_now = bm_5d_ago[0][0] if bm_5d_ago else None
        ret_5d_bm = (bm_close_now - bm_close_5d) / bm_close_5d * 100 if bm_close_5d and bm_close_now else None
        ret_vs_bm = (ret_5d or 0) - (ret_5d_bm or 0) if ret_5d is not None and ret_5d_bm is not None else None

        # stop_lossまでの日数
        days_to_sl = 0
        if exit_reason == "stop_loss":
            stop_price = entry_price * 0.95
            for j, bar in enumerate(entry_bars):
                if j == 0:
                    continue
                if bar[0] <= stop_price:
                    days_to_sl = j
                    break

        trades.append({
            "code": code,
            "entry_date": entry_date,
            "exit_date": exit_date,
            "holding_days": holding_days,
            "entry_price": round(entry_price, 1),
            "exit_price": round(exit_price, 1),
            "return_pct": round(return_pct, 2),
            "exit_reason": exit_reason,
            "entry_return_5d": round(ret_5d, 2) if ret_5d else None,
            "entry_return_20d": round(ret_20d, 2) if ret_20d else None,
            "entry_return_5d_vs_benchmark": round(ret_vs_bm, 2) if ret_vs_bm else None,
            "entry_volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
            "entry_high_20d_distance": round(high_20d_dist, 2) if high_20d_dist else None,
            "entry_close_vs_ma25": round(close_vs_ma25, 2) if close_vs_ma25 else None,
            "days_to_stop_loss": days_to_sl,
        })

    if not trades:
        conn.close()
        return

    # 診断CSV
    import pandas as pd
    df = pd.DataFrame(trades)
    diag_file = output_dir / f"backtest_trade_diagnostics_{date_str}{suffix}.csv"
    df.to_csv(diag_file, index=False, encoding="utf-8-sig")
    print(f"[OK] {diag_file}")

    # exit_reason別集計
    print("\n  --- stop_loss分析 ---")
    sl_trades = [t for t in trades if t["exit_reason"] == "stop_loss"]
    mc_trades = [t for t in trades if t["exit_reason"] == "ma25_cross"]
    if sl_trades:
        avg_ret_vs_bm = sum(t["entry_return_5d_vs_benchmark"] or 0 for t in sl_trades) / len(sl_trades)
        avg_vol = sum(t["entry_volume_ratio"] or 0 for t in sl_trades) / len(sl_trades) if sl_trades else 0
        avg_days = sum(t["holding_days"] for t in sl_trades) / len(sl_trades)
        early_sl = sum(1 for t in sl_trades if t["holding_days"] <= 3)
        late_sl = sum(1 for t in sl_trades if t["holding_days"] >= 10)
        print(f"    stop_loss: {len(sl_trades)}件")
        print(f"      avg entry return_5d_vs_benchmark: {avg_ret_vs_bm:.2f}%")
        print(f"      avg entry volume_ratio: {avg_vol:.2f}")
        print(f"      avg holding_days: {avg_days:.1f}日")
        print(f"      1-3日以内: {early_sl}件")
        print(f"      10日以上保有後: {late_sl}件")

    if mc_trades:
        avg_days_mc = sum(t["holding_days"] for t in mc_trades) / len(mc_trades)
        print(f"    ma25_cross: {len(mc_trades)}件")
        print(f"      avg holding_days: {avg_days_mc:.1f}日")

    # exit_reason別集計CSV
    summary = []
    for reason in set(t["exit_reason"] for t in trades):
        subset = [t for t in trades if t["exit_reason"] == reason]
        summary.append({
            "exit_reason": reason,
            "count": len(subset),
            "avg_holding_days": round(sum(t["holding_days"] for t in subset) / len(subset), 1),
            "avg_return_pct": round(sum(t["return_pct"] for t in subset) / len(subset), 2),
            "total_return_pct": round(sum(t["return_pct"] for t in subset), 2),
        })
    summary_file = output_dir / f"backtest_trade_summary_{date_str}{suffix}.csv"
    pd.DataFrame(summary).to_csv(summary_file, index=False, encoding="utf-8-sig")
    print(f"[OK] {summary_file}")
    conn.close()


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
            export_trade_diagnostics(config, run_id, label=s)

    if args.csv and args.strategy == "all":
        conn = sqlite3.connect(str(config.database_path))
        export_combined(conn, run_ids)
        for rid, s in zip(run_ids, strategies):
            export_trade_diagnostics(config, rid, label=s)
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
