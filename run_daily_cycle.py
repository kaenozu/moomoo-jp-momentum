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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import pandas as pd

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

Provider = Literal["auto", "moomoo", "yfinance"]

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


def get_yfinance_start_date(
    data_store: DataStore,
    code: str,
    target_date: str,
    latest_bar_count: int,
) -> str:
    """対象日以前のDB最新日を取得開始日にする。データなしなら安全な期間を遡る。"""
    existing = data_store.get_daily_bars(
        code,
        end_date=target_date,
        limit=1,
    )
    if not existing.empty:
        return str(existing.iloc[0]["date"])[:10]

    target = datetime.strptime(target_date, "%Y-%m-%d")
    lookback_days = max(latest_bar_count * 3, 90)
    return (target - timedelta(days=lookback_days)).strftime("%Y-%m-%d")


def load_indicator_bars(
    data_store: DataStore,
    code: str,
    target_date: str,
    limit: int,
) -> pd.DataFrame:
    """DBから対象日以前の指標計算用日足を時系列順で読み込む。"""
    bars = data_store.get_daily_bars(
        code,
        end_date=target_date,
        limit=limit,
    )
    if bars.empty:
        return bars
    bars = bars.rename(columns={"date": "time_key"})
    return bars.sort_values("time_key").reset_index(drop=True)


def fetch_daily_bars(
    quote_service: QuoteService,
    data_store: DataStore,
    code: str,
    target_date: str,
    provider: Provider,
    latest_bar_count: int,
    moomoo_available: bool,
) -> tuple[pd.DataFrame, str, bool]:
    """日足を取得し、使用providerとフォールバック有無を返す。"""
    if provider in {"auto", "moomoo"} and moomoo_available:
        try:
            moomoo_df = quote_service.get_daily_klines_latest_only(
                code,
                num=latest_bar_count,
            )
        except Exception as exc:
            logger.warning("moomoo取得例外: %s - %s", code, exc)
            moomoo_df = pd.DataFrame()

        if not moomoo_df.empty:
            if "time_key" in moomoo_df.columns:
                date_mask = (
                    pd.to_datetime(moomoo_df["time_key"])
                    .dt.strftime("%Y-%m-%d")
                    <= target_date
                )
                moomoo_df = cast(
                    pd.DataFrame,
                    moomoo_df.loc[date_mask].copy(),
                )
            if not moomoo_df.empty:
                return moomoo_df, "moomoo", False

        if provider == "moomoo":
            return pd.DataFrame(), "moomoo", False
        logger.warning("moomoo取得失敗のためyfinanceへフォールバック: %s", code)

    start_date = get_yfinance_start_date(
        data_store,
        code,
        target_date,
        latest_bar_count,
    )
    yfinance_df = quote_service.get_daily_klines_yfinance(
        code,
        start_date=start_date,
        end_date=target_date,
    )
    fallback_used = provider == "auto"
    return yfinance_df, "yfinance", fallback_used


def run_cycle(
    target_date: str,
    dry_run: bool = False,
    config_path: str = "config.yaml",
    allow_stale: bool = False,
    skip_fetch: bool = False,
    provider: Provider = "auto",
) -> dict:
    results: dict[str, int | bool] = {}
    if provider not in {"auto", "moomoo", "yfinance"}:
        raise ValueError(f"未対応providerです: {provider}")

    config = load_config(config_path)
    configured_markets, latest_bar_count = get_daily_cycle_settings(config)
    indicator_bar_count = int(
        config.get("daily_cycle.indicator_bar_count", max(120, latest_bar_count))
    )
    if indicator_bar_count <= 0:
        raise ValueError("daily_cycle.indicator_bar_countは1以上にしてください")

    if dry_run:
        normalized_markets = {str(item).upper() for item in configured_markets}
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

    opend_conn: OpenDConnection | None = None
    quote_ctx = None
    if not skip_fetch and provider in {"auto", "moomoo"}:
        opend_conn = OpenDConnection(config)
        status = opend_conn.connect()
        if status.connected:
            quote_ctx = status.quote_context
        elif provider == "moomoo":
            raise SystemError(f"OpenD接続失敗: {status.message}")
        else:
            logger.warning(
                "OpenD接続失敗のためyfinanceのみで継続します: %s",
                status.message,
            )
    results["connection"] = dry_run or quote_ctx is not None

    try:
        data_store = DataStore(config)
        data_store.sync_symbols_from_json(config.watchlist_file)
        symbols = get_daily_cycle_symbols(data_store, configured_markets)
        codes = [symbol.code for symbol in symbols]
        symbols_info = {symbol.code: symbol.name for symbol in symbols}
        benchmark_codes = {symbol.code for symbol in symbols if symbol.role == "benchmark"}
        results["symbols"] = len(codes)

        data_dict: dict[str, pd.DataFrame] = {}
        moomoo_count = 0
        yfinance_count = 0
        fallback_count = 0
        fetched_count = 0

        if not skip_fetch:
            quote_service = QuoteService(config, quote_ctx)
            for i, code in enumerate(codes, 1):
                logger.info(
                    "[%d/%d] %s の日足を取得中(provider=%s)...",
                    i,
                    len(codes),
                    code,
                    provider,
                )
                fetched_df, used_provider, fallback_used = fetch_daily_bars(
                    quote_service,
                    data_store,
                    code,
                    target_date,
                    provider,
                    latest_bar_count,
                    moomoo_available=quote_ctx is not None,
                )
                if not fetched_df.empty:
                    fetched_count += 1
                    if used_provider == "yfinance":
                        data_store.save_dataframe_to_daily_bars(
                            fetched_df,
                            code,
                            source="yfinance",
                            turnover_source="estimated",
                        )
                        yfinance_count += 1
                    else:
                        data_store.save_dataframe_to_daily_bars(fetched_df, code)
                        moomoo_count += 1
                    if fallback_used:
                        fallback_count += 1

                indicator_bars = load_indicator_bars(
                    data_store,
                    code,
                    target_date,
                    indicator_bar_count,
                )
                if not indicator_bars.empty:
                    data_dict[code] = indicator_bars

        results["daily_bars"] = fetched_count
        results["moomoo_codes"] = moomoo_count
        results["yfinance_codes"] = yfinance_count
        results["fallback_codes"] = fallback_count

        if not skip_fetch and data_dict:
            indicators = calculate_indicators_batch(data_dict, symbols_info)
            indicators_df = indicators_to_dataframe(indicators)
            benchmark_code = config.get(
                "signals.relative_strength.benchmark_code",
                "JP.1306",
            )
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

        if skip_fetch:
            results["virtual_orders"] = 0
            results["fills"] = 0
            results["exits"] = 0
            results["price_updates"] = 0
            results["alerts"] = 0
            logger.info("--skip-fetch指定のため、シグナル生成後に日次サイクルを終了します")
            return results

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
        if opend_conn and quote_ctx:
            opend_conn.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Moomoo 日次運用サイクル")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="基準日",
    )
    parser.add_argument("--dry-run", action="store_true", help="テスト実行")
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="API取得を行わず、既存DBからシグナル生成のみ実行",
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "moomoo", "yfinance"],
        default="auto",
        help="日足取得provider（autoはmoomoo失敗時にyfinanceへフォールバック）",
    )
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
    logger.info(
        "基準日: %s, dry-run: %s, skip-fetch: %s, provider: %s",
        args.date,
        args.dry_run,
        args.skip_fetch,
        args.provider,
    )

    start = time.time()
    try:
        results = run_cycle(
            args.date,
            dry_run=args.dry_run,
            config_path=args.config,
            allow_stale=args.allow_stale,
            skip_fetch=args.skip_fetch,
            provider=args.provider,
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
    print(
        "\n[DONE] dry-run 完了"
        if args.dry_run
        else f"\n[DONE] 日次サイクル完了: {args.date}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
