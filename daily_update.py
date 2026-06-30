"""
日次更新スクリプト

全銘柄の日足データを取得し、指標・相対強度を計算し、SQLiteとCSVに出力する。
benchmark銘柄もデータ取得対象に含め、通常スクリーニングでは別レイヤーで除外する。
"""

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from src.config import load_config
from src.connection import OpenDConnection
from src.data_store import DataStore
from src.indicators import calculate_indicators_batch, indicators_to_dataframe
from src.quote_service import QuoteService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def get_latest_bar_date(data_store: DataStore, code: str) -> str | None:
    with sqlite3.connect(data_store.db_path) as conn:
        result = conn.execute("SELECT MAX(date) FROM daily_bars WHERE code = ?", (code,)).fetchone()
        return result[0] if result and result[0] else None


def should_skip_fetch(data_store: DataStore, code: str, today: str) -> bool:
    latest_date = get_latest_bar_date(data_store, code)
    if latest_date is None:
        return False
    if latest_date >= today:
        return True
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    if today_dt.weekday() == 5:
        return latest_date >= (today_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    if today_dt.weekday() == 6:
        return latest_date >= (today_dt - timedelta(days=2)).strftime("%Y-%m-%d")
    return False


def fetch_and_save_daily_klines(
    quote_service: QuoteService,
    data_store: DataStore,
    codes: list[str],
    num_days: int = 120,
    force: bool = False,
) -> dict[str, pd.DataFrame]:
    today = datetime.now().strftime("%Y-%m-%d")
    data_dict = {}
    for i, code in enumerate(codes, 1):
        logger.info("[%s/%s] %s の日足を取得中...", i, len(codes), code)
        if not force and should_skip_fetch(data_store, code, today):
            logger.info("  スキップ: 最新データは取得済み")
            df = data_store.get_daily_bars(code, limit=num_days)
            if not df.empty:
                data_dict[code] = df
            continue
        df = quote_service.get_daily_klines_with_fallback(code, num=num_days, start="2025-01-01")
        if df.empty:
            logger.warning("  データ取得失敗: %s", code)
            continue
        count = data_store.save_dataframe_to_daily_bars(df, code)
        logger.info("  保存完了: %s件", count)
        data_dict[code] = df
    return data_dict


def add_relative_strength(indicators_df: pd.DataFrame, benchmark_code: str = "JP.1306") -> pd.DataFrame:
    """同一日付のベンチマークリターンとの差分を計算する。"""
    if indicators_df.empty or benchmark_code not in set(indicators_df["code"]):
        return indicators_df

    df = indicators_df.copy()
    bench = df[df["code"] == benchmark_code].sort_values("date").tail(1)
    if bench.empty:
        return df

    bench_5d = bench.iloc[0].get("return_5d")
    bench_20d = bench.iloc[0].get("return_20d")
    bench_60d = bench.iloc[0].get("return_60d")

    if pd.notna(bench_5d):
        df["return_5d_vs_benchmark"] = df["return_5d"] - bench_5d
    if "return_20d" in df.columns and pd.notna(bench_20d):
        df["return_20d_vs_benchmark"] = df["return_20d"] - bench_20d
    if "return_60d" in df.columns and pd.notna(bench_60d):
        df["return_60d_vs_benchmark"] = df["return_60d"] - bench_60d

    if "return_5d_vs_benchmark" in df.columns:
        df["relative_strength_rank"] = df["return_5d_vs_benchmark"].rank(ascending=False, method="min")

    return df


def save_benchmark_prices_from_indicators(data_store: DataStore, indicators_df: pd.DataFrame, benchmark_codes: set[str]) -> int:
    if indicators_df.empty or not benchmark_codes:
        return 0
    rows = indicators_df[indicators_df["code"].isin(benchmark_codes)]
    count = 0
    with sqlite3.connect(data_store.db_path) as conn:
        for _, row in rows.iterrows():
            conn.execute(
                """
                INSERT OR REPLACE INTO benchmark_prices
                (benchmark_code, date, close, daily_return, updated_at)
                VALUES (?, ?, ?, ?, datetime('now', 'localtime'))
                """,
                (row.get("code"), row.get("date"), row.get("close"), row.get("daily_return")),
            )
            count += 1
    return count


def save_indicators_to_db(data_store: DataStore, indicators_df: pd.DataFrame) -> int:
    if indicators_df.empty:
        return 0
    now = datetime.now().isoformat()
    sql = """
        INSERT OR REPLACE INTO indicators
        (code, date, close, volume, turnover, daily_return,
         ma5, ma25, high_20d, distance_from_high_20d,
         volume_ma20, volume_ratio, return_5d, history_days,
         return_5d_vs_benchmark, return_20d_vs_benchmark, return_60d_vs_benchmark,
         relative_strength_rank, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = []
    for _, row in indicators_df.iterrows():
        params.append((
            row.get("code"), row.get("date"), row.get("close"), row.get("volume"),
            row.get("turnover"), row.get("daily_return"), row.get("ma5"), row.get("ma25"),
            row.get("high_20d"), row.get("high_20d_distance"), row.get("volume_ma20"),
            row.get("volume_ratio"), row.get("return_5d"), row.get("history_days"),
            row.get("return_5d_vs_benchmark"), row.get("return_20d_vs_benchmark"),
            row.get("return_60d_vs_benchmark"), row.get("relative_strength_rank"), now,
        ))
    with sqlite3.connect(data_store.db_path) as conn:
        conn.executemany(sql, params)
    return len(params)


def export_to_csv(indicators_df: pd.DataFrame, output_dir: str = "reports") -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    filepath = Path(output_dir) / f"indicators_{today}.csv"
    indicators_df.to_csv(filepath, index=False, encoding="utf-8-sig")
    logger.info("CSV出力完了: %s", filepath)
    return str(filepath)


def ensure_symbols_loaded(data_store: DataStore, symbols_file: str) -> int:
    data_store.sync_symbols_from_json(symbols_file)
    return len(data_store.get_enabled_symbols(include_benchmarks=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Moomoo 日次更新スクリプト")
    parser.add_argument("--force", action="store_true", help="強制再取得（スキップしない）")
    parser.add_argument("--dry-run", action="store_true", help="テスト実行（API呼び出しなし）")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo 日次更新")
    print("=" * 60)

    try:
        config = load_config(args.config)
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    data_store = DataStore(config)
    print(f"[OK] データベース: {config.database_path}")

    ensure_symbols_loaded(data_store, config.watchlist_file)
    symbols = data_store.get_enabled_symbols(include_benchmarks=True)
    codes = [s.code for s in symbols]
    symbols_info = {s.code: s.name for s in symbols}
    benchmark_codes = {s.code for s in symbols if s.role == "benchmark"}
    print(f"[OK] 取得対象銘柄数: {len(codes)}（benchmark含む）")

    if not codes:
        print("[ERROR] 銘柄リストが空です")
        return 1

    if args.dry_run:
        print("\n[DRY-RUN] テスト実行（API呼び出しなし）")
        print(f"  対象銘柄: {codes[:5]}...")
        return 0

    print(f"\nOpenD接続先: {config.opend_host}:{config.opend_port}")
    with OpenDConnection(config) as conn:
        status = conn.connect()
        if not status.connected:
            print(f"[ERROR] 接続失敗: {status.message}")
            if status.hint:
                print(f"\n{status.hint}")
            return 1

        print("[OK] OpenD接続成功")
        quote_ctx = conn.get_quote_context()
        if not quote_ctx:
            print("[ERROR] 行情コンテキストが取得できません")
            return 1

        quote_service = QuoteService(config, quote_ctx)
        print("\n" + "-" * 60)
        print("日足データ取得・保存")
        print("-" * 60)
        data_dict = fetch_and_save_daily_klines(quote_service, data_store, codes, num_days=120, force=args.force)
        if not data_dict:
            print("[ERROR] データが取得できませんでした")
            return 1
        print(f"\n[OK] 取得完了: {len(data_dict)}/{len(codes)}銘柄")

        print("\n" + "-" * 60)
        print("指標計算")
        print("-" * 60)
        indicators = calculate_indicators_batch(data_dict, symbols_info)
        indicators_df = indicators_to_dataframe(indicators)
        benchmark_code = config.get("signals.relative_strength.benchmark_code", "JP.1306")
        indicators_df = add_relative_strength(indicators_df, benchmark_code)
        if indicators_df.empty:
            print("[ERROR] 指標計算結果が空です")
            return 1
        print(f"[OK] 指標計算完了: {len(indicators_df)}銘柄")

        ind_count = save_indicators_to_db(data_store, indicators_df)
        bench_count = save_benchmark_prices_from_indicators(data_store, indicators_df, benchmark_codes)
        print(f"[OK] 指標保存完了: {ind_count}件")
        print(f"[OK] ベンチマーク保存完了: {bench_count}件")

        output_dir = config.get("report.output_dir", "reports")
        csv_path = export_to_csv(indicators_df, output_dir)
        print(f"[OK] CSV出力: {csv_path}")

        display_cols = [
            "code", "name", "close", "daily_return", "ma5", "ma25",
            "volume_ratio", "return_5d", "return_5d_vs_benchmark", "history_days",
        ]
        available_cols = [c for c in display_cols if c in indicators_df.columns]
        print("\n" + indicators_df[available_cols].head().to_string(index=False))

        print("\n" + "=" * 60)
        print("日次更新完了")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
