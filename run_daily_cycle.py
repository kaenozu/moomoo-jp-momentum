"""
日次運用サイクル

毎日同じ手順で、データ更新→シグナル→仮想注文→仮想約定→評価を実行する。
moomoo APIの注文系APIは呼ばない。
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.alerts import AlertManager
from src.config import load_config
from src.connection import OpenDConnection
from src.data_freshness import DataFreshnessGuard
from src.data_store import DataStore
from src.indicators import calculate_indicators_batch, indicators_to_dataframe
from src.quote_service import QuoteService
from src.screener import Screener
from src.virtual_trade import VirtualTradeManager
from daily_update import add_relative_strength, save_benchmark_prices_from_indicators, save_indicators_to_db

logger = logging.getLogger(__name__)


def configure_logging(log_to_file: bool = True, log_dir: str | Path | None = None) -> None:
    """ログ設定を行う（import時には実行しないこと）"""
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


def run_cycle(target_date: str, dry_run: bool = False, config_path: str = "config.yaml") -> dict:
    results: dict[str, int | bool] = {}
    config = load_config(config_path)

    if not dry_run:
        opend_conn = OpenDConnection(config)
        status = opend_conn.connect()
        if not status.connected:
            logger.error("OpenD接続失敗: %s", status.message)
            return results
        quote_ctx = status.quote_context
    else:
        opend_conn = None
        quote_ctx = None
    results["connection"] = True

    if dry_run:
        # dry-run: DataStoreを作らずJSONをread-onlyで検証
        import json
        try:
            wl_file = config.watchlist_file
            with open(wl_file, encoding="utf-8") as f:
                symbols_data: list[dict] = json.load(f)
            jp_symbols = [s for s in symbols_data if isinstance(s, dict) and s.get("code", "").startswith("JP.")]
            codes = [s["code"] for s in jp_symbols]
            symbols_info = {s["code"]: s.get("name", "") for s in jp_symbols}
            benchmark_codes = {s["code"] for s in jp_symbols if s.get("role") == "benchmark"}
            results["symbols"] = len(codes)
        except FileNotFoundError:
            logger.warning("銘柄リストファイルが見つかりません: %s", wl_file)
            codes = []
            symbols_info = {}
            benchmark_codes = set()
            results["symbols"] = 0
        data_dict = {}
        results["daily_bars"] = 0
        results["indicators"] = 0
        results["benchmark_prices"] = 0
        results["signals"] = 0
        results["virtual_orders"] = 0
        results["fills"] = 0
        results["exits"] = 0
        results["price_updates"] = 0
        results["alerts"] = 0
        if opend_conn:
            opend_conn.disconnect()
        return results

    data_store = DataStore(config)
    data_store.sync_symbols_from_json(config.watchlist_file)
    symbols = data_store.get_enabled_symbols(include_benchmarks=True)
    codes = [s.code for s in symbols]
    symbols_info = {s.code: s.name for s in symbols}
    benchmark_codes = {s.code for s in symbols if s.role == "benchmark"}
    results["symbols"] = len(codes)

    if not dry_run and quote_ctx:
        quote_service = QuoteService(config, quote_ctx)
        data_dict = {}
        for i, code in enumerate(codes, 1):
            logger.info("[%d/%d] %s の日足を取得中...", i, len(codes), code)
            df = quote_service.get_daily_klines_with_fallback(code, num=120, start="2025-01-01")
            if not df.empty:
                data_store.save_dataframe_to_daily_bars(df, code)
                data_dict[code] = df
        results["daily_bars"] = len(data_dict)
    else:
        data_dict = {}
        results["daily_bars"] = 0

    if not dry_run and data_dict:
        indicators = calculate_indicators_batch(data_dict, symbols_info)
        indicators_df = indicators_to_dataframe(indicators)
        benchmark_code = config.get("signals.relative_strength.benchmark_code", "JP.1306")
        indicators_df = add_relative_strength(indicators_df, benchmark_code)
        results["indicators"] = save_indicators_to_db(data_store, indicators_df)
        results["benchmark_prices"] = save_benchmark_prices_from_indicators(data_store, indicators_df, benchmark_codes)
    else:
        results["indicators"] = 0
        results["benchmark_prices"] = 0

    # 鮮度チェック（日足更新後、シグナル判定前）
    if not dry_run:
        guard = DataFreshnessGuard(config)
        status = guard.check_freshness()
        if status.level == "error" and status.days_stale < 9000:
            raise SystemError(f"データが古すぎるため処理を停止します: {status.message}")
        if status.level == "warning":
            logger.warning("データが古いですが処理を続行します: %s", status.message)

    if not dry_run:
        screener = Screener(config)
        candidates = screener.screen_candidates(date=target_date)
        results["signals"] = screener.save_signals_to_db(candidates)
    else:
        candidates = []
        results["signals"] = 0

    if not dry_run:
        manager = VirtualTradeManager(config)
        vt_config = config.get("virtual_trade", {})
        score_threshold = vt_config.get("score_threshold_for_order", 70)
        cash = manager.get_cash("default")
        created = 0
        for c in candidates:
            if c.signal_type != "BUY_CANDIDATE":
                continue
            if c.role != "trade_candidate" or not c.tradable:
                continue
            if c.score < score_threshold:
                continue
            if c.close and c.close > cash:
                continue
            order = manager.place_order(
                strategy_name="default",
                code=c.code,
                side="BUY",
                quantity=1,
                order_type="MARKET_SIM",
                submitted_at=c.date,
            )
            if order:
                created += 1
                cash -= c.close or 0
        results["virtual_orders"] = created
    else:
        results["virtual_orders"] = 0

    if not dry_run:
        manager = VirtualTradeManager(config)
        fills = manager.process_fills("default", target_date)
        results["fills"] = len(fills)
        exits = manager.generate_exits("default", target_date)
        results["exits"] = len(exits)
        results["price_updates"] = manager.update_market_prices("default", target_date)
        manager.save_equity_curve("default", target_date)
    else:
        results["fills"] = 0
        results["exits"] = 0
        results["price_updates"] = 0

    if not dry_run:
        alert_mgr = AlertManager(config)
        alerts = alert_mgr.run_all_checks()
        results["alerts"] = len(alerts)
    else:
        results["alerts"] = 0

    if opend_conn and not dry_run:
        opend_conn.disconnect()

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Moomoo 日次運用サイクル")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="基準日")
    parser.add_argument("--dry-run", action="store_true", help="テスト実行")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    args = parser.parse_args()

    # dry-run時はファイル出力なし
    configure_logging(log_to_file=not args.dry_run)

    print("=" * 60)
    print("Moomoo 日次運用サイクル")
    print("=" * 60)
    logger.info("基準日: %s, dry-run: %s", args.date, args.dry_run)

    start = time.time()
    try:
        results = run_cycle(args.date, dry_run=args.dry_run, config_path=args.config)
    except SystemError as e:
        logger.error("日次サイクル停止: %s", e)
        return 1
    except Exception as e:
        logger.error("日次サイクル失敗: %s", e)
        return 1

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print("日次サイクル結果")
    print("=" * 60)
    for k, v in results.items():
        print(f"  {k}: {v}")
    print(f"  所要時間: {elapsed:.1f}秒")
    print("\n[DONE] dry-run 完了" if args.dry_run else f"\n[DONE] 日次サイクル完了: {args.date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
