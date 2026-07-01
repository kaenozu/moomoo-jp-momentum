"""
ユニバース診断

ファイルパス: universe_diagnostics.py
何をするか: 現行ユニバースの構成・買い候補になれない理由を分析する
なぜ存在するか: 指数に負ける原因が戦略条件か銘柄不足かを切り分けるため
関連ファイル: src/screener.py, src/config.py, data/symbols.json

使い方:
    python universe_diagnostics.py
    python universe_diagnostics.py --date 2026-06-30
    python universe_diagnostics.py --from 2026-05-21 --to 2026-06-30 --csv
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import sqlite3

from src.config import load_config
from src.data_store import DataStore


def load_config_cached(config_path="config.yaml"):
    return load_config(config_path)


def get_latest_indicators(conn, date: str = None) -> pd.DataFrame:
    if date:
        df = pd.read_sql_query("SELECT * FROM indicators WHERE date=?", conn, params=[date])
    else:
        df = pd.read_sql_query("SELECT i.*, s.name, s.type, s.role, s.tradable, s.sector FROM indicators i JOIN symbols s ON i.code=s.code WHERE i.date=(SELECT MAX(date) FROM indicators)", conn)
    return df


def analyze(conn, date: str = None) -> dict:
    """ユニバースを診断"""
    df = get_latest_indicators(conn, date)
    if df.empty:
        return {"error": "データがありません"}

    results = {}
    # role別件数
    results["role_counts"] = df.groupby("role").size().to_dict()

    # tradable別
    results["tradable_counts"] = df.groupby("tradable").size().to_dict()

    # sector別
    results["sector_counts"] = df.groupby("sector").size().to_dict()
    # sector別平均return（存在するカラムのみ）
    for col in ["return_20d", "return_5d", "return_5d_vs_benchmark"]:
        if col in df.columns:
            results[f"sector_{col}"] = df.groupby("sector")[col].mean().to_dict()

    # signal_type別（signalsテーブルから）
    if date:
        sig_df = pd.read_sql_query("SELECT s.*, sym.role, sym.tradable, sym.sector FROM signals s JOIN symbols sym ON s.code=sym.code WHERE s.date=?", conn, params=[date])
    else:
        sig_df = pd.read_sql_query("SELECT s.*, sym.role, sym.tradable, sym.sector FROM signals s JOIN symbols sym ON s.code=sym.code WHERE s.date=(SELECT MAX(date) FROM signals)", conn)

    if not sig_df.empty:
        results["signal_counts"] = sig_df.groupby("signal_type").size().to_dict()
        # sector別signal_type
        results["sector_signal"] = sig_df.groupby(["sector", "signal_type"]).size().to_dict()
    else:
        results["signal_counts"] = {}

    # WATCH理由分析
    watch_reasons = {"close_not_above_ma25": 0, "volume_ratio_low": 0, "high_20d_far": 0, "return_5d_negative": 0, "turnover_low": 0, "price_high": 0, "price_low": 0, "benchmark": 0, "watch_only_role": 0}

    # 高スコアなのにWATCH/EXCLUDE
    high_score_watch = []
    high_score_exclude = []
    benchmark_stronger_but_excluded = []

    for _, row in df.iterrows():
        role = row.get("role", "trade_candidate")
        close = row.get("close")
        ma25 = row.get("ma25")
        vol_ratio = row.get("volume_ratio")
        high_20d_dist = row.get("high_20d_distance")
        ret_5d = row.get("return_5d")
        turnover = row.get("turnover")
        score = None  # signalsテーブルから取得

        if role == "watch_only":
            watch_reasons["watch_only_role"] += 1
        if role == "benchmark":
            watch_reasons["benchmark"] += 1

        if close and ma25 and close <= ma25:
            watch_reasons["close_not_above_ma25"] += 1

        # 高スコア監視/除外
        if role == "watch_only" and close and close > 20000:
            watch_reasons["price_high"] += 1
            high_score_watch.append(row.get("code"))

    results["watch_reasons"] = watch_reasons
    results["high_score_watch"] = high_score_watch
    results["total_symbols"] = len(df)

    # 警告
    warnings = []
    trade_candidates = len([r for r in df["role"] if r == "trade_candidate"])
    if trade_candidates < len(df) * 0.3:
        warnings.append(f"trade_candidateが{trade_candidates}/{len(df)}と少なすぎます。最低30%は欲しい")
    if results.get("signal_counts", {}).get("BUY_CANDIDATE", 0) < len(df) * 0.1:
        warnings.append(f"BUY候補が{results['signal_counts'].get('BUY_CANDIDATE', 0)}件と少なすぎます")
    if len(df["sector"].unique()) < 10:
        warnings.append(f"セクター数が{len(df['sector'].unique())}と少なすぎます。網羅性不足")

    results["warnings"] = warnings
    return results


def display(results: dict, df: pd.DataFrame):
    print("=" * 60)
    print("ユニバース診断")
    print("=" * 60)
    print(f"全銘柄数: {results.get('total_symbols', 0)}")
    print(f"role別: {results.get('role_counts', {})}")
    print(f"tradable別: {results.get('tradable_counts', {})}")
    print(f"signal_type別: {results.get('signal_counts', {})}")
    print()
    print("sector別件数:")
    for sector, cnt in sorted(results.get("sector_counts", {}).items(), key=lambda x: -x[1])[:10]:
        ret = results.get("sector_return_5d_vs_benchmark", {}).get(sector, 0)
        print(f"  {sector}: {cnt}件 (vs_benchmark_5d={ret:.1f}%)" if ret else f"  {sector}: {cnt}件")

    print()
    print("WATCH/EXCLUDE理由:")
    for reason, cnt in sorted(results.get("watch_reasons", {}).items(), key=lambda x: -x[1]):
        if cnt > 0:
            print(f"  {reason}: {cnt}件")

    print()
    if results.get("warnings"):
        print("警告:")
        for w in results["warnings"]:
            print(f"  [WARN] {w}")

    # 高スコアwatch_only
    if results.get("high_score_watch"):
        print(f"\n高スコアだがwatch_only: {results['high_score_watch']}")


def export_results(results: dict, df: pd.DataFrame, output_dir="reports", date_str=""):
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 診断詳細CSV
    if not df.empty:
        cols = ["code", "name", "sector", "type", "role", "tradable", "close",
                "return_5d", "return_20d", "return_5d_vs_benchmark", "return_20d_vs_benchmark",
                "volume_ratio", "turnover", "ma5", "ma25"]
        available = [c for c in cols if c in df.columns]
        df[available].to_csv(f"{output_dir}/universe_diagnostics_{date_str}.csv", index=False, encoding="utf-8-sig")
        print(f"[OK] universe_diagnostics_{date_str}.csv")

    # サマリーCSV
    summary_rows = [{"metric": k, "value": str(v)} for k, v in results.items() if k not in ("high_score_watch",)]
    pd.DataFrame(summary_rows).to_csv(f"{output_dir}/universe_summary_{date_str}.csv", index=False, encoding="utf-8-sig")
    print(f"[OK] universe_summary_{date_str}.csv")


def main():
    parser = argparse.ArgumentParser(description="Moomoo ユニバース診断")
    parser.add_argument("--date", help="基準日")
    parser.add_argument("--csv", action="store_true")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    try:
        config = load_config_cached(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    conn = sqlite3.connect(str(config.database_path))
    df = get_latest_indicators(conn, args.date)
    if df.empty:
        print("[WARN] データがありません")
        conn.close()
        return 0

    results = analyze(conn, args.date)
    display(results, df)

    date_str = args.date or datetime.now().strftime("%Y%m%d")
    if args.csv:
        export_results(results, df, date_str=date_str)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
