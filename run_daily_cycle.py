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
from typing import Any

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
from src.cycle_run import (
    CycleControlError,
    CycleControlSettings,
    CycleFileLock,
    CycleRunLedger,
    config_fingerprint,
    resolve_git_commit_sha,
)
from src.data_freshness import DataFreshnessGuard, FreshnessStatus
from src.data_store import DataStore
from src.database_backup import BackupResult, DatabaseBackupManager
from src.indicators import calculate_indicators_batch, indicators_to_dataframe
from src.market_calendar import JST, get_jpx_calendar
from src.operational_notifier import OperationalNotifier
from src.quote_service import QuoteService
from src.screener import Screener
from src.virtual_trade import VirtualTradeManager
from src.virtual_trade_integrity import IntegrityReport, VirtualTradeIntegrityChecker
from src.trading_identity import virtual_portfolio_name

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 120


class DailyCycleStoppedError(RuntimeError):
    """Expected operational stop caused by a failed daily-cycle guard."""

    def __init__(
        self,
        message: str,
        *,
        event_type: str = "cycle_stopped",
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.event_type = event_type
        self.context = context or {}


def _default_target_date() -> str:
    """Resolve the command default in JST rather than host-local time."""
    return datetime.now(JST).strftime("%Y-%m-%d")


def _notify_operational_failure(
    config_path: str,
    event_type: str,
    target_date: str,
    message: str,
    context: dict[str, object] | None = None,
) -> bool:
    """Best-effort notification that never masks the original failure."""
    try:
        config = load_config(config_path)
        return OperationalNotifier(config).send_failure(
            event_type,
            message,
            target_date=target_date,
            context=context,
        )
    except Exception as notify_error:
        logger.error(
            "運用異常通知の初期化または送信に失敗しました: event=%s error=%s",
            event_type,
            notify_error,
        )
        return False


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


def _read_bool_setting(config, key: str, default: bool) -> bool:
    """Read a boolean setting without accepting truthy strings or integers."""
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key}はtrue/falseで指定してください: {value!r}")
    return value


def _virtual_trade_integrity_settings(
    config,
    virtual_trade_enabled: bool,
) -> tuple[bool, bool]:
    """Return whether the daily integrity gate is enabled and strict on warnings."""
    if not virtual_trade_enabled:
        return False, False
    return (
        _read_bool_setting(
            config,
            "virtual_trade.integrity_check.enabled",
            True,
        ),
        _read_bool_setting(
            config,
            "virtual_trade.integrity_check.fail_on_warning",
            False,
        ),
    )


def _log_virtual_trade_integrity_report(report: IntegrityReport) -> None:
    """Write every actionable finding to the normal operation log."""
    for finding in report.findings:
        context = (
            json.dumps(
                finding.context,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if finding.context
            else "{}"
        )
        message = "仮想取引整合性: severity=%s code=%s message=%s context=%s"
        if finding.severity == "error":
            logger.error(
                message,
                finding.severity,
                finding.code,
                finding.message,
                context,
            )
        else:
            logger.warning(
                message,
                finding.severity,
                finding.code,
                finding.message,
                context,
            )


def _run_virtual_trade_integrity_gate(
    config,
    strategy_name: str,
    target_date: str,
    *,
    fail_on_warning: bool,
) -> IntegrityReport:
    """Run the read-only integrity checker and enforce the configured policy."""
    report = VirtualTradeIntegrityChecker(config).run(
        strategy_name,
        as_of_date=target_date,
    )
    _log_virtual_trade_integrity_report(report)
    error_count = len(report.errors)
    warning_count = len(report.warnings)
    if error_count > 0:
        raise DailyCycleStoppedError(
            "仮想取引整合性チェックでエラーを検出しました: "
            f"strategy={strategy_name}, date={target_date}, "
            f"errors={error_count}, warnings={warning_count}",
            event_type="integrity_failure",
            context={"errors": error_count, "warnings": warning_count},
        )
    if fail_on_warning and warning_count > 0:
        raise DailyCycleStoppedError(
            "仮想取引整合性チェックの警告を厳格設定によりエラー扱いします: "
            f"strategy={strategy_name}, date={target_date}, "
            f"warnings={warning_count}",
            event_type="integrity_warning_strict",
            context={"warnings": warning_count},
        )
    logger.info(
        "仮想取引整合性チェック完了: strategy=%s, date=%s, warnings=%d",
        strategy_name,
        target_date,
        warning_count,
    )
    return report


def _load_indicator_inputs(
    data_store: DataStore,
    codes: list[str],
    target_date: str,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> dict[str, pd.DataFrame]:
    """全対象銘柄の指標入力をDBから対象日以前に限定して読み込む。"""
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


def _run_cycle_core(
    target_date: str,
    dry_run: bool = False,
    config_path: str = "config.yaml",
) -> dict:
    results: dict[str, int | bool | str] = {
        "connection_attempted": False,
        "database_write_attempted": False,
        "virtual_trade_enabled": False,
        "integrity_check_enabled": False,
        "integrity_fail_on_warning": False,
        "integrity_errors": 0,
        "integrity_warnings": 0,
        "integrity_exit_code": 0,
        "calendar_checked": False,
        "is_trading_day": False,
        "cycle_skipped": False,
        "skip_reason": "",
    }
    config = load_config(config_path)
    portfolio_name = virtual_portfolio_name(config)
    results["virtual_portfolio"] = portfolio_name
    virtual_trade_enabled = _read_bool_setting(
        config,
        "virtual_trade.enabled",
        True,
    )
    integrity_enabled, integrity_fail_on_warning = (
        _virtual_trade_integrity_settings(config, virtual_trade_enabled)
    )
    results["virtual_trade_enabled"] = virtual_trade_enabled
    results["integrity_check_enabled"] = integrity_enabled
    results["integrity_fail_on_warning"] = integrity_fail_on_warning

    calendar = get_jpx_calendar()
    results["calendar_checked"] = True
    is_trading_day = calendar.is_trading_day(target_date)
    results["is_trading_day"] = is_trading_day
    if not is_trading_day:
        results["cycle_skipped"] = True
        results["skip_reason"] = "jpx_market_closed"
        logger.info("JPX休場日のため日次サイクルをスキップします: %s", target_date)
        return results

    if dry_run:
        symbol_count, benchmark_count = _validate_watchlist_for_dry_run(config)
        results["symbols"] = symbol_count
        results["benchmarks"] = benchmark_count
        return results

    results["connection_attempted"] = True
    opend_conn = OpenDConnection(config)
    status = opend_conn.connect()
    if not status.connected:
        logger.error("OpenD接続失敗: %s", status.message)
        raise DailyCycleStoppedError(
            f"OpenD接続失敗: {status.message}",
            event_type="opend_connection_failure",
            context={"status_message": status.message},
        )
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

        try:
            freshness_by_code = _assert_cycle_data_freshness(
                config,
                codes,
                target_date,
            )
        except SystemError as error:
            raise DailyCycleStoppedError(
                str(error),
                event_type="data_freshness_failure",
                context={"symbol_count": len(codes)},
            ) from error
        results["fresh_symbols"] = sum(
            1 for freshness in freshness_by_code.values() if freshness.is_fresh
        )
        results["freshness_warnings"] = sum(
            1 for freshness in freshness_by_code.values() if freshness.level == "warning"
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
                strategy_name=portfolio_name,
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
        fills = manager.process_fills(portfolio_name, target_date)
        results["fills"] = len(fills)
        exits = manager.generate_exits(portfolio_name, target_date)
        results["exits"] = len(exits)
        results["price_updates"] = manager.update_market_prices(
            portfolio_name,
            target_date,
        )
        manager.save_equity_curve(portfolio_name, target_date)

        if integrity_enabled:
            integrity_report = _run_virtual_trade_integrity_gate(
                config,
                portfolio_name,
                target_date,
                fail_on_warning=integrity_fail_on_warning,
            )
            results["integrity_errors"] = len(integrity_report.errors)
            results["integrity_warnings"] = len(integrity_report.warnings)
            results["integrity_exit_code"] = integrity_report.exit_code

        alert_manager = AlertManager(config)
        alerts = alert_manager.run_all_checks(target_date=target_date)
        results["alerts"] = len(alerts)
        return results
    finally:
        opend_conn.disconnect()


def _backup_details(result: BackupResult | None, *, skipped: str = "") -> dict[str, Any]:
    if result is None:
        return {"skipped": skipped or "disabled"}
    return {
        "backup_path": result.backup_path,
        "metadata_path": result.metadata_path,
        "pruned_files": list(result.pruned_files),
    }


def _finish_failed_cycle_run(
    ledger: CycleRunLedger | None,
    error: BaseException,
    result: dict[str, Any] | None,
) -> None:
    """Best-effort FAILED finalization that never masks the original error."""
    if ledger is None or ledger.run_id is None:
        return
    try:
        ledger.finish_run("FAILED", result=result, error=error)
    except Exception as ledger_error:
        logger.error(
            "実行台帳をFAILEDへ終端できませんでした: original=%s ledger_error=%s",
            error,
            ledger_error,
        )


def run_cycle(
    target_date: str,
    dry_run: bool = False,
    config_path: str = "config.yaml",
    *,
    force_rerun: bool = False,
    rerun_reason: str | None = None,
) -> dict:
    """Run one cycle with an optional cross-entrypoint lock and execution ledger."""
    config = load_config(config_path)
    calendar = get_jpx_calendar()
    is_trading_day = calendar.is_trading_day(target_date)

    # Preserve the strict no-op boundary: no lock, backup, or SQLite access.
    if dry_run or not is_trading_day:
        return _run_cycle_core(target_date, dry_run=dry_run, config_path=config_path)

    control_enabled = _read_bool_setting(config, "cycle_control.enabled", False)
    backup_manager = DatabaseBackupManager(config)
    backup_enabled = backup_manager.settings.enabled
    if not control_enabled and not backup_enabled:
        return _run_cycle_core(target_date, dry_run=False, config_path=config_path)

    lock: CycleFileLock | None = None
    ledger: CycleRunLedger | None = None
    preflight_ledger: CycleRunLedger | None = None
    pre_backup: BackupResult | None = None
    post_backup: BackupResult | None = None
    acquisition = None
    record = None
    results: dict[str, Any] | None = None
    source_database_existed = backup_manager.source_path.is_file()

    try:
        if control_enabled:
            lock = CycleFileLock(
                CycleControlSettings.from_config(config),
                target_date,
            )
            acquisition = lock.acquire()

        if control_enabled or backup_enabled:
            preflight_ledger = CycleRunLedger(Path(config.database_path))
            preflight_ledger.assert_rerun_allowed(
                target_date=target_date,
                force_rerun=force_rerun,
                rerun_reason=rerun_reason,
            )
        if control_enabled:
            ledger = preflight_ledger

        if backup_enabled and source_database_existed:
            try:
                pre_backup = backup_manager.create_backup(kind="pre_cycle")
            except Exception as error:
                raise DailyCycleStoppedError(
                    f"pre-cycle SQLiteバックアップに失敗しました: {error}",
                    event_type="database_backup_failure",
                    context={"backup_kind": "pre_cycle"},
                ) from error

        if ledger is not None:
            record = ledger.start_run(
                target_date=target_date,
                force_rerun=force_rerun,
                rerun_reason=rerun_reason,
                git_commit_sha=resolve_git_commit_sha(Path(__file__).parent),
                config_sha256=config_fingerprint(config),
                stale_lock_recovered=bool(
                    acquisition and acquisition.recovered_stale_lock
                ),
            )

        try:
            if ledger is not None:
                ledger.start_stage(
                    "pre_cycle_backup",
                    _backup_details(
                        pre_backup,
                        skipped=(
                            "source_database_missing"
                            if backup_enabled and not source_database_existed
                            else "disabled"
                        ),
                    ),
                )
                ledger.finish_stage()
                ledger.start_stage("daily_pipeline")

            results = _run_cycle_core(
                target_date,
                dry_run=False,
                config_path=config_path,
            )

            if ledger is not None:
                ledger.finish_stage(
                    {
                        "symbols": results.get("symbols", 0),
                        "signals": results.get("signals", 0),
                        "fills": results.get("fills", 0),
                        "alerts": results.get("alerts", 0),
                    }
                )

            if backup_enabled:
                if ledger is not None:
                    ledger.start_stage("post_cycle_backup")
                try:
                    post_backup = backup_manager.create_backup(kind="post_cycle")
                except Exception as error:
                    raise DailyCycleStoppedError(
                        f"post-cycle SQLiteバックアップに失敗しました: {error}",
                        event_type="database_backup_failure",
                        context={"backup_kind": "post_cycle"},
                    ) from error
                if ledger is not None:
                    ledger.finish_stage(_backup_details(post_backup))

            results["cycle_control_enabled"] = control_enabled
            results["cycle_lock_acquired"] = acquisition is not None
            results["stale_lock_recovered"] = bool(
                acquisition and acquisition.recovered_stale_lock
            )
            results["cycle_run_id"] = record.run_id if record is not None else 0
            results["pre_cycle_backup"] = (
                pre_backup.backup_path if pre_backup else ""
            )
            results["post_cycle_backup"] = (
                post_backup.backup_path if post_backup else ""
            )
            if ledger is not None:
                ledger.finish_run("SUCCEEDED", result=results)
            return results
        except Exception as error:
            _finish_failed_cycle_run(ledger, error, results)
            raise
    except CycleControlError as error:
        raise DailyCycleStoppedError(
            str(error),
            event_type="cycle_concurrency_failure",
            context={"force_rerun": force_rerun},
        ) from error
    finally:
        if lock is not None:
            lock.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="Moomoo 日次運用サイクル")
    parser.add_argument(
        "--date",
        default=_default_target_date(),
        help="基準日",
    )
    parser.add_argument("--dry-run", action="store_true", help="テスト実行")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help="既存の対象日を理由付きで再実行する",
    )
    parser.add_argument(
        "--rerun-reason",
        default=None,
        help="--force-rerunを使用する理由",
    )
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
            force_rerun=args.force_rerun,
            rerun_reason=args.rerun_reason,
        )
    except DailyCycleStoppedError as error:
        logger.error("日次サイクル停止: %s", error)
        if not args.dry_run:
            _notify_operational_failure(
                args.config,
                error.event_type,
                args.date,
                str(error),
                error.context,
            )
        return 1
    except SystemError as error:
        logger.error("日次サイクル停止: %s", error)
        if not args.dry_run:
            _notify_operational_failure(
                args.config,
                "cycle_stopped",
                args.date,
                str(error),
            )
        return 1
    except Exception as error:
        logger.error("日次サイクル失敗: %s", error)
        if not args.dry_run:
            _notify_operational_failure(
                args.config,
                "unexpected_failure",
                args.date,
                str(error),
                {"exception_type": type(error).__name__},
            )
        return 1

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print("日次サイクル結果")
    print("=" * 60)
    for key, value in results.items():
        print(f"  {key}: {value}")
    print(f"  所要時間: {elapsed:.1f}秒")
    if args.dry_run:
        print("\n[DONE] dry-run 完了")
    elif results.get("cycle_skipped"):
        print(f"\n[SKIP] JPX休場日のため処理なし: {args.date}")
    else:
        print(f"\n[DONE] 日次サイクル完了: {args.date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
