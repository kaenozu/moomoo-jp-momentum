"""
日次運用サイクル

ファイルパス: run_daily_cycle.py
何をするか: 日次運用の全ステップを順番に実行する
なぜ存在するか: 毎日同じ手順で動かして戦略成績を正しく評価するため
関連ファイル: daily_update.py, screen_candidates.py, virtual_order.py, process_virtual_fills.py

使い方:
    python run_daily_cycle.py
    python run_daily_cycle.py --date 2026-07-01
    python run_daily_cycle.py --dry-run
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.data_freshness import DataFreshnessGuard
from src.connection import OpenDConnection
from src.data_store import DataStore
from src.quote_service import QuoteService
from src.indicators import calculate_indicators_batch, indicators_to_dataframe
from src.screener import Screener
from src.virtual_trade import VirtualTradeManager
from src.alerts import AlertManager

# ログ設定
log_dir = Path("logs")
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / f"app_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def step(name: str, func, *args, **kwargs) -> any:
    """ステップ実行とログ出力"""
    logger.info("=== %s ===", name)
    try:
        result = func(*args, **kwargs)
        logger.info("[OK] %s", name)
        return result
    except Exception as e:
        logger.error("[FAIL] %s: %s", name, e)
        raise


def run_cycle(target_date: str, dry_run: bool = False) -> dict:
    """日次サイクルを実行"""
    results = {}

    config = load_config("config.yaml")

    # 0. データ鮮度チェック（初回はスキップ、データがある場合のみチェック）
    if not dry_run:
        guard = DataFreshnessGuard(config)
        status = guard.check_freshness()
        if status.level == "error" and status.days_stale < 9000:
            # データはあるが古すぎる場合のみ停止
            raise SystemError(
                f"データが古すぎるため処理を停止します: {status.message}\n"
                f"強制実行する場合は --allow-stale オプションを使用してください"
            )
        elif status.level == "warning":
            logger.warning("データが古いですが処理を続行します: %s", status.message)
        # level=="error" かつ days_stale>=9000（データなし）は初回実行とみなしてスキップ

    # 1. OpenD接続確認
    if not dry_run:
        opend_conn = OpenDConnection(config)
        status = opend_conn.connect()
        if not status.connected:
            logger.error("OpenD接続失敗: %s", status.message)
            return results
        quote_ctx = status.quote_context
    else:
        quote_ctx = None
        opend_conn = None
    results["connection"] = True

    # 2. データストア初期化
    data_store = DataStore(config)
    symbols = data_store.get_enabled_symbols()
    codes = [s.code for s in symbols]
    symbols_info = {s.code: s.name for s in symbols}
    results["symbols"] = len(codes)

    # 3. 日足更新
    if not dry_run and quote_ctx:
        quote_service = QuoteService(config, quote_ctx)
        num_days = 120
        data_dict = {}
        for i, code in enumerate(codes):
            logger.info("[%d/%d] %s の日足を取得中...", i + 1, len(codes), code)
            df = quote_service.get_daily_klines_with_fallback(code, num=120, start="2025-01-01")
            if not df.empty:
                count = data_store.save_dataframe_to_daily_bars(df, code)
                data_dict[code] = df
                logger.info("  保存完了: %d件", count)
        results["daily_bars"] = len(data_dict)
    else:
        data_dict = {}
        results["daily_bars"] = 0

    # 4. 指標計算・保存
    if not dry_run and data_dict:
        indicators = calculate_indicators_batch(data_dict, symbols_info)
        indicators_df = indicators_to_dataframe(indicators)
        # SQLite保存
        import sqlite3
        sql = """
            INSERT OR REPLACE INTO indicators
            (code, date, close, volume, turnover, daily_return,
             ma5, ma25, high_20d, distance_from_high_20d,
             volume_ma20, volume_ratio, return_5d, history_days, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        now = datetime.now().isoformat()
        params = []
        for _, row in indicators_df.iterrows():
            params.append((
                row.get("code"), row.get("date"), row.get("close"),
                row.get("volume"), row.get("turnover"), row.get("daily_return"),
                row.get("ma5"), row.get("ma25"), row.get("high_20d"),
                row.get("high_20d_distance"), row.get("volume_ma20"),
                row.get("volume_ratio"), row.get("return_5d"),
                row.get("history_days"), now,
            ))
        with sqlite3.connect(str(config.database_path)) as conn:
            conn.executemany(sql, params)
        results["indicators"] = len(indicators)
    else:
        results["indicators"] = 0

    # 5. シグナル判定・保存
    if not dry_run:
        screener = Screener(config)
        candidates = screener.screen_candidates(date=target_date)
        saved = screener.save_signals_to_db(candidates)
        results["signals"] = saved
    else:
        results["signals"] = 0

    # 6. 仮想注文作成（from-signals）
    if not dry_run:
        manager = VirtualTradeManager(config)
        from src.screener import Screener as Scr
        scr = Scr(config)
        cands = scr.screen_candidates(date=target_date)
        universe_config = config.get("universe", {})
        max_trade_price = universe_config.get("max_trade_price", 20000)
        vt_config = config.get("virtual_trade", {})
        score_threshold = vt_config.get("score_threshold_for_order", 70)
        cash = manager.get_cash("default")
        positions = manager.get_positions("default")
        created = 0
        for c in cands:
            if c.signal_type != "BUY_CANDIDATE":
                continue
            if c.score < score_threshold:
                continue
            if c.close and c.close > max_trade_price:
                continue
            if any(p.code == c.code for p in positions):
                continue
            if len(positions) >= manager.max_total_positions:
                continue
            if c.close and c.close > cash:
                continue
            order = manager.place_order(
                strategy_name="default", code=c.code, side="BUY", quantity=1,
                order_type="MARKET_SIM",
            )
            if order:
                created += 1
                cash -= c.close or 0
                positions = manager.get_positions("default")
        results["virtual_orders"] = created
    else:
        results["virtual_orders"] = 0

    # 7. 仮想約定処理
    if not dry_run:
        manager = VirtualTradeManager(config)
        fills = manager.process_fills("default", target_date)
        results["fills"] = len(fills)
    else:
        results["fills"] = 0

    # 8. 売却候補生成
    if not dry_run:
        manager = VirtualTradeManager(config)
        exits = manager.generate_exits("default", target_date)
        results["exits"] = len(exits)
    else:
        results["exits"] = 0

    # 9. 現在値更新
    if not dry_run:
        manager = VirtualTradeManager(config)
        updated = manager.update_market_prices("default", target_date)
        results["price_updates"] = updated
    else:
        results["price_updates"] = 0

    # 10. アラート
    if not dry_run:
        alert_mgr = AlertManager(config)
        alerts = alert_mgr.run_all_checks()
        results["alerts"] = len(alerts)
    else:
        results["alerts"] = 0

    if opend_conn and not dry_run:
        opend_conn.disconnect()

    return results


def main():
    parser = argparse.ArgumentParser(description="Moomoo 日次運用サイクル")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="基準日")
    parser.add_argument("--dry-run", action="store_true", help="テスト実行")
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo 日次運用サイクル")
    print("=" * 60)
    logger.info("基準日: %s, dry-run: %s", args.date, args.dry_run)

    start = time.time()
    try:
        results = run_cycle(args.date, dry_run=args.dry_run)
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

    if args.dry_run:
        print("\n[DONE] dry-run 完了")
    else:
        print(f"\n[DONE] 日次サイクル完了: {args.date}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
