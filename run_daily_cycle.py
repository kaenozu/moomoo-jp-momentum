"""
日次運用サイクル

毎日同じ手順で、データ更新→シグナル→仮想注文→仮想約定→評価を実行する。
moomoo APIの注文系APIは呼ばない。
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from daily_update import (
    add_relative_strength,
    save_benchmark_prices_from_indicators,
    save_indicators_to_db,
)
from src.alerts import AlertManager
from src.config import load_config
from src.connection import OpenDConnection
from src.data_freshness import DataFreshnessGuard, FreshnessStatus
from src.data_store import DataStore
from src.indicators import calculate_indicators_batch, indicators_to_dataframe
from src.quote_service import QuoteService
from src.screener import Screener
from src.virtual_trade import VirtualTradeManager

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 120


def configure_logging(
    log_to_file: bool = True,
    log_dir: str | Path | None = None,
) -> None:
    """ログ設定を行う。import時には実行しない。"""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_to_file:
        log_path = Path(log_dir or "logs")
        log_path.mkdir(parents=True, exist_ok=True)
        log_file = log_path / f"app_{datetime.now().strftime('%Y%m%d')}.log"
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def _validate_watchlist_for_dry_run(config) -> tuple[int, int]:
    """watchlistを読み取り専用で検証し、JP銘柄数とbenchmark数を返す。"""
    watchlist_path = Path(config.watchlist_file)
    with watchlist_path.open(encoding="utf-8") as file:
        raw = json.load(file)

    if not isinstance(raw, list):
        raise RuntimeError(
            "watchlist JSONのトップレベルがlistではありません: "
            f"{type(raw).__name__}"
        )

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeError(f"watchlist[{index}]がobjectではありません")
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            raise RuntimeError(f"watchlist[{index}]に有効なcodeがありません")

    jp_symbols = [item for item in raw if item["code"].startswith("JP.")]
    if not jp_symbols:
        raise RuntimeError("JP. 銘柄が0件です")

    benchmark_codes = {
        item["code"]
        for item in jp_symbols
        if item.get("role") == "benchmark"
    }
    if not benchmark_codes:
        raise RuntimeError("benchmark が0件です")

    return len(jp_symbols), len(benchmark_codes)


def _assert_cycle_data_freshness(
    config,
    codes: list[str],
    target_date: str,
) -> dict[str, FreshnessStatus]:
    """日次処理で使用する全enabled銘柄を対象日基準で個別検証する。"""
    max_stale_days = int(config.get("data_freshness.max_stale_days", 5))
    guard = DataFreshnessGuard(config)
    return guard.assert_required_codes_fresh_or_stop(
        codes,
        reference_date=target_date,
        max_stale_days=max_stale_days,
        table_name="daily_bars",
    )


def _load_indicator_inputs(
    data_store: DataStore,
    codes: list[str],
    target_date: str,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> dict[str, pd.DataFrame]:
    """全対象銘柄の指標入力をDBから対象日以前に限定して読み込む。

    APIレスポンスは保存処理の入力にだけ使い、指標計算ではSQLiteを正規の
    データソースにする。これにより一時的なAPI取得失敗で銘柄がクロス
    セクション計算から脱落せず、過去日実行で未来データも混入しない。
    """
    if history_limit <= 0:
        raise ValueError("history_limitは1以上で指定してください")

    normalized_codes = list(
        dict.fromkeys(code.strip() for code in codes if code.strip())
    )
    loaded_inputs = data_store.get_daily_bars_for_codes(
        normalized_codes,
        end_date=target_date,
        limit_per_code=history_limit,
    )

    missing_codes = [code for code in normalized_codes if code not in loaded_inputs]
    if missing_codes:
        missing_codes_text = ", ".join(missing_codes)
        logger.error(
            "鮮度確認後にDB日足を読み込めない銘柄があります: target=%s, codes=%s",
            target_date,
            missing_codes_text,
        )
        raise SystemError(
            "指標計算用の日足をDBから取得できない銘柄があります: "
            + missing_codes_text
        )

    return {code: loaded_inputs[code] for code in normalized_codes}


def run_cycle(
    target_date: str,
    dry_run: bool = False,
    config_path: str = "config.yaml",
) -> dict:
    results: dict[str, int | bool | str] = {
        "connection_attempted": False,
        "database_write_attempted": False,
    }
    config = load_config(config_path)

    if dry_run:
        virtual_trade_config = config.get("virtual_trade", {})
        results["virtual_trade_enabled"] = virtual_trade_config.get(
            "enabled",
            True,
        )
        symbol_count, benchmark_count = _validate_watchlist_for_dry_run(config)
        results["symbols"] = symbol_count
        results["benchmarks"] = benchmark_count
        return results

    results["connection_attempted"] = True
    opend_conn = OpenDConnection(config)
    status = opend_conn.connect()
    if not status.connected:
        logger.error("OpenD接続失敗: %s", status.message)
        return results
    quote_ctx = status.quote_context

    try:
        results["database_write_attempted"] = True
        data_store = DataStore(config)
        data_store.sync_symbols_from_json(config.watchlist_file)
        symbols = data_store.get_enabled_symbols(include_benchmarks=True)
        codes = [symbol.code for symbol in symbols]
        symbols_info = {symbol.code: symbol.name for symbol in symbols}
        benchmark_codes = {
            symbol.code for symbol in symbols if symbol.role == "benchmark"
        }
        results["symbols"] = len(codes)

        fetched_symbols = 0
        if quote_ctx:
            quote_service = QuoteService(config, quote_ctx)
            for index, code in enumerate(codes, 1):
                logger.info("[%d/%d] %s の日足を取得中...", index, len(codes), code)
                dataframe = quote_service.get_daily_klines_with_fallback(
                    code,
                    num=DEFAULT_HISTORY_LIMIT,
                    start="2025-01-01",
                )
                if not dataframe.empty:
                    data_store.save_dataframe_to_daily_bars(dataframe, code)
                    fetched_symbols += 1
        results["daily_bars"] = fetched_symbols
        results["fetched_symbols"] = fetched_symbols

        freshness_by_code = _assert_cycle_data_freshness(
            config,
            codes,
            target_date,
        )
        results["fresh_symbols"] = sum(
            1 for status in freshness_by_code.values() if status.is_fresh
        )
        results["freshness_warnings"] = sum(
            1 for status in freshness_by_code.values() if status.level == "warning"
        )

        data_dict = _load_indicator_inputs(
            data_store,
            codes,
            target_date,
            history_limit=DEFAULT_HISTORY_LIMIT,
        )
        results["indicator_input_symbols"] = len(data_dict)

        indicators = calculate_indicators_batch(data_dict, symbols_info)
        indicators_df = indicators_to_dataframe(indicators)
        benchmark_code = config.get(
            "signals.relative_strength.benchmark_code",
            "JP.1306",
        )
        indicators_df = add_relative_strength(indicators_df, benchmark_code)
        results["indicators"] = save_indicators_to_db(
            data_store,
            indicators_df,
        )
        results["benchmark_prices"] = save_benchmark_prices_from_indicators(
            data_store,
            indicators_df,
            benchmark_codes,
        )

        screener = Screener(config)
        candidates = screener.screen_candidates(date=target_date)
        results["signals"] = screener.save_signals_to_db(candidates)

        manager = VirtualTradeManager(config)
        virtual_trade_config = config.get("virtual_trade", {})
        score_threshold = virtual_trade_config.get("score_threshold_for_order", 70)
        created = 0
        for candidate in candidates:
            if candidate.signal_type != "BUY_CANDIDATE":
                continue
            if candidate.role != "trade_candidate" or not candidate.tradable:
                continue
            if candidate.score < score_threshold:
                continue
            order = manager.place_order(
                strategy_name="default",
                code=candidate.code,
                side="BUY",
                quantity=1,
                order_type="MARKET_SIM",
                submitted_at=candidate.date,
            )
            if order:
                created += 1
        results["virtual_orders"] = created

        manager = VirtualTradeManager(config)
        fills = manager.process_fills("default", target_date)
        results["fills"] = len(fills)
        exits = manager.generate_exits("default", target_date)
        results["exits"] = len(exits)
        results["price_updates"] = manager.update_market_prices(
            "default",
            target_date,
        )
        manager.save_equity_curve("default", target_date)

        alert_manager = AlertManager(config)
        alerts = alert_manager.run_all_checks()
        results["alerts"] = len(alerts)
        return results
    finally:
        opend_conn.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Moomoo 日次運用サイクル")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="基準日",
    )
    parser.add_argument("--dry-run", action="store_true", help="テスト実行")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    args = parser.parse_args()

    configure_logging(log_to_file=not args.dry_run)

    print("=" * 60)
    print("Moomoo 日次運用サイクル")
    print("=" * 60)
    logger.info("基準日: %s, dry-run: %s", args.date, args.dry_run)

    start = time.time()
    try:
        results = run_cycle(
            args.date,
            dry_run=args.dry_run,
            config_path=args.config,
        )
    except SystemError as error:
        logger.error("日次サイクル停止: %s", error)
        return 1
    except Exception as error:
        logger.error("日次サイクル失敗: %s", error)
        return 1

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print("日次サイクル結果")
    print("=" * 60)
    for key, value in results.items():
        print(f"  {key}: {value}")
    print(f"  所要時間: {elapsed:.1f}秒")
    print(
        "\n[DONE] dry-run 完了"
        if args.dry_run
        else f"\n[DONE] 日次サイクル完了: {args.date}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
