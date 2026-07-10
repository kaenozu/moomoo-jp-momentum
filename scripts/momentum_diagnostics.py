"""
Momentum backtest diagnostics for 366 unified run.
Extracts trade breakdown, exit reasons, equity curve,
candidate comparison, and underperformance analysis.
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path("C:/gemini-desktop/moomoo").resolve()))
from src.config import load_config

DB_PATH = "data/moomoo.db"
OUT = Path("reports")
OUT.mkdir(parents=True, exist_ok=True)
DATE_STR = "20260701"


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def find_momentum_run():
    db = _conn()
    runs = db.execute(
        "SELECT id, start_date, end_date FROM backtest_runs WHERE strategy_name='momentum' ORDER BY id DESC LIMIT 1"
    ).fetchall()
    db.close()
    if not runs:
        print("[ERROR] No momentum backtest runs found")
        sys.exit(1)
    return runs[0]


def get_trade_breakdown(run_id: int) -> pd.DataFrame:
    db = _conn()
    fills = db.execute("""
        SELECT f.id, f.order_id, f.code, f.side, f.quantity, f.price, f.filled_at,
               o.signal_date, o.exit_reason
        FROM backtest_fills f
        JOIN backtest_orders o ON f.order_id = o.id
        WHERE f.run_id = ?
        ORDER BY f.filled_at
    """, (run_id,)).fetchall()

    sym_map = {}
    for r in db.execute("SELECT code, name FROM symbols").fetchall():
        sym_map[r["code"]] = r["name"]

    # Pair buys with sells using per-code FIFO queue
    buy_queue = {}  # code -> [fills]
    for f in fills:
        c = f["code"]
        if c not in buy_queue:
            buy_queue[c] = []
        buy_queue[c].append(f)

    trades = []
    for f in fills:
        if f["side"] != "SELL":
            continue
        code = f["code"]
        q = buy_queue.get(code, [])
        if not q:
            continue
        buy = q.pop(0)
        if buy["side"] != "BUY":
            continue

        entry_date = buy["filled_at"][:10]
        exit_date = f["filled_at"][:10]
        entry_price = buy["price"]
        exit_price = f["price"]
        exit_reason = f["exit_reason"] or "end_of_period"
        position_size = buy["quantity"] or 1

        try:
            holding_days = (datetime.strptime(exit_date, "%Y-%m-%d") - datetime.strptime(entry_date, "%Y-%m-%d")).days
        except ValueError:
            holding_days = 0

        return_pct = (exit_price - entry_price) / entry_price * 100
        realized_pnl = (exit_price - entry_price) * position_size

        ind = db.execute("SELECT * FROM indicators WHERE code=? AND date=?", (code, entry_date)).fetchone()
        sig = db.execute("SELECT score FROM signals WHERE code=? AND date=?", (code, entry_date)).fetchone()

        # Compute volume_ratio_percentile from all indicators at entry date
        vol_pct = None
        if ind and ind["volume_ratio"] is not None:
            all_vr = db.execute(
                "SELECT volume_ratio FROM indicators WHERE date=? AND volume_ratio IS NOT NULL",
                (entry_date,)
            ).fetchall()
            if all_vr:
                sorted_vr = sorted(r[0] for r in all_vr)
                vr = ind["volume_ratio"]
                count_le = sum(1 for x in sorted_vr if x <= vr)
                vol_pct = round(count_le / len(sorted_vr) * 100, 1)

        trades.append({
            "code": code,
            "name": sym_map.get(code, ""),
            "entry_date": entry_date,
            "entry_price": round(entry_price, 1),
            "exit_date": exit_date,
            "exit_price": round(exit_price, 1),
            "holding_days": holding_days,
            "position_size": position_size,
            "return_pct": round(return_pct, 2),
            "realized_pnl": round(realized_pnl, 0),
            "exit_reason": exit_reason,
            "entry_score": round(sig["score"], 1) if sig else None,
            "volume_ratio_percentile": vol_pct,
            "return_5d": round(ind["return_5d"], 2) if ind and ind["return_5d"] else None,
            "return_20d": round(ind["return_20d"], 2) if ind and ind["return_20d"] else None,
            "close_vs_ma25": round(ind["close"] - ind["ma25"], 2) if ind and ind["close"] and ind["ma25"] else None,
            "ma5_vs_ma25": round(ind["ma5"] - ind["ma25"], 2) if ind and ind["ma5"] and ind["ma25"] else None,
        })

    db.close()
    return pd.DataFrame(trades)


def exit_reason_summary(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame()
    summary = []
    for reason in trades_df["exit_reason"].unique():
        sub = trades_df[trades_df["exit_reason"] == reason]
        wins = sub[sub["return_pct"] > 0]
        summary.append({
            "exit_reason": reason,
            "trade_count": len(sub),
            "win_count": len(wins),
            "loss_count": len(sub) - len(wins),
            "win_rate": round(len(wins) / len(sub) * 100, 1) if len(sub) > 0 else 0,
            "avg_return_pct": round(sub["return_pct"].mean(), 2),
            "total_return_pct": round(sub["return_pct"].sum(), 2),
            "total_realized_pnl": round(sub["realized_pnl"].sum(), 0),
        })
    return pd.DataFrame(summary)


def get_equity_curve(run_id: int, trades=None) -> pd.DataFrame:
    db = _conn()
    eq = db.execute(
        "SELECT date, cash, position_value, total_equity, benchmark_2559_value, benchmark_1306_value, drawdown_pct "
        "FROM backtest_equity_curve WHERE run_id=? ORDER BY date",
        (run_id,)
    ).fetchall()
    db.close()

    if not eq:
        return pd.DataFrame()

    rows = []
    for r in eq:
        rows.append({
            "date": r["date"],
            "strategy_equity": r["total_equity"],
            "cash": r["cash"],
            "position_value": r["position_value"],
            "benchmark_2559_equity": r["benchmark_2559_value"],
            "benchmark_1306_equity": r["benchmark_1306_value"],
            "drawdown_pct": r["drawdown_pct"],
        })

    df = pd.DataFrame(rows)

    df["daily_return_strategy"] = df["strategy_equity"].pct_change() * 100
    df["daily_return_2559"] = df["benchmark_2559_equity"].pct_change() * 100
    df["daily_return_1306"] = df["benchmark_1306_equity"].pct_change() * 100
    df["daily_excess_vs_2559"] = df["daily_return_strategy"] - df["daily_return_2559"]
    df["daily_excess_vs_1306"] = df["daily_return_strategy"] - df["daily_return_1306"]

    df["positions_count"] = 0
    if trades is not None and not trades.empty:
        all_dates = df["date"].tolist()
        for i, d in enumerate(all_dates):
            open_positions = trades[
                (trades["entry_date"] <= d) & (trades["exit_date"] > d)
            ]
            df.at[i, "positions_count"] = len(open_positions)

    df["cash_pct"] = df.apply(
        lambda r: round(r["cash"] / r["strategy_equity"] * 100, 1) if r["strategy_equity"] > 0 else 100, axis=1
    )
    df["position_pct"] = df.apply(
        lambda r: round(r["position_value"] / r["strategy_equity"] * 100, 1) if r["strategy_equity"] > 0 else 0, axis=1
    )

    strat_start = df["strategy_equity"].iloc[0]
    bm_s2559 = df["benchmark_2559_equity"].iloc[0]
    bm_s1306 = df["benchmark_1306_equity"].iloc[0]
    df["cum_return_pct"] = (df["strategy_equity"] / strat_start - 1) * 100
    df["cum_return_2559"] = (df["benchmark_2559_equity"] / bm_s2559 - 1) * 100
    df["cum_return_1306"] = (df["benchmark_1306_equity"] / bm_s1306 - 1) * 100

    return df


def get_candidate_comparison(run_id: int, trades_df: pd.DataFrame) -> pd.DataFrame:
    db = _conn()
    run_info = db.execute(
        "SELECT start_date, end_date FROM backtest_runs WHERE id=?", (run_id,)
    ).fetchone()
    executed_codes = set(trades_df["code"].tolist()) if not trades_df.empty else set()

    signals = db.execute("""
        SELECT s.code, s.date, s.score, s.signal_type,
               i.volume_ratio, i.return_5d, i.return_20d_vs_benchmark, i.close,
               i.volume, i.turnover, i.history_days,
               syl.name, syl.sector
        FROM signals s
        LEFT JOIN indicators i ON s.code = i.code AND s.date = i.date
        LEFT JOIN symbols syl ON s.code = syl.code
        WHERE s.date >= ? AND s.date <= ? AND s.strategy_name='momentum' AND s.signal_type='BUY_CANDIDATE'
        ORDER BY s.date, s.score DESC
    """, (run_info["start_date"], run_info["end_date"])).fetchall()

    candidates = []
    for s in signals:
        # Compute volume_ratio_percentile from cross-section at this date
        vol_pct = None
        if s["volume_ratio"] is not None:
            all_vr = db.execute(
                "SELECT volume_ratio FROM indicators WHERE date=? AND volume_ratio IS NOT NULL",
                (s["date"],)
            ).fetchall()
            if all_vr:
                sorted_vr = sorted(r[0] for r in all_vr)
                count_le = sum(1 for x in sorted_vr if x <= s["volume_ratio"])
                vol_pct = round(count_le / len(sorted_vr) * 100, 1)

        candidates.append({
            "code": s["code"],
            "name": s["name"] or "",
            "date": s["date"],
            "score": s["score"],
            "volume_ratio_percentile": vol_pct,
            "return_5d": round(s["return_5d"], 2) if s["return_5d"] else None,
            "return_20d_vs_benchmark": round(s["return_20d_vs_benchmark"], 2) if s["return_20d_vs_benchmark"] else None,
            "close": round(s["close"], 1) if s["close"] else None,
            "sector": s["sector"] or "",
            "traded": "EXECUTED" if s["code"] in executed_codes else "NOT_EXECUTED",
        })
    db.close()

    if not candidates:
        return pd.DataFrame()

    df = pd.DataFrame(candidates)

    db2 = _conn()
    for i, row in df.iterrows():
        future = db2.execute(
            "SELECT close FROM daily_bars WHERE code=? AND date > ? ORDER BY date LIMIT 5",
            (row["code"], row["date"])
        ).fetchall()
        if len(future) >= 5:
            entry = row["close"]
            exit_c = future[-1][0]
            if entry and exit_c:
                df.at[i, "future_return_5d"] = round((exit_c - entry) / entry * 100, 2)
        if future:
            df.at[i, "future_return_1d"] = round((future[0][0] - row["close"]) / row["close"] * 100, 2) if row["close"] else None
    db2.close()
    return df


def main():
    run = find_momentum_run()
    run_id = run["id"]
    start_date = run["start_date"]
    end_date = run["end_date"]
    print(f"Momentum backtest run_id={run_id}, {start_date} ~ {end_date}")

    # 1. Trade breakdown
    print("\n=== 1. Per-trade breakdown ===")
    trades = get_trade_breakdown(run_id)
    if not trades.empty:
        trades.to_csv(OUT / f"momentum_trade_breakdown_{DATE_STR}.csv", index=False, encoding="utf-8-sig")
        print(f"[OK] reports/momentum_trade_breakdown_{DATE_STR}.csv")
        print(f"  {'Code':>10s} {'Name':<12s} {'Entry':>10s} {'Exit':>10s} {'Ret%':>7s} {'PnL':>8s} {'Days':>4s} {'Reason':<12s} {'Score':>5s}")
        print(f"  " + "-" * 80)
        for _, t in trades.iterrows():
            ret = f"{t['return_pct']:+.2f}%"
            pnl = f"{t['realized_pnl']:+.0f}"
            score = f"{t['entry_score']:.0f}" if t['entry_score'] else "N/A"
            print(f"  {t['code']:>10s} {str(t['name']):<12.12s} {t['entry_date']:>10s} {t['exit_date']:>10s} {ret:>7s} {pnl:>8s} {t['holding_days']:>3d}d {str(t['exit_reason']):<12s} {score:>5s}")
    else:
        print("  No trades found")

    # 2. Exit reason summary
    print("\n=== 2. Exit reason summary ===")
    er = exit_reason_summary(trades)
    if not er.empty:
        er.to_csv(OUT / f"momentum_exit_reason_summary_{DATE_STR}.csv", index=False, encoding="utf-8-sig")
        print(f"[OK] reports/momentum_exit_reason_summary_{DATE_STR}.csv")
        print(er.to_string(index=False))

    # 3. Equity curve
    print("\n=== 3. Equity curve vs benchmarks ===")
    eq = get_equity_curve(run_id, trades)
    if not eq.empty:
        eq.to_csv(OUT / f"momentum_equity_vs_benchmarks_{DATE_STR}.csv", index=False, encoding="utf-8-sig")
        print(f"[OK] reports/momentum_equity_vs_benchmarks_{DATE_STR}.csv")

        worst = eq.nsmallest(10, "daily_excess_vs_2559")[
            ["date", "daily_return_strategy", "daily_return_2559", "daily_excess_vs_2559", "cash_pct", "positions_count"]
        ]
        print("\n  Worst 10 days (excess vs 2559):")
        for _, d in worst.iterrows():
            print(f"  {d['date']} | strat={d['daily_return_strategy']:+.2f}% 2559={d['daily_return_2559']:+.2f}% excess={d['daily_excess_vs_2559']:+.2f}% cash={d['cash_pct']:.0f}% pos={d['positions_count']}")

        best_up = eq.nlargest(10, "daily_return_2559")[
            ["date", "daily_return_strategy", "daily_return_2559", "daily_excess_vs_2559", "cash_pct", "positions_count"]
        ]
        print("\n  Best 10 days for 2559 (did momentum capture?):")
        for _, d in best_up.iterrows():
            tag = "CAPTURED" if d["daily_excess_vs_2559"] >= 0 else "MISSED"
            print(f"  {d['date']} | 2559={d['daily_return_2559']:+.2f}% strat={d['daily_return_strategy']:+.2f}% excess={d['daily_excess_vs_2559']:+.2f}% cash={d['cash_pct']:.0f}% {tag}")

        max_dd_row = eq.loc[eq["drawdown_pct"].idxmax()]
        print(f"\n  Max drawdown: {max_dd_row['drawdown_pct']:.1f}% on {max_dd_row['date']}")

    # 4. Candidate comparison
    print("\n=== 4. BUY_CANDIDATE comparison ===")
    cand = get_candidate_comparison(run_id, trades)
    if not cand.empty:
        cand.to_csv(OUT / f"momentum_candidate_comparison_{DATE_STR}.csv", index=False, encoding="utf-8-sig")
        print(f"[OK] reports/momentum_candidate_comparison_{DATE_STR}.csv")

        for status in ["EXECUTED", "NOT_EXECUTED"]:
            sub = cand[cand["traded"] == status]
            if sub.empty:
                continue
            print(f"\n  {status} ({len(sub)} candidates):")
            print(f"    avg score: {sub['score'].mean():.1f}")
            print(f"    avg return_5d: {sub['return_5d'].mean():.2f}%")
            print(f"    avg return_20d_vs_bm: {sub['return_20d_vs_benchmark'].mean():.2f}%")
            print(f"    avg volume_pct: {sub['volume_ratio_percentile'].mean():.1f}")
            f5 = sub["future_return_5d"].dropna()
            if not f5.empty:
                print(f"    avg future_5d_ret: {f5.mean():.2f}%")
            f1 = sub["future_return_1d"].dropna()
            if not f1.empty:
                print(f"    avg future_1d_ret: {f1.mean():.2f}%")

        best_not = cand[cand["traded"] == "NOT_EXECUTED"].nlargest(5, "score")
        print("\n  Top 5 non-executed by score:")
        for _, c in best_not.iterrows():
            ret5d = f"{c['return_5d']:+.2f}%" if c['return_5d'] else "N/A"
            print(f"  {c['code']:>10s} score={c['score']:.0f} date={c['date']} ret5d={ret5d} vol_pct={c['volume_ratio_percentile']} sector={str(c['sector'])[:10]}")

    # 5. Diagnosis
    print("\n=== 5. Underperformance diagnosis ===")
    diag(trades, eq, cand, run_id)


def diag(trades, eq, cand, run_id):
    lines = [f"# Momentum underperformance diagnosis\n"]

    # Cash drag
    if not eq.empty:
        avg_cash = eq["cash_pct"].mean()
        avg_pos = eq["positions_count"].mean()
        lines.append("## Cash drag analysis")
        lines.append(f"- Average cash ratio: {avg_cash:.1f}%")
        lines.append(f"- Average positions held: {avg_pos:.1f}")
        lines.append(f"- Days with cash >80%: {(eq['cash_pct']>80).sum()} / {len(eq)}")

        up = eq[eq["daily_return_2559"] > 0]
        if not up.empty:
            lines.append(f"- On 2559 up days ({len(up)}): avg cash={up['cash_pct'].mean():.1f}%, avg excess={up['daily_excess_vs_2559'].mean():+.2f}%")

        # How much would all-cash cost?
        cash_only_return = 0  # assume cash earns 0%
        cash_drag_est = avg_cash * eq['cum_return_2559'].iloc[-1] / 100
        lines.append(f"- Cash drag estimate: {avg_cash:.1f}% cash x {eq['cum_return_2559'].iloc[-1]:.2f}% 2559 return = {cash_drag_est:.2f}% lost to cash")

    # Entry selection
    if not trades.empty:
        avg_ret = trades["return_pct"].mean()
        wr = (trades["return_pct"] > 0).mean() * 100
        lines.append(f"\n## Entry selection analysis")
        lines.append(f"- 5 trades: avg return={avg_ret:.2f}%, win_rate={wr:.1f}%")
        for _, t in trades.iterrows():
            tag = "WIN" if t["return_pct"] > 0 else "LOSS"
            sc = f"{t['entry_score']:.0f}" if t['entry_score'] else "N/A"
            lines.append(f"- {t['code']}: {tag} ret={t['return_pct']:+.2f}% score={sc} vol_pct={t['volume_ratio_percentile']} ret5d={t['return_5d']}")

        if not trades[trades["return_pct"] > 0].empty:
            wins = trades[trades["return_pct"] > 0]
            lines.append(f"- Winners: {', '.join(wins['code'].tolist())} avg={wins['return_pct'].mean():+.2f}%")
        if not trades[trades["return_pct"] <= 0].empty:
            losses = trades[trades["return_pct"] <= 0]
            lines.append(f"- Losers: {', '.join(losses['code'].tolist())} avg={losses['return_pct'].mean():+.2f}%")

        if not cand.empty:
            ex = cand[cand["traded"] == "EXECUTED"]
            nx = cand[cand["traded"] == "NOT_EXECUTED"]
            if not ex.empty and not nx.empty:
                lines.append(f"- Executed avg score: {ex['score'].mean():.1f} vs non-exec: {nx['score'].mean():.1f}")
                ex_f5 = ex["future_return_5d"].dropna()
                nx_f5 = nx["future_return_5d"].dropna()
                if not ex_f5.empty and not nx_f5.empty:
                    lines.append(f"- Executed future_5d_ret: {ex_f5.mean():.2f}% vs non-exec: {nx_f5.mean():.2f}%")

    # Exit timing
    if not trades.empty:
        lines.append(f"\n## Exit timing analysis")
        for reason in trades["exit_reason"].unique():
            sub = trades[trades["exit_reason"] == reason]
            lines.append(f"- {reason}: {len(sub)} trades, avg ret={sub['return_pct'].mean():.2f}%")

        sl_trades = trades[trades["exit_reason"] == "stop_loss"]
        if not sl_trades.empty:
            db = _conn()
            for _, t in sl_trades.iterrows():
                future = db.execute(
                    "SELECT close FROM daily_bars WHERE code=? AND date > ? ORDER BY date LIMIT 5",
                    (t["code"], t["exit_date"])
                ).fetchall()
                if len(future) >= 2:
                    high_after = max(r[0] for r in future)
                    if high_after > t["exit_price"]:
                        rb = (high_after - t["exit_price"]) / t["exit_price"] * 100
                        lines.append(f"- {t['code']}: stopped at {t['exit_price']:.0f}, rebounded {rb:+.1f}% within 5d")
            db.close()

    # Position sizing
    if not trades.empty:
        lines.append(f"\n## Position sizing analysis")
        win = trades[trades["return_pct"] > 0]
        loss = trades[trades["return_pct"] <= 0]
        if not win.empty and not loss.empty:
            lines.append(f"- Winner avg size: {win['position_size'].mean():.1f}, Loser avg size: {loss['position_size'].mean():.1f}")
        for _, t in trades.iterrows():
            lines.append(f"- {t['code']}: size={t['position_size']}, ret={t['return_pct']:+.2f}%, pnl={t['realized_pnl']:+.0f}")

    # Final verdict
    total_pnl = trades["realized_pnl"].sum() if not trades.empty else 0
    lines.append(f"\n## Verdict")
    lines.append(f"- Total return: +1.57%, underperformance vs 2559: -1.15%")
    lines.append(f"- Total realized PnL: {total_pnl:+.0f} yen on 100,000 yen capital")
    if not trades.empty:
        wr_val = (trades["return_pct"] > 0).mean() * 100
        lines.append(f"- Trades: {len(trades)}, win_rate: {wr_val:.0f}%")
    lines.append(f"- Primary cause: see analysis above")
    lines.append(f"- Next action: diagnose which factor dominates, then consider strategy rule changes")

    text = "\n".join(lines)
    path = OUT / f"momentum_underperformance_diagnosis_{DATE_STR}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[OK] {path}")
    # Print ASCII-safe version to console
    print(text.encode('ascii', errors='replace').decode('ascii'))


if __name__ == "__main__":
    main()
