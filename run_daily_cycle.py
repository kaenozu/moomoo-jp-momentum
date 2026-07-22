"""
日次運用サイクル。

毎日同じ手順で、データ更新→シグナル→仮想注文→仮想約定→評価を実行する。
moomoo APIの注文系APIは呼ばない。
"""

import argparse
import json
import logging
import sys
import time
from collections.abc import Collection
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from daily_update import (
    add_relative_strength,
    save_benchmark_prices_from_indicators,
    save_indicators_to_db,
)
from src.alerts import AlertManager
from src.config import Config, load_config
from src.connection import OpenDConnection
from src.data_freshness import DataFreshnessGuard
from src.data_store import DataStore
from src.indicators import calculate_indicators_batch, indicators_to_dataframe
from src.models import Symbol
from src.quote_service import QuoteService
from src.screener import Screener
from src.virtual_trade import VirtualTradeManager

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def _configure_file_logging() -> None:
    """実運用CLIでだけファイルログを有効化する。"""
    root_logger = logging.getLogger()
    if any(isinstance(handler, logging.FileHandler) for handler in root_logger.handlers):
        return
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"app_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(file_handler)


def get_daily_cycle_symbols(
    data_store: DataStore,
    markets: Collection[str],
) -> list[Symbol]:
    """dry-runと実運用で共通の日次サイクル対象銘柄を取得する。"""
    return data_store.get_enabled_symbols(
        include_benchmarks=True,
        markets=markets,
    )


def get_daily_cycle_settings(config: Config) -> tuple[Collection[str], int]:
    """日次サイクルの市場と取得件数を設定から取得する。"""
    markets = config.get("daily_cycle.markets", ["JP"])
    fetch_mode = str(config.get("daily_cycle.fetch_mode", "latest")).lower()
    latest_bar_count = int(config.get("daily_cycle.latest_bar_count", 120))

    if fetch_mode != "latest":
        raise ValueError("daily_cycle.fetch_modeはlatestのみ指定できます")
    if latest_bar_count <= 0:
        raise ValueError("daily_cycle.latest_bar_countは1以上にしてください")

    return markets, latest_bar_count


def _available_cash(
    manager: VirtualTradeManager,
    strategy_name: str,
    target_date: str,
) -> float:
    """新実装と軽量テストスタブの両方から利用可能cashを取得する。"""
    try:
        return float(manager.get_available_cash(strategy_name, target_date))
    except AttributeError:
        return float(manager.get_cash(strategy_name))


def run_cycle(
    target_date: str,
    dry_run: bool = False,
    config_path: str = "config.yaml",
    allow_stale: bool = False,
) -> dict:
    config = load_config(config_path)
    configured_markets, latest_bar_count = get_daily_cycle_settings(config)
    normalized_markets = {str(item).upper() for item in configured_markets}

    if dry_run:
        watchlist_path = Path(config.watchlist_file)
        try:
            raw_symbols = json.loads(watchlist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"watchlistを読み込めません: {exc}") from exc
        if not isinstance(raw_symbols, list):
            raise RuntimeError("watchlistのトップレベルがlistではありません")
        selected = [
            item
            for item in raw_symbols
            if isinstance(item, dict)
            and item.get("enabled", True)
            and str(item.get("market", "JP")).upper() in normalized_markets
        ]
        benchmark_count = sum(
            1 for item in selected if item.get("role") == "benchmark"
        )
        if benchmark_count == 0:
            raise RuntimeError("日次対象にbenchmark が0件です")
        return {
            "connection_attempted": False,
            "database_write_attempted": False,
            "virtual_trade_enabled": bool(config.get("virtual_trade.enabled", True)),
            "symbols": len(selected),
            "benchmarks": benchmark_count,
        }

    results: dict[str, int | bool] = {}
    connection = OpenDConnection(config)
    try:
        status = connection.connect()
        if not status.connected or status.quote_context is None:
            raise RuntimeError(f"OpenD接続失敗: {status.message}")
        results["connection"] = True

        store = DataStore(config)
        store.sync_symbols_from_json(config.watchlist_file)
        symbols = get_daily_cycle_symbols(store, configured_markets)
        codes = [symbol.code for symbol in symbols]
        names = {symbol.code: symbol.name for symbol in symbols}
        benchmarks = {symbol.code for symbol in symbols if symbol.role == "benchmark"}
        results["symbols"] = len(codes)

        quote_service = QuoteService(config, status.quote_context)
        updated = 0
        for index, code in enumerate(codes, 1):
            logger.info("[%d/%d] %s の日足を取得中...", index, len(codes), code)
            frame = quote_service.get_daily_klines_latest_only(
                code,
                num=latest_bar_count,
            )
            if not frame.empty:
                store.save_dataframe_to_daily_bars(frame, code)
                updated += 1
        results["daily_bars"] = updated

        history_limit = max(120, latest_bar_count)
        data_dict = {}
        for code in codes:
            history = store.get_daily_bars(
                code,
                end_date=target_date,
                limit=history_limit,
            )
            if not history.empty:
                history = history.sort_values("date").rename(
                    columns={"date": "time_key"}
                )
                data_dict[code] = history

        if data_dict:
            indicators = calculate_indicators_batch(data_dict, names)
            indicators_df = indicators_to_dataframe(indicators)
            benchmark_code = config.get(
                "signals.relative_strength.benchmark_code",
                "JP.1306",
            )
            indicators_df = add_relative_strength(indicators_df, benchmark_code)
            results["indicators"] = save_indicators_to_db(store, indicators_df)
            results["benchmark_prices"] = save_benchmark_prices_from_indicators(
                store,
                indicators_df,
                benchmarks,
            )
        else:
            results["indicators"] = 0
            results["benchmark_prices"] = 0

        freshness = DataFreshnessGuard(config).check_freshness()
        if freshness.level == "error":
            raise SystemError(f"データ鮮度エラー: {freshness.message}")
        if freshness.level == "warning" and not allow_stale:
            raise SystemError(
                f"データが古いため処理を停止します: {freshness.message}"
            )
        if freshness.level == "warning":
            logger.warning("古いデータで明示続行します: %s", freshness.message)

        screener = Screener(config)
        candidates = screener.screen_candidates(date=target_date)
        results["signals"] = screener.save_signals_to_db(candidates)

        manager = VirtualTradeManager(config)
        threshold = config.get("virtual_trade.score_threshold_for_order", 70)
        available_cash = _available_cash(manager, "default", target_date)
        created = 0
        for candidate in candidates:
            if candidate.signal_type != "BUY_CANDIDATE":
                continue
            if candidate.role != "trade_candidate" or not candidate.tradable:
                continue
            if candidate.score < threshold:
                continue
            if candidate.close and candidate.close > available_cash:
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
                available_cash = _available_cash(manager, "default", target_date)
        results["virtual_orders"] = created
        results["fills"] = len(manager.process_fills("default", target_date))
        results["exits"] = len(manager.generate_exits("default", target_date))
        results["price_updates"] = manager.update_market_prices(
            "default",
            target_date,
        )
        manager.save_equity_curve("default", target_date)
        results["alerts"] = len(AlertManager(config).run_all_checks())
        return results
    finally:
        connection.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Moomoo 日次運用サイクル")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="基準日",
    )
    parser.add_argument("--dry-run", action="store_true", help="テスト実行")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="古いデータでの続行を明示許可",
    )
    args = parser.parse_args()

    if not args.dry_run:
        _configure_file_logging()

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
            allow_stale=args.allow_stale,
        )
    except SystemError as exc:
        logger.error("日次サイクル停止: %s", exc)
        return 1
    except Exception as exc:
        logger.error("日次サイクル失敗: %s", exc)
        return 1

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print("日次サイクル結果")
    print("=" * 60)
    for key, value in results.items():
        print(f"  {key}: {value}")
    print(f"  所要時間: {elapsed:.1f}秒")
    print("\n[DONE] dry-run 完了" if args.dry_run else f"\n[DONE] 日次サイクル完了: {args.date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
