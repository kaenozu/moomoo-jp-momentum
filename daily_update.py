"""
日次更新スクリプト

全銘柄の日足データを取得し、指標・相対強度を計算し、SQLiteとCSVに出力する。
benchmark銘柄もデータ取得対象に含め、通常スクリーニングでは別レイヤーで除外する。

取得モード:
  history - request_history_kline中心（購読枠不要）、大量バックフィル向け
  latest  - get_cur_kline + subscribe/unsubscribe、日次少量更新向け
  auto    - 銘柄数に応じて自動選択（100超→history）
"""

import argparse
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from src.config import load_config
from src.connection import OpenDConnection
from src.data_store import DataStore
from src.indicators import calculate_indicators_batch, indicators_to_dataframe, add_relative_strength
from src.quote_service import QuoteService, BATCH_SLEEP_SECONDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

DEFAULT_NUM_DAYS = 120
DEFAULT_START = "2025-01-01"


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
    num_days: int = DEFAULT_NUM_DAYS,
    force: bool = False,
    mode: str = "auto",
    start: str | None = DEFAULT_START,
    batch_size: int = 80,
    quota_aware: bool = True,
) -> dict[str, pd.DataFrame]:
    """日足を取得・保存する（バッチ処理＋リトライ対応）。

    mode履歴:
      history -> request_history_kline のみ（購読枠不要）
      latest  -> get_cur_kline + subscribe/unsubscribe
      auto    -> 100銘柄超でhistory、以下でlatest
    """
    today = datetime.now().strftime("%Y-%m-%d")
    effective_mode = mode
    if effective_mode == "auto":
        effective_mode = "history" if len(codes) > 100 else "latest"

    logger.info("fetch_and_save: mode=%s, force=%s, batch_size=%s, start=%s", effective_mode, force, batch_size, start)

    # スキップ判定＋既存DB読み込み
    skip_codes: list[str] = []
    fetch_codes: list[str] = []
    for code in codes:
        if not force and should_skip_fetch(data_store, code, today):
            skip_codes.append(code)
        else:
            fetch_codes.append(code)

    logger.info("  取得対象: %s銘柄, スキップ: %s銘柄", len(fetch_codes), len(skip_codes))

    data_dict: dict[str, pd.DataFrame] = {}

    # スキップ分はDBから読み込む
    for code in skip_codes:
        df = data_store.get_daily_bars(code, limit=num_days)
        if not df.empty:
            data_dict[code] = df

    # 取得対象をバッチ処理
    if fetch_codes:
        total = len(fetch_codes)
        n_batches = (total + batch_size - 1) // batch_size

        for batch_idx in range(0, total, batch_size):
            batch = fetch_codes[batch_idx : batch_idx + batch_size]
            batch_num = batch_idx // batch_size + 1
            logger.info("  バッチ %d/%d: %s銘柄", batch_num, n_batches, len(batch))

            batch_dict = quote_service.batch_fetch_daily_klines(
                codes=batch,
                mode=effective_mode,
                num=num_days,
                start=start if effective_mode == "history" else None,
                batch_size=len(batch),
                retry_count=2,
                quota_aware=quota_aware,
            )

            # 保存
            for code, df in batch_dict.items():
                count = data_store.save_dataframe_to_daily_bars(df, code)
                logger.info("    [%s] 保存完了: %s件", code, count)
                data_dict[code] = df

            if batch_idx + batch_size < total:
                time.sleep(BATCH_SLEEP_SECONDS)

    logger.info(
        "取得完了: 成功=%d, スキップ=%d, 取得対象=%d",
        len(data_dict),
        len(skip_codes),
        len(fetch_codes),
    )
    return data_dict


def save_benchmark_prices_from_indicators(data_store: DataStore, indicators_df: pd.DataFrame, benchmark_codes: set[str]) -> int:
    if indicators_df.empty or not benchmark_codes:
        return 0
    rows = indicators_df[indicators_df["code"].isin(list(benchmark_codes))]
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
         volume_ma20, volume_ratio, return_5d, return_20d, return_60d, history_days,
         return_5d_vs_benchmark, return_20d_vs_benchmark, return_60d_vs_benchmark,
         relative_strength_rank, volume_ratio_percentile, volume_ratio_rank,
         relative_volume_ratio, market_median_volume_ratio, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = []
    for _, row in indicators_df.iterrows():
        params.append((
            row.get("code"), row.get("date"), row.get("close"), row.get("volume"),
            row.get("turnover"), row.get("daily_return"), row.get("ma5"), row.get("ma25"),
            row.get("high_20d"), row.get("high_20d_distance"), row.get("volume_ma20"),
            row.get("volume_ratio"), row.get("return_5d"), row.get("return_20d"),
            row.get("return_60d"), row.get("history_days"),
            row.get("return_5d_vs_benchmark"), row.get("return_20d_vs_benchmark"),
            row.get("return_60d_vs_benchmark"), row.get("relative_strength_rank"),
            row.get("volume_ratio_percentile"), row.get("volume_ratio_rank"),
            row.get("relative_volume_ratio"), row.get("market_median_volume_ratio"), now,
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
    parser.add_argument("--mode", choices=["history", "latest", "auto"], default="auto",
                        help="取得モード: history(request_history_kline), latest(get_cur_kline), auto(自動判別)")
    parser.add_argument("--start", default=None, help="取得開始日 (YYYY-MM-DD、デフォルト: 2025-01-01)")
    parser.add_argument("--batch-size", type=int, default=80, help="1バッチあたりの銘柄数 (デフォルト: 80)")
    parser.add_argument("--quota-check", action="store_true", help="取得前にquota状況を確認して終了")
    parser.add_argument("--no-quota-aware", action="store_true", help="quota確認なしで取得（従来動作）")
    args = parser.parse_args()

    effective_start = args.start or "2025-01-01"

    print("=" * 60)
    print("Moomoo 日次更新")
    print("=" * 60)
    print(f"  mode: {args.mode}")
    print(f"  start: {effective_start}")
    print(f"  batch-size: {args.batch_size}")

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
        print(f"  mode: {args.mode}")
        print(f"  start: {effective_start}")
        print(f"  batch-size: {args.batch_size}")
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

        # quota確認モード
        if args.quota_check:
            quota = quote_service.get_history_kl_quota()
            print("\n=== 履歴K-line Quota 状況 ===")
            print(f"  使用済み: {quota['used']}銘柄")
            print(f"  残り: {quota['remaining']}銘柄")
            print(f"  合計: {quota['total']}銘柄")
            if quota["recent_codes"]:
                print(f"\n  直近7日以内に取得以内に取得済みの銘柄 ({len(quota['recent_codes'])}件):")
                for code in sorted(quota["recent_codes"]):
                    print(f"    {code}")
            return 0

        print("\n" + "-" * 60)
        print("日足データ取得・保存")
        print("-" * 60)
        data_dict = fetch_and_save_daily_klines(
            quote_service, data_store, codes,
            num_days=DEFAULT_NUM_DAYS,
            force=args.force,
            mode=args.mode,
            start=effective_start,
            batch_size=args.batch_size,
            quota_aware=not args.no_quota_aware,
        )
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

        with sqlite3.connect(data_store.db_path) as conn:
            bar_count = conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
            indicator_count = conn.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]

        print(f"\n  取得成功: {len(data_dict)}/{len(codes)}銘柄")
        print(f"  取得モード: {args.mode}")
        print(f"  バッチサイズ: {args.batch_size}")
        print(f"  最終daily_bars件数: {bar_count}")
        print(f"  最終indicators件数: {indicator_count}")

        print("\n" + "=" * 60)
        print("日次更新完了")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
