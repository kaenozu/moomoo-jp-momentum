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
from collections.abc import Collection
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from daily_update import add_relative_strength, save_benchmark_prices_from_indicators, save_indicators_to_db
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)
_file_logging_configured = False


def configure_live_file_logging() -> None:
    """実運用時だけログファイルを作成する。dry-runとimportはread-onlyに保つ。"""
    global _file_logging_configured
    if _file_logging_configured:
        return
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"app_{datetime.now().strftime('%Y%m%d')}.log"
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(formatter)
    logging.getLogger().addHandler(handler)
    _file_logging_configured = True


def get_daily_cycle_symbols(
    data_store: DataStore,
    markets: Collection[str],
) -> list[Symbol]:
    """実運用の日次サイクル対象銘柄を取得する。"""
    return data_store.get_enabled_symbols(
        include_benchmarks=True,
        markets=markets,
    )


def get_daily_cycle_settings(config: Config) -> tuple[Collection[str], int]:
    """日次サイクルの市場と取得件数を設定から取得する。"""
    markets = config.get("daily_cycle.markets", ["JP"])
    fetch_mode = str(config.get("daily_cycle.fetch_mode", "latest")).lower()
    latest_bar_count = int(config.get("daily_cycle.latest_bar_count", 30))

    if fetch_mode != "latest":
        raise ValueError("daily_cycle.fetch_modeはlatestのみ指定できます")
    if latest_bar_count <= 0:
        raise ValueError("daily_cycle.latest_bar_countは1以上にしてください")

    return markets, latest_bar_count


def inspect_dry_run_inputs(
    config: Config,
    markets: Collection[str],
) -> dict[str, int | bool]:
    """外部接続やDB初期化をせず、日次サイクル入力だけを検証する。"""
    watchlist_path = Path(config.watchlist_file)
    try:
        with watchlist_path.open(encoding="utf-8") as file:
            raw_watchlist = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"watchlistを読み込めません: {watchlist_path}") from error

    if not isinstance(raw_watchlist, list):
        raise RuntimeError("watchlistのトップレベルがlistではありません")

    allowed_markets = {str(market).upper() for market in markets}
    symbols = 0
    benchmarks = 0
    for index, item in enumerate(raw_watchlist):
        if not isinstance(item, dict):
            raise RuntimeError(f"watchlist[{index}]がobjectではありません")
        code = str(item.get("code", "")).strip()
        if not code:
            raise RuntimeError(f"watchlist[{index}]にcodeがありません")
        if not bool(item.get("enabled", True)):
            continue
        market = str(item.get("market", "JP")).upper()
        if allowed_markets and market not in allowed_markets:
            continue
        symbols += 1
        if str(item.get("role", "trade_candidate")) == "benchmark":
            benchmarks += 1

    if benchmarks == 0:
        raise RuntimeError("watchlistのbenchmark が0件です")

    return {
        "connection_attempted": False,
        "database_write_attempted": False,
        "virtual_trade_enabled": bool(config.get("virtual_trade.enabled", False)),
        "symbols": symbols,
        "benchmarks": benchmarks,
    }


def run_cycle(target_date: str, dry_run: bool = False, config_path: str = "config.yaml") -> dict:
    results: dict[str, int | bool] = {}
    config = load_config(config_path)
    configured_markets, latest_bar_count = get_daily_cycle_settings(config)

    if dry_run:
        return inspect_dry_run_inputs(config, configured_markets)

    configure_live_file_logging()
    opend_conn = OpenDConnection(config)
    status = opend_conn.connect()
    if not status.connected:
        logger.error("OpenD接続失敗: %s", status.message)
        return results
    quote_ctx = status.quote_context
    results["connection"] = True

    data_store = DataStore(config)
    data_store.sync_symbols_from_json(config.watchlist_file)
    symbols = get_daily_cycle_symbols(data_store, configured_markets)
    codes = [symbol.code for symbol in symbols]
    symbols_info = {symbol.code: symbol.name for symbol in symbols}
    benchmark_codes = {symbol.code for symbol in symbols if symbol.role == "benchmark"}
    results["symbols"] = len(codes)

    if quote_ctx:
        quote_service = QuoteService(config, quote_ctx)
        data_dict = {}
        for index, code in enumerate(codes, 1):
            logger.info("[%d/%d] %s の日足を取得中...", index, len(codes), code)
            dataframe = quote_service.get_daily_klines_latest_only(code, num=latest_bar_count)
            if not dataframe.empty:
                data_store.save_dataframe_to_daily_bars(dataframe, code)
                data_dict[code] = dataframe
        results["daily_bars"] = len(data_dict)
    else:
        data_dict = {}
        results["daily_bars"] = 0

    if data_dict:
        indicators = calculate_indicators_batch(data_dict, symbols_info)
        indicators_df = indicators_to_dataframe(indicators)
        benchmark_code = config.get("signals.relative_strength.benchmark_code", "JP.1306")
        indicators_df = add_relative_strength(indicators_df, benchmark_code)
        results["indicators"] = save_indicators_to_db(data_store, indicators_df)
        results["benchmark_prices"] = save_benchmark_prices_from_indicators(
            data_store,
            indicators_df,
            benchmark_codes,
        )
    else:
        results["indicators"] = 0
        results["benchmark_prices"] = 0

    guard = DataFreshnessGuard(config)
    freshness = guard.check_freshness()
    if freshness.level == "error" and freshness.days_stale < 9000:
        raise SystemError(f"データが古すぎるため処理を停止します: {freshness.message}")
    if freshness.level == "warning":
        logger.warning("データが古いですが処理を続行します: %s", freshness.message)

    screener = Screener(config)
    candidates = screener.screen_candidates(date=target_date)
    results["signals"] = screener.save_signals_to_db(candidates)

    manager = VirtualTradeManager(config)
    vt_config = config.get("virtual_trade", {})
    score_threshold = vt_config.get("score_threshold_for_order", 70)
    cash = manager.get_cash("default")
    created = 0
    for candidate in candidates:
        if candidate.signal_type != "BUY_CANDIDATE":
            continue
        if candidate.role != "trade_candidate" or not candidate.tradable:
            continue
        if candidate.score < score_threshold:
            continue
        if candidate.close and candidate.close > cash:
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
            cash -= candidate.close or 0
    results["virtual_orders"] = created

    fills = manager.process_fills("default", target_date)
    results["fills"] = len(fills)
    exits = manager.generate_exits("default", target_date)
    results["exits"] = len(exits)
    results["price_updates"] = manager.update_market_prices("default", target_date)
    manager.save_equity_curve("default", target_date)

    alert_mgr = AlertManager(config)
    alerts = alert_mgr.run_all_checks()
    results["alerts"] = len(alerts)

    opend_conn.disconnect()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Moomoo 日次運用サイクル")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="基準日")
    parser.add_argument("--dry-run", action="store_true", help="テスト実行")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo 日次運用サイクル")
    print("=" * 60)
    logger.info("基準日: %s, dry-run: %s", args.date, args.dry_run)

    start = time.time()
    try:
        results = run_cycle(args.date, dry_run=args.dry_run, config_path=args.config)
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
    print("\n[DONE] dry-run 完了" if args.dry_run else f"\n[DONE] 日次サイクル完了: {args.date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
