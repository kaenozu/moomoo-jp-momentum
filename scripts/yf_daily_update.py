"""
yfinance US株日次更新スクリプト

ファイルパス: scripts/yf_daily_update.py
何をするか: yfinanceからUS株の日足データを取得しdaily_barsを更新する
なぜ存在するか: moomoo OpenDの購読枠制限を回避し、安定的に日次更新するため
関連ファイル: scripts/recalc_indicators.py, screen_candidates.py
"""

import sqlite3
import sys
import time
import warnings
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

DB_PATH = "data/moomoo.db"
BATCH_SIZE = 50
SLEEP_SEC = 1.0


def to_yahoo(code: str) -> str:
    if not code.startswith("US."):
        return None
    return code[3:]


def codes_from_db(db, include_benchmarks: bool = True) -> list[tuple[str, str]]:
    rows = db.execute(
        "SELECT code, name FROM symbols WHERE enabled=1 ORDER BY code"
    ).fetchall()
    result = []
    for r in rows:
        yahoo = to_yahoo(r[0])
        if yahoo:
            result.append((r[0], r[1], yahoo))
    return result


def get_latest_date(db, code: str) -> str | None:
    row = db.execute(
        "SELECT MAX(date) FROM daily_bars WHERE code = ?", (code,)
    ).fetchone()
    return row[0] if row and row[0] else None


def fetch_yf(symbols: list[tuple[str, str, str]], period: str = "1mo") -> pd.DataFrame:
    """Fetch daily data from yfinance for given US symbols"""
    yahoo_tickers = [s[2] for s in symbols]
    all_data = []

    for i in range(0, len(yahoo_tickers), BATCH_SIZE):
        batch = yahoo_tickers[i : i + BATCH_SIZE]
        batch_codes = {s[2]: s[0] for s in symbols}

        try:
            df = yf.download(batch, period=period, group_by="ticker", auto_adjust=True, threads=True, progress=False)
            print(f"  batch {i//BATCH_SIZE + 1}: {len(batch)} tickers")

            if df.empty:
                continue

            if len(batch) == 1:
                ticker = batch[0]
                temp = df.copy()
                temp["ticker"] = ticker
                df = temp.reset_index()
                records = df.to_dict("records")
                for rec in records:
                    date_val = rec["Date"]
                    close_val = rec.get("Close")
                    open_val = rec.get("Open")
                    high_val = rec.get("High")
                    low_val = rec.get("Low")
                    volume_val = rec.get("Volume")
                    date_str = pd.Timestamp(date_val).strftime("%Y-%m-%d") if hasattr(date_val, "strftime") else str(date_val)[:10]
                    code = batch_codes.get(ticker, f"US.{ticker}")
                    all_data.append({
                        "code": code,
                        "date": date_str,
                        "open": float(open_val) if open_val is not None and pd.notna(open_val) else None,
                        "high": float(high_val) if high_val is not None and pd.notna(high_val) else None,
                        "low": float(low_val) if low_val is not None and pd.notna(low_val) else None,
                        "close": float(close_val) if close_val is not None and pd.notna(close_val) else None,
                        "volume": int(volume_val) if volume_val is not None and pd.notna(volume_val) else 0,
                        "turnover": None,
                    })
            else:
                for ticker in batch:
                    if ticker not in df.columns.levels[0] if hasattr(df.columns, "levels") else False:
                        continue
                    try:
                        ticker_df = df.xs(ticker, axis=1, level=0)
                        for idx, row in ticker_df.iterrows():
                            date_str = pd.Timestamp(idx).strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                            close_val = row.get("Close")
                            if close_val is None or (hasattr(close_val, "__iter__") and len(close_val) == 0) or (hasattr(close_val, "__array__") and close_val.size == 0):
                                close_val = None
                            else:
                                close_val = float(close_val) if hasattr(close_val, "__float__") else close_val
                                if hasattr(close_val, "__iter__"):
                                    continue
                            if close_val is None:
                                continue
                            code = batch_codes.get(ticker, f"US.{ticker}")
                            all_data.append({
                                "code": code,
                                "date": date_str,
                                "open": float(row.get("Open", 0)) if pd.notna(row.get("Open", 0)) else None,
                                "high": float(row.get("High", 0)) if pd.notna(row.get("High", 0)) else None,
                                "low": float(row.get("Low", 0)) if pd.notna(row.get("Low", 0)) else None,
                                "close": float(close_val),
                                "volume": int(row.get("Volume", 0)) if pd.notna(row.get("Volume", 0)) else 0,
                                "turnover": None,
                            })
                    except Exception as e:
                        print(f"    [WARN] {ticker}: {e}")
                        continue

            time.sleep(SLEEP_SEC)
        except Exception as e:
            print(f"  [ERROR] batch {i//BATCH_SIZE + 1}: {e}")
            time.sleep(SLEEP_SEC * 3)

    return pd.DataFrame(all_data)


def save_to_db(db, df: pd.DataFrame):
    inserted = 0
    skipped = 0
    for _, row in df.iterrows():
        existing = db.execute(
            "SELECT 1 FROM daily_bars WHERE code = ? AND date = ?",
            (row["code"], row["date"]),
        ).fetchone()
        if existing:
            skipped += 1
            continue
        db.execute(
            """INSERT OR IGNORE INTO daily_bars
            (code, date, open, high, low, close, volume, turnover, source, turnover_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'yfinance', 'estimated')""",
            (row["code"], row["date"], row["open"], row["high"], row["low"],
             row["close"], row["volume"], row["turnover"]),
        )
        inserted += 1
    db.commit()
    return inserted, skipped


def main():
    print("=" * 60)
    print("yfinance US株日次更新")
    print("=" * 60)

    db = sqlite3.connect(DB_PATH)
    codes = codes_from_db(db)
    print(f"[OK] US symbols: {len(codes)}")

    row = db.execute("SELECT MAX(date) FROM daily_bars WHERE code LIKE 'US.SPY'").fetchone()
    print(f"[OK] US.SPY latest date: {row[0] if row else 'N/A'}")

    print(f"\nFetching yfinance data for {len(codes)} symbols...")
    df = fetch_yf(codes, period="1mo")

    if df.empty:
        print("[ERROR] No data fetched")
        return 1

    print(f"[OK] Fetched {len(df)} rows")

    inserted, skipped = save_to_db(db, df)
    print(f"[OK] Inserted: {inserted}, Skipped (existing): {skipped}")

    codes_with_new = df["code"].nunique()
    print(f"[OK] Codes with new data: {codes_with_new}")

    us_dates = db.execute(
        "SELECT MIN(date), MAX(date), COUNT(DISTINCT code) FROM daily_bars WHERE code LIKE 'US.%'"
    ).fetchone()
    print(f"[OK] US daily_bars: {us_dates[2]} codes, {us_dates[0]} ~ {us_dates[1]}")

    db.close()
    print("\nDone!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
