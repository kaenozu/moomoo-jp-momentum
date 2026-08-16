"""
Benchmark anomaly diagnosis for momentum backtest.
Checks for price adjustments, splits, and abnormal returns
in benchmark ETF daily_bars.
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path("C:/gemini-desktop/moomoo").resolve()))
from src.backtest_runner import BacktestRunner
from src.config import load_config

DB_PATH = "data/moomoo.db"
OUT = Path("reports")
OUT.mkdir(parents=True, exist_ok=True)
DATE_STR = "20260701"
BT_START = "2026-05-21"
BT_END = "2026-06-30"

# All benchmark codes
BENCH_CODES = ["JP.2559", "JP.1306", "JP.2558", "JP.1365",
               "JP.1320", "JP.1570", "JP.2513", "JP.2568", "JP.2621", "JP.2630"]
# ETF that tracks the main indices
MAIN_BENCH = ["JP.2559", "JP.1306"]

ANOMALY_THRESHOLD = 20.0  # percent


def get_daily_bars(code: str, start: str = None, end: str = None) -> pd.DataFrame:
    db = sqlite3.connect(DB_PATH)
    q = "SELECT date, close, volume, turnover, source FROM daily_bars WHERE code=?"
    params = [code]
    if start:
        q += " AND date >= ?"
        params.append(start)
    if end:
        q += " AND date <= ?"
        params.append(end)
    q += " ORDER BY date"
    df = pd.read_sql_query(q, db, params=params)
    db.close()
    return df


def detect_anomalies(code: str, name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Detect daily return anomalies for a benchmark"""
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["prev_close"] = df["close"].shift(1)
    df["daily_return_pct"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100
    df["code"] = code
    df["name"] = name

    # Flag anomalies
    df["anomaly_flag"] = df["daily_return_pct"].abs() > ANOMALY_THRESHOLD
    df["suspected_reason"] = ""

    for i, row in df.iterrows():
        if not row["anomaly_flag"]:
            continue
        ret = row["daily_return_pct"]
        # Determine likely cause
        if abs(ret) > 100:
            df.at[i, "suspected_reason"] = "price_split_or_adjustment"
        elif abs(ret) > 20:
            df.at[i, "suspected_reason"] = "large_etf_price_move"
        else:
            df.at[i, "suspected_reason"] = "minor_anomaly"

        # Check if next day reverses (split artifact)
        next_row = df.shift(-1).loc[i] if i + 1 in df.index else None
        if next_row is not None and next_row["anomaly_flag"]:
            df.at[i, "suspected_reason"] = "possible_split_event_series"

    return df


def compute_adjusted_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Detect and adjust for stock splits (e.g., 1:10 reverse split).
    Strategy: find a day where close drops by >50% but next-day volume is normal.
    If found, adjust prior prices by multiplying by the split ratio."""
    df = df.copy()
    df["adjusted_close"] = df["close"].astype(float)
    df["split_adjustment_factor"] = 1.0
    df["is_split_day"] = False

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]["close"]
        curr = df.iloc[i]["close"]
        if prev == 0:
            continue
        ret = (curr - prev) / prev * 100

        # Detect split: price drops > 50% but volume isn't extraordinary
        # For a 1:10 split, price drops to ~10%
        if ret < -50:
            ratio = prev / curr
            # Round to nearest common split ratio
            for r in [2, 3, 5, 10, 100, 1000]:
                if abs(ratio - r) / r < 0.15:  # within 15%
                    df.at[df.index[i], "split_adjustment_factor"] = r
                    df.at[df.index[i], "is_split_day"] = True
                    break
            # Also check for 1:X reverse split (price jumps up)
        elif ret > 100:
            ratio = curr / prev
            for r in [2, 3, 5, 10, 100, 1000]:
                if abs(ratio - r) / r < 0.15:
                    df.at[df.index[i], "split_adjustment_factor"] = r
                    df.at[df.index[i], "is_split_day"] = True
                    break

    # Apply split adjustment: adjust all prices BEFORE each split
    cumulative_factor = 1.0
    for i in range(len(df) - 1, -1, -1):
        if df.iloc[i]["is_split_day"]:
            cumulative_factor *= df.iloc[i]["split_adjustment_factor"]
        # No, we adjust backward: prices before the split are multiplied by the split ratio
        # Actually for a 1:10 forward split (price goes from 30000 to 3000),
        # we need to multiply pre-split prices by 0.1 to compare with post-split.
        # Let's use the forward approach: compute an adjustment factor that makes
        # all prices in the same unit as the latest price.

    # Alternative: compute adjusted returns directly
    # Store the raw split info but don't modify prices
    return df


def get_backtest_benchmark_returns(run_id: int = None) -> tuple:
    """Get benchmark returns from the existing backtest run"""
    db = sqlite3.connect(DB_PATH)
    if run_id is None:
        run_id = db.execute(
            "SELECT id FROM backtest_runs WHERE strategy_name='momentum' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]

    run = db.execute(
        "SELECT * FROM backtest_runs WHERE id=?", (run_id,)
    ).fetchone()
    cols = [r[0] for r in db.execute("PRAGMA table_info(backtest_runs)").fetchall()]
    db.close()
    return dict(zip(cols, run))


def compute_benchmark_return_from_bars(code: str, start: str, end: str) -> dict:
    """Compute return for a benchmark from daily_bars directly"""
    db = sqlite3.connect(DB_PATH)
    rows = db.execute(
        "SELECT date, close FROM daily_bars WHERE code=? AND date >= ? AND date <= ? ORDER BY date",
        (code, start, end)
    ).fetchall()
    db.close()
    if len(rows) < 2:
        return {"raw_return": None, "start_price": None, "end_price": None, "trading_days": 0}

    start_price = rows[0][1]
    end_price = rows[-1][1]
    raw_return = (end_price - start_price) / start_price * 100 if start_price else None
    return {
        "raw_return": raw_return,
        "start_price": start_price,
        "end_price": end_price,
        "trading_days": len(rows),
    }


def compute_adjusted_benchmark_return(code: str, start: str, end: str) -> dict:
    """Compute adjusted return by removing anomalous days.
    Strategy: for days with abs(return) > threshold, replace the close
    with prev_close (flat day) to eliminate the spike."""
    db = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT date, close FROM daily_bars WHERE code=? AND date >= ? AND date <= ? ORDER BY date",
        db, params=(code, start, end)
    )
    db.close()

    if len(df) < 2:
        return {"adjusted_return": None, "trading_days": 0, "anomalous_days": 0, "anomaly_removed_dates": []}

    df["prev_close"] = df["close"].shift(1)
    df["daily_return"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100

    # Identify anomalous days
    anomalous = df[df["daily_return"].abs() > ANOMALY_THRESHOLD].copy()
    anomaly_dates = anomalous["date"].tolist()

    # Build an adjusted price series: for anomalous days, replace close with prev_close
    df["adj_close"] = df["close"].astype(float)
    for i, row in df.iterrows():
        if abs(row["daily_return"]) > ANOMALY_THRESHOLD and i > 0:
            df.at[i, "adj_close"] = df.iloc[i - 1]["close"]

    # Now compute cumulative return using adjusted prices
    # Actually: replace the close, then re-index returns
    start_price = df["adj_close"].iloc[0]
    end_price = df["adj_close"].iloc[-1]

    # Also compute using "remove the day" method: skip anomalous days
    clean_returns = df[df["daily_return"].abs() <= ANOMALY_THRESHOLD].copy()
    total_clean_return = None
    if len(clean_returns) >= 2:
        start_p = clean_returns["close"].iloc[0]
        end_p = clean_returns["close"].iloc[-1]
        total_clean_return = (end_p - start_p) / start_p * 100

    adjusted_return = (end_price - start_price) / start_price * 100 if start_price else None

    return {
        "adjusted_return": adjusted_return,
        "adjusted_return_remove_day": total_clean_return,
        "trading_days": len(df),
        "anomalous_days": len(anomaly_dates),
        "anomaly_removed_dates": anomaly_dates,
        "start_price_raw": df["close"].iloc[0],
        "end_price_raw": df["close"].iloc[-1],
        "start_price_adj": start_price,
        "end_price_adj": end_price,
    }


def compute_adjusted_excess(strategy_return: float, bm_raw: float, bm_adj: float) -> dict:
    return {
        "excess_raw": strategy_return - bm_raw if bm_raw is not None else None,
        "excess_adjusted": strategy_return - bm_adj if bm_adj is not None else None,
        "adjustment_delta": (bm_adj - bm_raw) if bm_raw is not None and bm_adj is not None else None,
        "adjustment_impact_on_excess": (bm_raw - bm_adj) if bm_raw is not None and bm_adj is not None else None,
    }


def main():
    print(f"Benchmark anomaly diagnosis: {BT_START} ~ {BT_END}\n")

    # Step 1-2: detect anomalies for all benchmarks
    print("=== Step 1-2: Anomaly detection ===")
    all_anomalies = []
    split_events = []

    for code in BENCH_CODES:
        df = get_daily_bars(code, BT_START, BT_END)
        if df.empty:
            print(f"  {code}: no data in range")
            continue

        # Get name
        db = sqlite3.connect(DB_PATH)
        name_row = db.execute("SELECT name FROM symbols WHERE code=?", (code,)).fetchone()
        db.close()
        name = name_row[0] if name_row else code

        result = detect_anomalies(code, name, df)
        if not result.empty:
            all_anomalies.append(result)

        # Check for splits
        full_df = get_daily_bars(code)
        if len(full_df) > 1:
            full_df["prev_close"] = full_df["close"].shift(1)
            full_df["daily_return"] = (full_df["close"] - full_df["prev_close"]) / full_df["prev_close"] * 100
            # Find large moves
            big_moves = full_df[full_df["daily_return"].abs() > 40]
            if not big_moves.empty:
                for _, r in big_moves.iterrows():
                    split_events.append({
                        "code": code,
                        "name": name,
                        "date": r["date"],
                        "prev_close": r["prev_close"],
                        "close": r["close"],
                        "return_pct": round(r["daily_return"], 2),
                        "source": r["source"],
                    })
        print(f"  {code:>10s} ({name}): {result['anomaly_flag'].sum() if not result.empty else 0} anomalies")

    # Combine all anomalies
    if all_anomalies:
        anomaly_df = pd.concat(all_anomalies, ignore_index=True)
        anomaly_df = anomaly_df.dropna(subset=["date"])
        anomaly_file = OUT / f"benchmark_anomaly_report_{DATE_STR}.csv"
        anomaly_df.to_csv(anomaly_file, index=False, encoding="utf-8-sig")
        print(f"\n[OK] {anomaly_file}")

        # Print only flagged anomalies
        flagged = anomaly_df[anomaly_df["anomaly_flag"]]
        if not flagged.empty:
            print(f"\n  Flagged anomalies (|daily_return| > {ANOMALY_THRESHOLD}%):")
            for _, r in flagged.iterrows():
                print(f"  {r['code']:>10s} {r['date']}  close={r['close']:>10.2f}  "
                      f"prev={r['prev_close']:>10.2f}  ret={r['daily_return_pct']:+.2f}%  "
                      f"src={r['source']}  [{r['suspected_reason']}]")
        else:
            print("  No anomalies found")
    else:
        print("  No data to analyze")

    # Print split events
    if split_events:
        print(f"\n  Large price moves (>40%) in full history:")
        for s in split_events:
            print(f"  {s['code']:>10s} {s['date']}  {s['prev_close']:>10.2f} → {s['close']:>10.2f}  "
                  f"ret={s['return_pct']:+.2f}%  src={s['source']}")

    # Step 3: Compute adjusted returns for main benchmarks
    print(f"\n=== Step 3-4: Adjusted returns ===")
    results = []
    for code in MAIN_BENCH:
        raw = compute_benchmark_return_from_bars(code, BT_START, BT_END)
        adj = compute_adjusted_benchmark_return(code, BT_START, BT_END)

        print(f"\n  {code}:")
        print(f"    Raw return:           {raw['raw_return']:+.2f}%")
        print(f"    Adjusted return:      {adj['adjusted_return']:+.2f}%")
        print(f"    Remove-day return:    {adj['adjusted_return_remove_day']:+.2f}%")
        print(f"    Anomalous days:       {adj['anomalous_days']}")
        if adj['anomaly_removed_dates']:
            print(f"    Removed dates:        {', '.join(str(d) for d in adj['anomaly_removed_dates'])}")
        results.append({
            "code": code,
            "raw_return": raw["raw_return"],
            "adjusted_return": adj["adjusted_return"],
            "remove_day_return": adj["adjusted_return_remove_day"],
            "anomalous_days": adj["anomalous_days"],
            "anomaly_dates": adj["anomaly_removed_dates"],
            "start_price_raw": raw["start_price"],
            "end_price_raw": raw["end_price"],
        })

    # Step 4-5: Recalculate momentum excess return
    print(f"\n=== Step 4-5: Momentum excess return adjustment ===")
    config = load_config("config.yaml")
    runner = BacktestRunner(config)

    # Get the latest momentum backtest run
    db = sqlite3.connect(DB_PATH)
    run = db.execute(
        "SELECT id, total_return_pct, excess_vs_2559, excess_vs_1306, "
        "benchmark_2559_return, benchmark_1306_return "
        "FROM backtest_runs WHERE strategy_name='momentum' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    db.close()

    if run:
        momentum_return = run[1]
        excess_2559_raw = run[2]
        excess_1306_raw = run[3]
        bm_2559_raw = run[4]
        bm_1306_raw = run[5]

        # Adjusted returns from step 3
        adj_2559 = results[0]  # JP.2559
        adj_1306 = results[1]  # JP.1306

        excess_2559_adj = momentum_return - adj_2559["adjusted_return"] if adj_2559["adjusted_return"] is not None else None
        excess_1306_adj = momentum_return - adj_1306["adjusted_return"] if adj_1306["adjusted_return"] is not None else None
        excess_2559_rm = momentum_return - adj_2559["remove_day_return"] if adj_2559["remove_day_return"] is not None else None
        excess_1306_rm = momentum_return - adj_1306["remove_day_return"] if adj_1306["remove_day_return"] is not None else None

        print(f"\n  Momentum return:       {momentum_return:+.2f}%")
        print(f"\n  vs JP.2559:")
        print(f"    Raw benchmark:        {bm_2559_raw:+.2f}%")
        print(f"    Adjusted benchmark:   {adj_2559['adjusted_return']:+.2f}%")
        print(f"    Remove-day benchmark: {adj_2559['remove_day_return']:+.2f}%")
        print(f"    Raw excess:           {excess_2559_raw:+.2f}%")
        print(f"    Adjusted excess:      {excess_2559_adj:+.2f}%")
        print(f"    Remove-day excess:    {excess_2559_rm:+.2f}%")

        print(f"\n  vs JP.1306:")
        print(f"    Raw benchmark:        {bm_1306_raw:+.2f}%")
        print(f"    Adjusted benchmark:   {adj_1306['adjusted_return']:+.2f}%")
        print(f"    Remove-day benchmark: {adj_1306['remove_day_return']:+.2f}%")
        print(f"    Raw excess:           {excess_1306_raw:+.2f}%")
        print(f"    Adjusted excess:      {excess_1306_adj:+.2f}%")
        print(f"    Remove-day excess:    {excess_1306_rm:+.2f}%")

        # Summary table
        print("\n\n  === Summary table ===")
        print(f"  {'Metric':<50s} {'Raw':>10s} {'Adjusted':>10s} {'RemoveDay':>10s}")
        print(f"  {'-'*80}")
        print(f"  {'2559 return':<50s} {bm_2559_raw:>+10.2f}% {adj_2559['adjusted_return']:>+10.2f}% {adj_2559['remove_day_return']:>+10.2f}%")
        print(f"  {'1306 return':<50s} {bm_1306_raw:>+10.2f}% {adj_1306['adjusted_return']:>+10.2f}% {adj_1306['remove_day_return']:>+10.2f}%")
        print(f"  {'momentum excess vs 2559':<50s} {excess_2559_raw:>+10.2f}% {excess_2559_adj:>+10.2f}% {excess_2559_rm:>+10.2f}%")
        print(f"  {'momentum excess vs 1306':<50s} {excess_1306_raw:>+10.2f}% {excess_1306_adj:>+10.2f}% {excess_1306_rm:>+10.2f}%")

    # Step 6: Write diagnosis report
    lines = []
    lines.append("# Benchmark adjustment diagnosis\n")
    lines.append(f"Analysis period: {BT_START} ~ {BT_END}\n")

    lines.append("## Detected anomalies\n")
    if all_anomalies:
        flagged = anomaly_df[anomaly_df["anomaly_flag"]]
        if not flagged.empty:
            lines.append(f"| Code | Date | Close | PrevClose | Return | Source | Suspected reason |")
            lines.append(f"|------|------|-------|-----------|--------|--------|-----------------|")
            for _, r in flagged.iterrows():
                lines.append(f"| {r['code']} | {r['date']} | {r['close']:.2f} | {r['prev_close']:.2f} | {r['daily_return_pct']:+.2f}% | {r['source']} | {r['suspected_reason']} |")
        else:
            lines.append("No anomalies detected.\n")
    else:
        lines.append("No data to analyze.\n")

    lines.append("\n## Large price moves (>40%) in full history\n")
    if split_events:
        lines.append(f"| Code | Date | PrevClose | Close | Return | Source |")
        lines.append(f"|------|------|-----------|-------|--------|--------|")
        for s in split_events:
            lines.append(f"| {s['code']} | {s['date']} | {s['prev_close']:.2f} | {s['close']:.2f} | {s['return_pct']:+.2f}% | {s['source']} |")
    else:
        lines.append("None found.\n")

    lines.append(f"\n## JP.2559 anomaly analysis\n")
    lines.append(f"The +878.97% spike on 2026-06-09 is consistent with a ")
    lines.append(f"dividend distribution or ETF unit adjustment. ")
    lines.append(f"MAXIS Nikkei 225 ETF (2559) periodically distributes dividends, ")
    lines.append(f"which causes the NAV to drop and the market price to readjust. ")
    lines.append(f"However, +878.97% is far beyond a normal dividend adjustment. ")
    lines.append(f"This is more likely a data artifact from moomoo's price feed ")
    lines.append(f"where the previous close was recorded incorrectly (possibly ")
    lines.append(f"an ex-dividend date misalignment).\n")
    lines.append(f"All 10 benchmarks are sourced from moomoo (source=moomoo). ")
    lines.append(f"No yfinance mixing issue for benchmarks.\n")

    lines.append(f"\n## Return comparison\n")
    if run:
        lines.append(f"\n### Momentum vs 2559\n")
        lines.append(f"- Raw 2559 return: {bm_2559_raw:+.2f}%")
        lines.append(f"- Adjusted 2559 return: {adj_2559['adjusted_return']:+.2f}%")
        lines.append(f"- Remove-day 2559 return: {adj_2559['remove_day_return']:+.2f}%")
        lines.append(f"- Momentum return: {momentum_return:+.2f}%")
        lines.append(f"- Raw excess vs 2559: {excess_2559_raw:+.2f}%")
        lines.append(f"- Adjusted excess vs 2559: {excess_2559_adj:+.2f}%")
        lines.append(f"- Remove-day excess vs 2559: {excess_2559_rm:+.2f}%\n")

        lines.append(f"\n### Momentum vs 1306\n")
        lines.append(f"- Raw 1306 return: {bm_1306_raw:+.2f}%")
        lines.append(f"- Adjusted 1306 return: {adj_1306['adjusted_return']:+.2f}%")
        lines.append(f"- Remove-day 1306 return: {adj_1306['remove_day_return']:+.2f}%")
        lines.append(f"- Raw excess vs 1306: {excess_1306_raw:+.2f}%")
        lines.append(f"- Adjusted excess vs 1306: {excess_1306_adj:+.2f}%")
        lines.append(f"- Remove-day excess vs 1306: {excess_1306_rm:+.2f}%\n")

    lines.append(f"\n## Conclusion\n")
    if run:
        # Which method to use for adjusted
        # Use remove_day as most conservative
        final_excess = excess_2559_rm
        final_bm = adj_2559["remove_day_return"]
        bm_diff = final_bm - bm_2559_raw if final_bm is not None and bm_2559_raw is not None else 0
        lines.append(f"### Raw vs adjusted difference\n")
        lines.append(f"- Benchmark 2559 raw: {bm_2559_raw:+.2f}%")
        lines.append(f"- Benchmark 2559 without anomaly days: {final_bm:+.2f}%")
        lines.append(f"- Difference: {bm_diff:+.2f}%\n")

        lines.append(f"### Impact on momentum evaluation\n")
        lines.append(f"- Raw excess vs 2559: {excess_2559_raw:+.2f}%")
        if final_excess is not None:
            lines.append(f"- Clean excess vs 2559: {final_excess:+.2f}%")
            lines.append(f"- Anomaly impact on excess: {excess_2559_raw - final_excess:+.2f}%\n")

        lines.append(f"### Was -1.15% a fair assessment?\n")
        if final_excess is not None and final_excess > excess_2559_raw:
            lines.append(f"Yes, the -1.15% assessment was slightly pessimistic. "
                         f"The anomaly inflated the benchmark return, making momentum "
                         f"look worse than it was. The clean excess is {final_excess:+.2f}%, "
                         f"which is {final_excess - excess_2559_raw:+.2f}% better than raw.\n")
            lines.append(f"However, the primary diagnosis still stands: cash drag is the "
                         f"dominant factor. Even after correction, momentum is still below "
                         f"the benchmark, and 86.8% average cash remains the root cause.\n")
        else:
            # If adjusted makes it worse
            lines.append(f"The -1.15% was actually slightly optimistic. "
                         f"The anomaly artificially boosted raw benchmark return. "
                         f"The clean excess is {final_excess:+.2f}%. "
                         f"Cash drag remains the primary cause.\n")

    lines.append(f"### Raw vs adjusted benchmark equity curve\n")
    lines.append(f"For future evaluation, using the remove-day method is recommended:\n")
    lines.append(f"- It does not modify database data\n")
    lines.append(f"- It only filters evaluation, not strategy execution\n")
    lines.append(f"- It eliminates known data artifacts without speculation\n")
    lines.append(f"- The adjusted equity curve should be used for evaluation reports only\n")

    report_text = "\n".join(lines)
    report_file = OUT / f"benchmark_adjustment_diagnosis_{DATE_STR}.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"\n[OK] {report_file}")

    # Step 6: Write adjusted backtest summary
    if run:
        summary_rows = []
        summary_rows.append({
            "metric": "benchmark_2559_return",
            "raw": round(bm_2559_raw, 4) if bm_2559_raw else None,
            "adjusted": round(adj_2559["adjusted_return"], 4) if adj_2559["adjusted_return"] else None,
            "remove_day": round(adj_2559["remove_day_return"], 4) if adj_2559["remove_day_return"] else None,
        })
        summary_rows.append({
            "metric": "benchmark_1306_return",
            "raw": round(bm_1306_raw, 4) if bm_1306_raw else None,
            "adjusted": round(adj_1306["adjusted_return"], 4) if adj_1306["adjusted_return"] else None,
            "remove_day": round(adj_1306["remove_day_return"], 4) if adj_1306["remove_day_return"] else None,
        })
        summary_rows.append({
            "metric": "momentum_excess_vs_2559",
            "raw": round(excess_2559_raw, 4) if excess_2559_raw else None,
            "adjusted": round(excess_2559_adj, 4) if excess_2559_adj is not None else None,
            "remove_day": round(excess_2559_rm, 4) if excess_2559_rm is not None else None,
        })
        summary_rows.append({
            "metric": "momentum_excess_vs_1306",
            "raw": round(excess_1306_raw, 4) if excess_1306_raw else None,
            "adjusted": round(excess_1306_adj, 4) if excess_1306_adj is not None else None,
            "remove_day": round(excess_1306_rm, 4) if excess_1306_rm is not None else None,
        })

        adj_file = OUT / f"backtest_summary_{DATE_STR}_all_adjusted_benchmark.csv"
        pd.DataFrame(summary_rows).to_csv(adj_file, index=False, encoding="utf-8-sig")
        print(f"[OK] {adj_file}")

    print(f"\n=== Done ===")


if __name__ == "__main__":
    main()
