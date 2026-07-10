"""
yfinance supplement for missing daily_bars

- Adds source/turnover_source columns to daily_bars
- Fetches yfinance data for 239 missing codes
- Inserts with source='yfinance', turnover_source='estimated'
- Does NOT overwrite existing moomoo data
"""
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

DB_PATH = "data/moomoo.db"


def to_yahoo(code: str) -> str:
    if not code.startswith("JP."):
        return None
    num = code[3:]
    return f"{num}.T"


def schema_migration(db):
    """Add source and turnover_source columns if not present"""
    cols = [r[1] for r in db.execute("PRAGMA table_info(daily_bars)").fetchall()]
    if "source" not in cols:
        db.execute("ALTER TABLE daily_bars ADD COLUMN source TEXT DEFAULT 'moomoo'")
        print("[MIGRATION] added column: source")
    if "turnover_source" not in cols:
        db.execute("ALTER TABLE daily_bars ADD COLUMN turnover_source TEXT DEFAULT 'actual'")
        print("[MIGRATION] added column: turnover_source")

    # Mark existing moomoo rows explicitly
    db.execute("UPDATE daily_bars SET source='moomoo', turnover_source='actual' WHERE source IS NULL OR source='moomoo'")
    db.commit()
    print("[MIGRATION] existing rows marked as moomoo/actual")


def get_missing_codes(db) -> list[tuple[str, str]]:
    """Get codes that are trade_candidate but have no daily_bars"""
    existing = set(r[0] for r in db.execute("SELECT DISTINCT code FROM daily_bars").fetchall())
    rows = db.execute("""
        SELECT code, name FROM symbols
        WHERE enabled=1 AND role='trade_candidate'
        ORDER BY code
    """).fetchall()
    missing = [(r[0], r[1]) for r in rows if r[0] not in existing]
    return missing


def get_date_range(db) -> tuple[str, str]:
    """Get existing data date range to align yfinance fetch"""
    r = db.execute("SELECT MIN(date), MAX(date) FROM daily_bars").fetchone()
    return r[0], r[1]


def fetch_yfinance(code: str, yahoo_ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch yfinance data for a single ticker"""
    try:
        t = yf.Ticker(yahoo_ticker)
        df = t.history(start=start, end=end, auto_adjust=False)
    except Exception as e:
        print(f"    ERROR fetching {code} ({yahoo_ticker}): {e}")
        return pd.DataFrame()

    if df.empty:
        return df

    # Normalize
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
    })
    # Strip timezone from index
    df.index = pd.to_datetime(df.index.date)
    return df


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    # Step 1: Schema migration
    print("=== Step 1: Schema migration ===")
    schema_migration(db)

    # Step 2: Get missing codes
    print("\n=== Step 2: Identify missing codes ===")
    missing = get_missing_codes(db)
    print(f"  Missing trade_candidate codes: {len(missing)}")

    if not missing:
        print("  Nothing to do. Exiting.")
        db.close()
        return

    # Step 3: Get date range
    start, end = get_date_range(db)
    # yfinance end is exclusive, so add 1 day
    end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
    end_str = end_dt.strftime("%Y-%m-%d")
    print(f"  Fetch range: {start} ~ {end} (yfinance end: {end_str})")

    # Step 4: Fetch and insert
    print("\n=== Step 3: Fetch yfinance data ===")
    inserted = 0
    skipped = 0
    errors = 0
    error_codes = []
    batch = []

    for i, (code, name) in enumerate(missing):
        yahoo_ticker = to_yahoo(code)
        if not yahoo_ticker:
            print(f"  [{i+1}/{len(missing)}] {code} -> SKIP (invalid code format)")
            skipped += 1
            continue

        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(missing)}] {code} ({yahoo_ticker}) ...")

        df = fetch_yfinance(code, yahoo_ticker, start, end_str)
        if df.empty:
            print(f"    {code} ({yahoo_ticker}): empty")
            errors += 1
            error_codes.append((code, name, "empty"))
            continue

        # Filter dates that already exist
        existing_dates = set(
            r[0] for r in db.execute(
                "SELECT date FROM daily_bars WHERE code=?", (code,)
            ).fetchall()
        )

        new_dates = 0
        for date_idx, row in df.iterrows():
            date_str = date_idx.strftime("%Y-%m-%d")
            if date_str in existing_dates:
                continue
            if date_str < start or date_str > end:
                continue  # only fill within existing range

            vol = int(row["volume"]) if pd.notna(row["volume"]) else 0
            close_val = float(row["close"]) if pd.notna(row["close"]) else 0.0
            open_val = float(row["open"]) if pd.notna(row["open"]) else 0.0
            high_val = float(row["high"]) if pd.notna(row["high"]) else 0.0
            low_val = float(row["low"]) if pd.notna(row["low"]) else 0.0
            turnover_est = vol * close_val  # estimate

            batch.append((
                code, date_str, open_val, high_val, low_val, close_val,
                vol, turnover_est, "yfinance", "estimated"
            ))
            new_dates += 1

        if new_dates == 0:
            skipped += 1
        else:
            inserted += new_dates

        # Batch insert every 20 codes
        if len(batch) >= 5000:
            db.executemany(
                "INSERT OR IGNORE INTO daily_bars (code, date, open, high, low, close, volume, turnover, source, turnover_source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                batch,
            )
            db.commit()
            batch = []

        # Rate limiting: 1 request per second (similar to moomoo approach)
        time.sleep(1.1)

    # Final batch insert
    if batch:
        db.executemany(
            "INSERT OR IGNORE INTO daily_bars (code, date, open, high, low, close, volume, turnover, source, turnover_source) VALUES (?,?,?,?,?,?,?,?,?,?)",
            batch,
        )
        db.commit()

    # Step 5: Summary
    print("\n=== Summary ===")
    print(f"  Total missing: {len(missing)}")
    print(f"  Codes with new data: {inserted > 0}")
    print(f"  Skipped (no new dates): {skipped}")
    print(f"  Errors: {errors}")
    print(f"  New rows inserted: {inserted}")

    if error_codes:
        print(f"\n  Error codes:")
        for c, n, reason in error_codes[:20]:
            print(f"    {c} ({n}): {reason}")
        if len(error_codes) > 20:
            print(f"    ... and {len(error_codes) - 20} more")

    # Post-insert stats
    total_codes = db.execute("SELECT COUNT(DISTINCT code) FROM daily_bars").fetchone()[0]
    total_rows = db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    source_counts = db.execute("SELECT source, COUNT(*) as c FROM daily_bars GROUP BY source").fetchall()
    print(f"\n  daily_bars after supplement:")
    print(f"    unique codes: {total_codes}")
    print(f"    total rows: {total_rows}")
    for r in source_counts:
        print(f"    source={r['source']}: {r['c']}")

    db.close()
    print("\n[OK] Supplement complete")

    # Return error codes for reporting
    if error_codes:
        csv_path = "data/yf_errors.csv"
        import csv
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "name", "reason"])
            w.writerows(error_codes)
        print(f"[OK] {csv_path}")


if __name__ == "__main__":
    main()
