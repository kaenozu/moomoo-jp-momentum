"""ペーパートレードの日次運用サイクル。

1回の実行で、日足差分取得、指標・シグナル生成、前日注文の翌営業日始値約定、
ポジション更新、新規注文生成、日次JSONレポート出力まで完了する。
moomoo APIの注文系APIは呼ばない。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Collection
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

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
from src.screener import Candidate, Screener
from src.virtual_trade import VirtualFill, VirtualOrder, VirtualPosition, VirtualTradeManager
from src.yfinance_data import (
    fetch_adjusted_history,
    incremental_start_date,
    record_splits,
    upsert_yfinance_bars,
)

Provider = Literal["auto", "moomoo", "yfinance"]
JST = ZoneInfo("Asia/Tokyo")
UTC = ZoneInfo("UTC")
logger = logging.getLogger(__name__)


def configure_logging(*, dry_run: bool) -> None:
    """CLI用ロギングを設定する。dry-runではファイルを作らない。"""
    if logging.getLogger().handlers:
        return
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if not dry_run:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"app_{datetime.now(JST).strftime('%Y%m%d')}.log"
        handlers.insert(0, logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def current_timestamp() -> str:
    """監査用のUTCタイムスタンプを返す。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


def calculate_config_hash(config_path: str | Path) -> str:
    """設定ファイル内容のSHA-256を返す。"""
    return hashlib.sha256(Path(config_path).read_bytes()).hexdigest()


def get_git_sha() -> str:
    """実行中チェックアウトのGit SHA。取得不能時はCI環境変数へフォールバック。"""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        sha = completed.stdout.strip()
        if sha:
            return sha
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("GITHUB_SHA", "unknown")


def get_daily_cycle_symbols(
    data_store: DataStore,
    markets: Collection[str],
) -> list[Symbol]:
    """日次サイクル対象銘柄を取得する。"""
    return data_store.get_enabled_symbols(
        include_benchmarks=True,
        markets=markets,
    )


def get_daily_cycle_settings(config: Config) -> tuple[Collection[str], int, int]:
    """市場、差分取得件数、指標計算用履歴件数を設定から取得する。"""
    markets = config.get("daily_cycle.markets", ["JP"])
    fetch_mode = str(config.get("daily_cycle.fetch_mode", "latest")).lower()
    latest_bar_count = int(config.get("daily_cycle.latest_bar_count", 30))
    indicator_bar_count = int(
        config.get("daily_cycle.indicator_bar_count", max(120, latest_bar_count))
    )
    if fetch_mode != "latest":
        raise ValueError("daily_cycle.fetch_modeはlatestのみ指定できます")
    if latest_bar_count <= 0:
        raise ValueError("daily_cycle.latest_bar_countは1以上にしてください")
    if indicator_bar_count <= 0:
        raise ValueError("daily_cycle.indicator_bar_countは1以上にしてください")
    return markets, latest_bar_count, indicator_bar_count


def validate_execution_model(config: Config) -> dict[str, Any]:
    """バックテストと同じ翌営業日始値約定モデルを強制する。"""
    vt_config = config.get("virtual_trade", {})
    fill_mode = str(vt_config.get("market_fill_mode", "next_day_open"))
    if fill_mode != "next_day_open":
        raise ValueError(
            "virtual_trade.market_fill_modeはnext_day_openにしてください "
            f"(actual={fill_mode})"
        )
    return {
        "market_fill_mode": fill_mode,
        "slippage_bps": float(vt_config.get("slippage_bps", 0)),
        "commission": float(vt_config.get("commission", 0)),
    }


def load_indicator_bars(
    data_store: DataStore,
    code: str,
    target_date: str,
    limit: int,
) -> pd.DataFrame:
    """DBから対象日以前の日足を時系列順で読み込む。"""
    bars = data_store.get_daily_bars(code, end_date=target_date, limit=limit)
    if bars.empty:
        return bars
    bars = bars.rename(columns={"date": "time_key"})
    return bars.sort_values("time_key").reset_index(drop=True)


def _filter_moomoo_rows(df: pd.DataFrame, target_date: str) -> pd.DataFrame:
    if df.empty or "time_key" not in df.columns:
        return df
    mask = pd.to_datetime(df["time_key"]).dt.strftime("%Y-%m-%d") <= target_date
    return cast(pd.DataFrame, df.loc[mask].copy())


def fetch_symbol_daily_bars(
    *,
    code: str,
    target_date: str,
    provider: Provider,
    latest_bar_count: int,
    data_store: DataStore,
    quote_service: QuoteService | None,
) -> dict[str, Any]:
    """1銘柄の日足差分を取得・保存する。"""
    if provider in {"auto", "moomoo"} and quote_service is not None:
        try:
            moomoo_df = _filter_moomoo_rows(
                quote_service.get_daily_klines_latest_only(
                    code,
                    num=latest_bar_count,
                ),
                target_date,
            )
        except Exception as exc:
            logger.warning("moomoo取得例外: %s - %s", code, exc)
            moomoo_df = pd.DataFrame()
        if not moomoo_df.empty:
            written = data_store.save_dataframe_to_daily_bars(moomoo_df, code)
            return {
                "code": code,
                "provider": "moomoo",
                "rows": int(written),
                "splits": 0,
                "preserved_rows": 0,
                "fallback": False,
            }
        if provider == "moomoo":
            return {
                "code": code,
                "provider": "moomoo",
                "rows": 0,
                "splits": 0,
                "preserved_rows": 0,
                "fallback": False,
                "error": "empty",
            }
        logger.warning("moomoo取得失敗のためyfinanceへフォールバック: %s", code)

    start_date = incremental_start_date(
        data_store.db_path,
        code,
        target_date,
        fallback_days=max(latest_bar_count * 4, 120),
    )
    try:
        fetched = fetch_adjusted_history(code, start_date, target_date)
        stats = upsert_yfinance_bars(data_store.db_path, code, fetched.bars)
        split_count = record_splits(data_store.db_path, code, fetched.splits)
    except Exception as exc:
        logger.warning("yfinance取得失敗: %s - %s", code, exc)
        return {
            "code": code,
            "provider": "yfinance",
            "rows": 0,
            "splits": 0,
            "preserved_rows": 0,
            "fallback": provider == "auto",
            "error": str(exc),
        }
    return {
        "code": code,
        "provider": "yfinance",
        "rows": stats.written,
        "splits": split_count,
        "preserved_rows": stats.preserved,
        "fallback": provider == "auto",
        **({"error": "empty"} if fetched.bars.empty else {}),
    }


def get_data_as_of_date(
    db_path: str | Path,
    codes: Collection[str],
    target_date: str,
) -> str | None:
    if not codes:
        return None
    placeholders = ",".join("?" for _ in codes)
    params = [*codes, target_date]
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            f"""
            SELECT MAX(date)
            FROM daily_bars
            WHERE code IN ({placeholders}) AND date <= ?
            """,
            params,
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def _serialize(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return cast(dict[str, Any], asdict(value))
    return {"value": str(value)}


def serialize_candidate(candidate: Candidate) -> dict[str, Any]:
    return {
        "code": candidate.code,
        "date": candidate.date,
        "signal_type": candidate.signal_type,
        "strategy_name": candidate.strategy_name,
        "score": candidate.score,
        "close": candidate.close,
        "role": candidate.role,
        "tradable": candidate.tradable,
        "reason": candidate.reason,
    }


def serialize_order(order: VirtualOrder) -> dict[str, Any]:
    return _serialize(order)


def serialize_fill(fill: VirtualFill) -> dict[str, Any]:
    return _serialize(fill)


def serialize_position(position: VirtualPosition) -> dict[str, Any]:
    return _serialize(position)


def paper_trade_report_path(config: Config, target_date: str) -> Path:
    report_root = Path(str(config.get("report.output_dir", "reports")))
    return report_root / "paper_trade" / f"{target_date}.json"


def write_paper_trade_report(
    config: Config,
    target_date: str,
    report: dict[str, Any],
) -> Path:
    """JSONレポートを一時ファイル経由で原子的に保存する。"""
    output_path = paper_trade_report_path(config, target_date)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return output_path


def _dry_run_result(
    *,
    target_date: str,
    config: Config,
    config_path: str,
    provider: Provider,
) -> dict[str, Any]:
    """ファイル・DBを書き換えない設定検証パス。"""
    markets, latest_bar_count, indicator_bar_count = get_daily_cycle_settings(config)
    execution_model = validate_execution_model(config)
    watchlist_path = Path(config.watchlist_file)
    symbols = json.loads(watchlist_path.read_text(encoding="utf-8"))
    enabled = [
        item
        for item in symbols
        if item.get("enabled", True)
        and str(item.get("market", "JP")).upper()
        in {str(market).upper() for market in markets}
    ]
    return {
        "status": "dry_run",
        "target_date": target_date,
        "generated_at": current_timestamp(),
        "config_hash": calculate_config_hash(config_path),
        "git_sha": get_git_sha(),
        "data_timestamp": None,
        "data_as_of_date": None,
        "provider": provider,
        "execution_model": execution_model,
        "connection": True,
        "symbols": len(enabled),
        "daily_bars": 0,
        "indicators": 0,
        "benchmark_prices": 0,
        "signals": 0,
        "virtual_orders": 0,
        "fills": 0,
        "exits": 0,
        "price_updates": 0,
        "alerts": 0,
        "settings": {
            "markets": list(markets),
            "latest_bar_count": latest_bar_count,
            "indicator_bar_count": indicator_bar_count,
        },
    }


def run_cycle(
    target_date: str,
    dry_run: bool = False,
    config_path: str = "config.yaml",
    *,
    provider: Provider = "auto",
) -> dict[str, Any]:
    """日次サイクルを実行し、レポート辞書を返す。"""
    if provider not in {"auto", "moomoo", "yfinance"}:
        raise ValueError(f"未対応providerです: {provider}")
    datetime.strptime(target_date, "%Y-%m-%d")

    config = load_config(config_path)
    if dry_run:
        return _dry_run_result(
            target_date=target_date,
            config=config,
            config_path=config_path,
            provider=provider,
        )

    markets, latest_bar_count, indicator_bar_count = get_daily_cycle_settings(config)
    execution_model = validate_execution_model(config)
    report: dict[str, Any] = {
        "status": "running",
        "target_date": target_date,
        "generated_at": current_timestamp(),
        "config_hash": calculate_config_hash(config_path),
        "git_sha": get_git_sha(),
        "data_timestamp": None,
        "data_as_of_date": None,
        "provider": provider,
        "execution_model": execution_model,
        "stages": {},
    }

    opend_conn: OpenDConnection | None = None
    quote_service: QuoteService | None = None
    try:
        if provider in {"auto", "moomoo"}:
            opend_conn = OpenDConnection(config)
            connection_status = opend_conn.connect()
            if (
                connection_status.connected
                and connection_status.quote_context is not None
            ):
                quote_service = QuoteService(config, connection_status.quote_context)
                report["connection"] = True
            elif provider == "moomoo":
                raise SystemError(f"OpenD接続失敗: {connection_status.message}")
            else:
                logger.warning(
                    "OpenD接続失敗のためyfinanceのみで継続します: %s",
                    connection_status.message,
                )
                report["connection"] = False
        else:
            report["connection"] = False

        data_store = DataStore(config)
        data_store.sync_symbols_from_json(config.watchlist_file)
        symbols = get_daily_cycle_symbols(data_store, markets)
        codes = [symbol.code for symbol in symbols]
        symbols_info = {symbol.code: symbol.name for symbol in symbols}
        benchmark_codes = {symbol.code for symbol in symbols if symbol.role == "benchmark"}
        report["symbols"] = len(codes)

        fetch_results: list[dict[str, Any]] = []
        for index, code in enumerate(codes, 1):
            logger.info("[%d/%d] %s の不足日足を取得中...", index, len(codes), code)
            fetch_results.append(
                fetch_symbol_daily_bars(
                    code=code,
                    target_date=target_date,
                    provider=provider,
                    latest_bar_count=latest_bar_count,
                    data_store=data_store,
                    quote_service=quote_service,
                )
            )
        report["data_timestamp"] = current_timestamp()
        report["data_as_of_date"] = get_data_as_of_date(
            data_store.db_path,
            codes,
            target_date,
        )
        report["daily_bars"] = sum(
            1 for item in fetch_results if int(item.get("rows", 0)) > 0
        )
        report["stages"]["data_fetch"] = {
            "status": "completed",
            "requested_codes": len(codes),
            "successful_codes": report["daily_bars"],
            "error_codes": sum(1 for item in fetch_results if item.get("error")),
            "rows_written": sum(int(item.get("rows", 0)) for item in fetch_results),
            "split_events": sum(int(item.get("splits", 0)) for item in fetch_results),
            "details": fetch_results,
        }

        indicator_data: dict[str, pd.DataFrame] = {}
        for code in codes:
            bars = load_indicator_bars(
                data_store,
                code,
                target_date,
                indicator_bar_count,
            )
            if not bars.empty:
                indicator_data[code] = bars
        if indicator_data:
            indicators = calculate_indicators_batch(indicator_data, symbols_info)
            indicators_df = indicators_to_dataframe(indicators)
            benchmark_code = config.get(
                "signals.relative_strength.benchmark_code",
                "JP.1306",
            )
            indicators_df = add_relative_strength(indicators_df, benchmark_code)
            report["indicators"] = save_indicators_to_db(data_store, indicators_df)
            report["benchmark_prices"] = save_benchmark_prices_from_indicators(
                data_store,
                indicators_df,
                benchmark_codes,
            )
        else:
            report["indicators"] = 0
            report["benchmark_prices"] = 0
        report["stages"]["indicators"] = {
            "status": "completed",
            "codes": len(indicator_data),
            "rows_saved": report["indicators"],
        }

        freshness = DataFreshnessGuard(config).check_freshness()
        report["freshness"] = {
            "level": freshness.level,
            "days_stale": freshness.days_stale,
            "message": freshness.message,
        }
        if freshness.level == "error" and freshness.days_stale < 9000:
            raise SystemError(f"データが古すぎるため処理を停止します: {freshness.message}")
        if freshness.level == "warning":
            logger.warning("データが古いですが処理を続行します: %s", freshness.message)

        screener = Screener(config)
        candidates = screener.screen_candidates(date=target_date)
        report["signals"] = screener.save_signals_to_db(candidates)
        report["stages"]["signals"] = {
            "status": "completed",
            "saved": report["signals"],
            "candidates": [serialize_candidate(candidate) for candidate in candidates],
        }

        manager = VirtualTradeManager(config)
        fills = manager.process_fills("default", target_date)
        price_updates = manager.update_market_prices("default", target_date)
        report["fills"] = len(fills)
        report["price_updates"] = price_updates
        report["stages"]["fills"] = {
            "status": "completed",
            "note": "target_dateより前に提出された注文のみnext_day_openで約定",
            "fills": [serialize_fill(fill) for fill in fills],
        }

        vt_config = config.get("virtual_trade", {})
        score_threshold = float(vt_config.get("score_threshold_for_order", 70))
        created_orders: list[VirtualOrder] = []
        available_cash = manager.get_cash("default")
        for candidate in candidates:
            if candidate.signal_type != "BUY_CANDIDATE":
                continue
            if candidate.role != "trade_candidate" or not candidate.tradable:
                continue
            if candidate.score < score_threshold:
                continue
            if candidate.close is None or candidate.close <= 0:
                continue
            estimated_cost = candidate.close * (
                1 + execution_model["slippage_bps"] / 10000
            ) + execution_model["commission"]
            if estimated_cost > available_cash:
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
                created_orders.append(order)
                available_cash -= estimated_cost
        report["virtual_orders"] = len(created_orders)

        exits = manager.generate_exits("default", target_date)
        report["exits"] = len(exits)
        report["stages"]["orders"] = {
            "status": "completed",
            "buy_orders": [serialize_order(order) for order in created_orders],
            "exit_orders": [serialize_order(order) for order in exits],
        }

        manager.save_equity_curve("default", target_date)
        positions = manager.get_positions("default")
        performance = manager.get_strategy_performance("default")
        report["stages"]["positions"] = {
            "status": "completed",
            "positions": [serialize_position(position) for position in positions],
            "performance": performance,
        }

        alerts = AlertManager(config).run_all_checks()
        report["alerts"] = len(alerts)
        report["stages"]["alerts"] = {
            "status": "completed",
            "count": len(alerts),
        }

        report["status"] = "completed"
        report["completed_at"] = current_timestamp()
        output_path = write_paper_trade_report(config, target_date, report)
        report["report_path"] = str(output_path)
        return report
    except Exception as exc:
        report["status"] = "failed"
        report["completed_at"] = current_timestamp()
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        try:
            output_path = write_paper_trade_report(config, target_date, report)
            report["report_path"] = str(output_path)
        except Exception:
            logger.exception("失敗レポートの保存にも失敗しました")
        raise
    finally:
        if opend_conn is not None:
            opend_conn.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Moomoo ペーパートレード日次サイクル")
    parser.add_argument(
        "--date",
        default=datetime.now(JST).strftime("%Y-%m-%d"),
        help="データ・シグナル基準日 (YYYY-MM-DD)",
    )
    parser.add_argument("--dry-run", action="store_true", help="非破壊の設定検証")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    parser.add_argument(
        "--provider",
        choices=("auto", "moomoo", "yfinance"),
        default="auto",
        help="日足取得provider",
    )
    args = parser.parse_args()
    configure_logging(dry_run=args.dry_run)

    logger.info(
        "日次サイクル開始: date=%s dry_run=%s provider=%s",
        args.date,
        args.dry_run,
        args.provider,
    )
    started = time.monotonic()
    try:
        result = run_cycle(
            args.date,
            dry_run=args.dry_run,
            config_path=args.config,
            provider=cast(Provider, args.provider),
        )
    except (SystemError, ValueError) as exc:
        logger.error("日次サイクル停止: %s", exc)
        return 1
    except Exception:
        logger.exception("日次サイクル失敗")
        return 1

    elapsed = time.monotonic() - started
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    logger.info("日次サイクル完了: %.1f秒", elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
