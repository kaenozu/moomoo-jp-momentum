"""
yfinance vs moomoo validation for 127 existing codes

Outputs:
  validation_report.csv  — per-code metrics
  validation_summary.txt — overall summary
"""
import sqlite3
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

DB_PATH = "data/moomoo.db"
YF_INTERVAL = "1d"

# Map moomoo JP code -> yahoo ticker
# moomoo: JP.7203  -> yahoo: 7203.T
def to_yahoo(code: str) -> str:
    if not code.startswith("JP."):
        return None
    num = code[3:]
    # Handle potential leading zeros
    return f"{num}.T"

def fetch_moomoo_bars(db, code: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume, turnover "
        "FROM daily_bars WHERE code = ? ORDER BY date",
        db, params=(code,)
    )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df

def compute_indicators(df: pd.DataFrame) -> dict:
    """Compute key indicators from OHLCV dataframe"""
    if df.empty or len(df) < 25:
        return {}
    close = df["close"].astype(float)
    volume = df["volume"].fillna(0).astype(float)

    # MA
    ma5 = close.rolling(5).mean()
    ma25 = close.rolling(25).mean()

    # Daily return
    daily_ret = close.pct_change()

    # volume_ratio = today_turnover / 20d_avg_turnover
    turnover = df["turnover"].astype(float) if "turnover" in df.columns else close * volume
    vol_ma20 = turnover.rolling(20).mean()
    volume_ratio = turnover / vol_ma20

    # return_5d / return_20d
    return_5d = close.pct_change(5)
    return_20d = close.pct_change(20)

    latest = {
        "close": float(close.iloc[-1]),
        "ma25": float(ma25.iloc[-1]) if not pd.isna(ma25.iloc[-1]) else None,
        "ma5": float(ma5.iloc[-1]) if not pd.isna(ma5.iloc[-1]) else None,
        "volume_ratio": float(volume_ratio.iloc[-1]) if not pd.isna(volume_ratio.iloc[-1]) else None,
    }

    # For correlation metrics: align both sources to common dates
    metrics = {
        "daily_return_corr": None,
        "volume_ratio_corr": None,
        "return_5d_corr": None,
        "return_20d_corr": None,
        "close_ma25_agree": None,
        "ma5_ma25_agree": None,
        "close_avg_dev_pct": None,
        "close_max_dev_pct": None,
        "close_corr": None,
        "volume_corr": None,
        "common_days": 0,
        "latest": latest,
    }
    return metrics

def compare_series(s1: pd.Series, s2: pd.Series) -> dict:
    """Compare two aligned series"""
    common = s1.dropna().align(s2.dropna(), join="inner")
    a, b = common[0], common[1]
    if len(a) < 5:
        return {"corr": None, "common_days": 0}
    corr = a.corr(b)
    return {"corr": corr, "common_days": len(a), "mean_diff_pct": float((a - b).abs().mean())}

def validate_code(code: str, name: str, mm_df: pd.DataFrame) -> dict:
    """Validate a single code: compare mm_df (moomoo) with yfinance"""
    yahoo_ticker = to_yahoo(code)
    if not yahoo_ticker:
        return {"code": code, "name": name, "error": "invalid_code_format"}

    # Fetch yfinance data
    try:
        yf_ticker = yf.Ticker(yahoo_ticker)
        yf_df = yf_ticker.history(period="max", interval=YF_INTERVAL, auto_adjust=False)
    except Exception as e:
        return {"code": code, "name": name, "error": f"yfinance_fetch_failed: {e}"}

    if yf_df.empty:
        return {"code": code, "name": name, "error": "yfinance_empty"}

    # Normalize columns
    yf_df = yf_df.rename(columns={
        "Open": "open", "High": "high", "Low": "low", "Close": "close",
        "Volume": "volume"
    })
    yf_df.index = pd.to_datetime(yf_df.index.date)

    # Align date ranges to common period
    mm_dates = mm_df.index
    yf_dates = yf_df.index
    common_start = max(mm_dates.min(), yf_dates.min())
    common_end = min(mm_dates.max(), yf_dates.max())

    mm_aligned = mm_df.loc[common_start:common_end].copy()
    yf_aligned = yf_df.loc[common_start:common_end].copy()

    common_dates = mm_aligned.index.intersection(yf_aligned.index)
    if len(common_dates) < 5:
        return {"code": code, "name": name, "error": f"too_few_common_days: {len(common_dates)}"}

    mm_c = mm_aligned.loc[common_dates, "close"].astype(float)
    yf_c = yf_aligned.loc[common_dates, "close"].astype(float)
    mm_v = mm_aligned.loc[common_dates, "volume"].fillna(0).astype(float)
    yf_v = yf_aligned.loc[common_dates, "volume"].fillna(0).astype(float)

    # ---- Close correlation ----
    close_corr = mm_c.corr(yf_c) if len(mm_c) > 2 else None

    # ---- Close deviation ----
    dev_pct = (mm_c - yf_c) / yf_c * 100
    avg_dev = float(dev_pct.abs().mean())
    max_dev = float(dev_pct.abs().max())

    # ---- Daily return correlation ----
    mm_ret = mm_c.pct_change().dropna()
    yf_ret = yf_c.pct_change().dropna()
    ret_common = mm_ret.align(yf_ret, join="inner")
    ret_corr = ret_common[0].corr(ret_common[1]) if len(ret_common[0]) > 2 else None

    # ---- Volume correlation ----
    vol_corr = mm_v.corr(yf_v) if len(mm_v) > 2 and mm_v.std() > 0 and yf_v.std() > 0 else None

    # ---- Volume_ratio correlation ----
    mm_turnover = mm_aligned.loc[common_dates, "turnover"].astype(float) if "turnover" in mm_aligned.columns else (mm_c * mm_v)
    yf_turnover = yf_c * yf_v  # estimate for yfinance
    mm_vol_ma20 = mm_turnover.rolling(20, min_periods=5).mean()
    yf_vol_ma20 = yf_turnover.rolling(20, min_periods=5).mean()
    mm_vr = mm_turnover / mm_vol_ma20
    yf_vr = yf_turnover / yf_vol_ma20
    vr_common = mm_vr.dropna().align(yf_vr.dropna(), join="inner")
    vr_corr = vr_common[0].corr(vr_common[1]) if len(vr_common[0]) > 2 and vr_common[0].std() > 0 and vr_common[1].std() > 0 else None

    # ---- MA agreement (latest common date) ----
    mm_ma25 = mm_c.rolling(25, min_periods=20).mean()
    mm_ma5 = mm_c.rolling(5, min_periods=4).mean()
    yf_ma25 = yf_c.rolling(25, min_periods=20).mean()
    yf_ma5 = yf_c.rolling(5, min_periods=4).mean()

    # For each common date, check if both agree on close > ma25
    agree_close_ma25 = 0
    agree_ma5_ma25 = 0
    total_ma_days = 0
    for d in common_dates:
        mm_c_val = mm_c.loc[d]
        yf_c_val = yf_c.loc[d]
        mm_m25 = mm_ma25.loc[d]
        yf_m25 = yf_ma25.loc[d]
        mm_m5 = mm_ma5.loc[d]
        yf_m5 = yf_ma5.loc[d]
        if pd.isna(mm_m25) or pd.isna(yf_m25) or pd.isna(mm_m5) or pd.isna(yf_m5):
            continue
        total_ma_days += 1
        # close > ma25
        mm_flag = mm_c_val > mm_m25
        yf_flag = yf_c_val > yf_m25
        if mm_flag == yf_flag:
            agree_close_ma25 += 1
        # ma5 > ma25
        mm_flag2 = mm_m5 > mm_m25
        yf_flag2 = yf_m5 > yf_m25
        if mm_flag2 == yf_flag2:
            agree_ma5_ma25 += 1

    close_ma25_agree_pct = (agree_close_ma25 / total_ma_days * 100) if total_ma_days > 0 else None
    ma5_ma25_agree_pct = (agree_ma5_ma25 / total_ma_days * 100) if total_ma_days > 0 else None

    # ---- return_5d / return_20d correlation ----
    mm_ret5 = mm_c.pct_change(5).dropna()
    yf_ret5 = yf_c.pct_change(5).dropna()
    r5_common = mm_ret5.align(yf_ret5, join="inner")
    ret5_corr = r5_common[0].corr(r5_common[1]) if len(r5_common[0]) > 2 else None

    mm_ret20 = mm_c.pct_change(20).dropna()
    yf_ret20 = yf_c.pct_change(20).dropna()
    r20_common = mm_ret20.align(yf_ret20, join="inner")
    ret20_corr = r20_common[0].corr(r20_common[1]) if len(r20_common[0]) > 2 else None

    # ---- Latest values snapshot ----
    latest_date = common_dates[-1]
    mm_ma25_val = mm_ma25.loc[latest_date] if latest_date in mm_ma25.index else None
    yf_ma25_val = yf_ma25.loc[latest_date] if latest_date in yf_ma25.index else None
    latest = {
        "mm_close": float(mm_c.loc[latest_date]),
        "yf_close": float(yf_c.loc[latest_date]),
        "mm_volume": int(mm_v.loc[latest_date]),
        "yf_volume": int(yf_v.loc[latest_date]),
        "mm_ma25": float(mm_ma25_val) if mm_ma25_val is not None and not pd.isna(mm_ma25_val) else None,
        "yf_ma25": float(yf_ma25_val) if yf_ma25_val is not None and not pd.isna(yf_ma25_val) else None,
    }

    return {
        "code": code,
        "name": name or yahoo_ticker,
        "error": None,
        "common_days": len(common_dates),
        "close_corr": close_corr,
        "daily_return_corr": ret_corr,
        "volume_corr": vol_corr,
        "volume_ratio_corr": vr_corr,
        "close_avg_dev_pct": avg_dev,
        "close_max_dev_pct": max_dev,
        "close_ma25_agree_pct": close_ma25_agree_pct,
        "ma5_ma25_agree_pct": ma5_ma25_agree_pct,
        "return_5d_corr": ret5_corr,
        "return_20d_corr": ret20_corr,
        "mm_latest_close": latest["mm_close"],
        "yf_latest_close": latest["yf_close"],
        "mm_latest_volume": latest["mm_volume"],
        "yf_latest_volume": latest["yf_volume"],
    }


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    # Get codes that have bars in moomoo
    rows = db.execute("""
        SELECT DISTINCT s.code, s.name
        FROM symbols s
        JOIN daily_bars b ON s.code = b.code
        WHERE s.enabled = 1
        ORDER BY s.code
    """).fetchall()
    codes = [(r["code"], r["name"]) for r in rows]
    print(f"Found {len(codes)} codes with daily_bars in moomoo DB")

    results = []
    for i, (code, name) in enumerate(codes):
        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(codes)}] {code} ...")
        mm_df = fetch_moomoo_bars(db, code)
        if mm_df.empty:
            results.append({"code": code, "name": name, "error": "moomoo_empty"})
            continue
        try:
            r = validate_code(code, name, mm_df)
        except Exception as e:
            r = {"code": code, "name": name, "error": f"exception: {e}"}
        results.append(r)

    db.close()

    # Build summary
    df = pd.DataFrame(results)
    df_ok = df[df["error"].isna()].copy()
    df_err = df[df["error"].notna()].copy()

    print(f"\n=== Summary ===")
    print(f"  Total: {len(results)}")
    print(f"  OK: {len(df_ok)}")
    print(f"  Errors: {len(df_err)}")

    if len(df_err) > 0:
        print(f"\n  Errors breakdown:")
        for err_type, cnt in df_err["error"].value_counts().items():
            print(f"    {err_type}: {cnt}")

    if len(df_ok) > 0:
        print(f"\n=== Price ===")
        print(f"  close_corr:         mean={df_ok['close_corr'].mean():.4f}, median={df_ok['close_corr'].median():.4f}")
        print(f"  close_avg_dev_pct:  mean={df_ok['close_avg_dev_pct'].mean():.2f}%")
        print(f"  close_max_dev_pct:  mean={df_ok['close_max_dev_pct'].mean():.2f}%")

        print(f"\n=== Return ===")
        print(f"  daily_return_corr:  mean={df_ok['daily_return_corr'].mean():.4f}, median={df_ok['daily_return_corr'].median():.4f}")
        print(f"  return_5d_corr:     mean={df_ok['return_5d_corr'].mean():.4f}, median={df_ok['return_5d_corr'].median():.4f}")
        print(f"  return_20d_corr:    mean={df_ok['return_20d_corr'].mean():.4f}, median={df_ok['return_20d_corr'].median():.4f}")

        print(f"\n=== Volume ===")
        print(f"  volume_corr:        mean={df_ok['volume_corr'].mean():.4f}, median={df_ok['volume_corr'].median():.4f}")
        print(f"  volume_ratio_corr:  mean={df_ok['volume_ratio_corr'].mean():.4f}, median={df_ok['volume_ratio_corr'].median():.4f}")

        print(f"\n=== MA Agreement ===")
        print(f"  close>ma25 agree:   mean={df_ok['close_ma25_agree_pct'].mean():.1f}%")
        print(f"  ma5>ma25 agree:     mean={df_ok['ma5_ma25_agree_pct'].mean():.1f}%")

        # Anomalies
        print(f"\n=== Anomalies (50 worst by close_avg_dev_pct) ===")
        anomalies = df_ok.nlargest(50, "close_avg_dev_pct")[
            ["code", "name", "close_avg_dev_pct", "close_max_dev_pct", "close_corr", "daily_return_corr", "common_days", "error"]
        ]
        for _, r in anomalies.iterrows():
            print(f"  {r['code']:>10s} | avg_dev={r['close_avg_dev_pct']:.2f}% | max_dev={r['close_max_dev_pct']:.2f}% | corr={r['close_corr']:.4f} | n={r['common_days']}")

        # Output files
        out_dir = Path("data")
        csv_path = out_dir / "yf_validation_codes.csv"
        df.to_csv(csv_path, index=False)
        print(f"\n[OK] {csv_path}")

        # Summary text
        summary_path = out_dir / "yf_validation_summary.txt"
        with open(summary_path, "w") as f:
            f.write(f"yfinance vs moomoo validation summary\n")
            f.write(f"Total codes: {len(results)}, OK: {len(df_ok)}, Errors: {len(df_err)}\n\n")
            for col in ["close_corr", "daily_return_corr", "return_5d_corr", "return_20d_corr",
                        "volume_corr", "volume_ratio_corr", "close_avg_dev_pct", "close_max_dev_pct",
                        "close_ma25_agree_pct", "ma5_ma25_agree_pct"]:
                vals = df_ok[col].dropna()
                if len(vals) == 0:
                    continue
                f.write(f"{col}:    mean={vals.mean():.4f}  median={vals.median():.4f}  min={vals.min():.4f}  max={vals.max():.4f}\n")
        print(f"[OK] {summary_path}")


if __name__ == "__main__":
    main()
