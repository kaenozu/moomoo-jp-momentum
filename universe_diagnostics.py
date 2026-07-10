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


def load_config_cached(config_path="config.yaml"):
    return load_config(config_path)


def get_latest_indicators(conn, date: str = None) -> pd.DataFrame:
    if date:
        df = pd.read_sql_query("SELECT i.*, s.name, s.type, s.role, s.tradable, s.sector FROM indicators i JOIN symbols s ON i.code=s.code WHERE i.date=?", conn, params=[date])
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


def funnel_analysis(conn, df: pd.DataFrame, config=None, date: str = None) -> dict:
    """シグナル判定条件のファネル分析。
    indicatorsを直接見て各条件で何件落ちているかを算出する。
    signalsテーブルに依存しない。"""
    if df.empty:
        return {"error": "データがありません"}

    screening = config.get("screening", {}) if config else {}
    universe_cfg = config.get("universe", {}) if config else {}
    min_tp = universe_cfg.get("min_trade_price", 500)
    max_tp = universe_cfg.get("max_trade_price", 20000)
    min_turnover = screening.get("min_turnover", 1000000000)
    min_vr = screening.get("min_volume_ratio", 1.5)
    max_h20d = screening.get("max_distance_from_high_20d", 5.0)
    min_history = screening.get("min_history_days", 25)

    # クロスセクション統計
    ratios_df = df[df["role"] == "trade_candidate"]["volume_ratio"].dropna()
    if not ratios_df.empty:
        import statistics
        market_median_vr = statistics.median(ratios_df.tolist())
        df["volume_ratio_percentile"] = df["volume_ratio"].rank(pct=True) * 100
        df["relative_volume_ratio"] = df["volume_ratio"] / market_median_vr if market_median_vr > 0 else 1.0
        df["market_median_volume_ratio"] = market_median_vr
    else:
        df["volume_ratio_percentile"] = None
        df["relative_volume_ratio"] = None
        df["market_median_volume_ratio"] = None
        market_median_vr = 0

    steps_base = [
        ("total_indicators", None),
        ("role_trade_candidate", lambda r: r.get("role") == "trade_candidate"),
        ("tradable_true", lambda r: bool(r.get("tradable", True))),
        ("price_ok", lambda r: (r.get("close") or 0) >= min_tp and (r.get("close") or 0) <= max_tp),
        ("history_days_ok", lambda r: (r.get("history_days") or 0) >= min_history),
        ("close_gt_ma25", lambda r: r.get("close") and r.get("ma25") and r["close"] > r["ma25"]),
        ("ma5_gt_ma25", lambda r: r.get("ma5") and r.get("ma25") and r["ma5"] > r["ma25"]),
        ("return_5d_vs_benchmark_gt_0", lambda r: (r.get("return_5d_vs_benchmark") or -999) > 0),
        ("return_20d_vs_benchmark_gt_0", lambda r: (r.get("return_20d_vs_benchmark") or -999) > 0),
    ]

    rows = df.to_dict("records")

    # ベース（sequential chain）を計算
    base_passed = list(rows)
    result = {}
    base_step_names = [s[0] for s in steps_base]
    for step_name, condition in steps_base:
        if condition is None:
            result[step_name] = len(base_passed)
        else:
            base_passed = [r for r in base_passed if condition(r)]
            result[step_name] = len(base_passed)

    # ベース通過後の銘柄リスト（return_20d_vs_benchmark_gt_0 の33銘柄）
    base_set = base_passed

    # 各ブランチはベース通過銘柄から独立計算
    result["branch:volume_ge_1.2"] = sum(1 for r in base_set if (r.get("volume_ratio") or 0) >= 1.2)
    result["branch:volume_ge_1.5"] = sum(1 for r in base_set if (r.get("volume_ratio") or 0) >= 1.5)
    result["branch:volume_pct_ge_80"] = sum(1 for r in base_set if (r.get("volume_ratio_percentile") or 0) >= 80)
    result["branch:volume_pct_ge_60"] = sum(1 for r in base_set if (r.get("volume_ratio_percentile") or 0) >= 60)
    result["branch:no_volume_gate"] = sum(1 for r in base_set if (r.get("turnover") or 0) >= min_turnover)

    # ベースステップの減少数
    result["drops"] = {}
    for i in range(1, len(base_step_names)):
        prev = base_step_names[i - 1]
        curr = base_step_names[i]
        drop = result[prev] - result[curr]
        if drop > 0:
            result["drops"][f"{prev}_to_{curr}"] = drop

    # signalsテーブルのBUY_CANDIDATE実績
    if date:
        buy_df = pd.read_sql_query("SELECT COUNT(*) as cnt FROM signals WHERE date=? AND signal_type='BUY_CANDIDATE'", conn, params=[date])
    else:
        buy_df = pd.read_sql_query("SELECT COUNT(*) as cnt FROM signals WHERE date=(SELECT MAX(date) FROM signals) AND signal_type='BUY_CANDIDATE'", conn)
    result["actual_buy_candidate"] = int(buy_df["cnt"].iloc[0]) if not buy_df.empty else 0

    return result


def display(results: dict, df: pd.DataFrame):
    print("=" * 60)
    print("ユニバース診断")
    print("=" * 60)
    print(f"全銘柄数: {results.get('total_symbols', 0)}")
    print(f"role別: {results.get('role_counts', {})}")
    print(f"tradable別: {results.get('tradable_counts', {})}")
    print(f"signal_type別: {results.get('signal_counts', {})}")

    # ファネル分析
    funnel = results.get("funnel")
    if funnel and "error" not in funnel:
        print("\n--- シグナルファネル ---")
        steps = [
            ("total_indicators", "全indicators"),
            ("role_trade_candidate", "role=trade_candidate"),
            ("tradable_true", "tradable=true"),
            ("price_ok", "価格範囲内"),
            ("history_days_ok", "履歴日数OK"),
            ("close_gt_ma25", "close>ma25"),
            ("ma5_gt_ma25", "ma5>ma25"),
            ("return_5d_vs_benchmark_gt_0", "5日相対強度>0"),
            ("return_20d_vs_benchmark_gt_0", "20日相対強度>0"),
            ("---", "--- volume条件比較 ---"),
            ("branch:volume_ge_1.2", "旧:出来高比>=1.2"),
            ("branch:volume_ge_1.5", "旧:出来高比>=1.5"),
            ("branch:volume_pct_ge_80", "新:出来高Pct>=80"),
            ("branch:volume_pct_ge_60", "新:出来高Pct>=60"),
            ("branch:no_volume_gate", "新:volume条件なし"),
        ]
        for key, label in steps:
            val = funnel.get(key, 0)
            drop = funnel.get("drops", {}).get(f"{steps[steps.index((key, label)) - 1][0]}_to_{key}") if steps.index((key, label)) > 0 else None
            drop_str = f" (-{drop})" if drop else ""
            print(f"  {label}: {val}件{drop_str}")
        print(f"  → actual BUY_CANDIDATE: {funnel.get('actual_buy_candidate', 0)}件")

    print()
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
    summary_rows = [{"metric": k, "value": str(v)} for k, v in results.items() if k not in ("high_score_watch", "funnel")]
    pd.DataFrame(summary_rows).to_csv(f"{output_dir}/universe_summary_{date_str}.csv", index=False, encoding="utf-8-sig")
    print(f"[OK] universe_summary_{date_str}.csv")

    # ファネルCSV
    funnel = results.get("funnel")
    if funnel and "error" not in funnel:
        funnel_rows = [
            {"step": k, "passed": v}
            for k, v in funnel.items()
            if k not in ("drops", "actual_buy_candidate")
        ]
        if "actual_buy_candidate" in funnel:
            funnel_rows.append({"step": "actual_buy_candidate", "passed": funnel["actual_buy_candidate"]})
        funnel_rows.append({"step": "", "passed": ""})
        for drop_key, drop_val in funnel.get("drops", {}).items():
            funnel_rows.append({"step": f"drop_{drop_key}", "passed": f"-{drop_val}"})
        pd.DataFrame(funnel_rows).to_csv(f"{output_dir}/universe_funnel_{date_str}.csv", index=False, encoding="utf-8-sig")
        print(f"[OK] universe_funnel_{date_str}.csv")


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
    results["funnel"] = funnel_analysis(conn, df, config=config, date=args.date)
    display(results, df)

    date_str = args.date or datetime.now().strftime("%Y%m%d")
    if args.csv:
        export_results(results, df, date_str=date_str)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
