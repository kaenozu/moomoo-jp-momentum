"""APScheduler entry point for the single sequential daily pipeline."""

import argparse
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.market_calendar import JST
from src.operational_notifier import OperationalNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_lock_file = Path("data/scheduler.lock")


def acquire_lock() -> bool:
    """Acquire a process lock for the scheduler."""
    _lock_file.parent.mkdir(parents=True, exist_ok=True)
    if _lock_file.exists():
        try:
            pid = int(_lock_file.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            return False
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass
    _lock_file.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    """Release the scheduler process lock."""
    if _lock_file.exists():
        _lock_file.unlink()


def _run_script(args: list[str], timeout: int, name: str) -> None:
    """Run one Python entry point and fail the scheduler job on non-zero exit."""
    result = subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{name} failed with exit={result.returncode}: "
            f"stdout={result.stdout[-1000:]} stderr={result.stderr[-1000:]}"
        )
    logger.info("%s完了", name)


def _notify_scheduler_failure(
    config_path: str,
    event_type: str,
    message: str,
    context: dict[str, object] | None = None,
) -> bool:
    """Best-effort scheduler notification without hiding the job failure."""
    try:
        config = load_config(config_path)
        return OperationalNotifier(config).send_failure(
            event_type,
            message,
            target_date=datetime.now(JST).strftime("%Y-%m-%d"),
            context=context,
        )
    except Exception as notify_error:
        logger.error(
            "scheduler運用異常通知に失敗しました: event=%s error=%s",
            event_type,
            notify_error,
        )
        return False


def job_connection_check(config_path: str = "config.yaml") -> None:
    """Verify OpenD connectivity without running the data pipeline."""
    from src.connection import OpenDConnection

    try:
        config = load_config(config_path)
        with OpenDConnection(config) as connection:
            status = connection.connect()
            if not status.connected:
                raise RuntimeError(f"OpenD接続失敗: {status.message}")
    except Exception as error:
        _notify_scheduler_failure(
            config_path,
            "opend_connection_check_failure",
            str(error),
            {"job": "connection_check"},
        )
        raise
    logger.info("OpenD接続確認成功")


def job_daily_cycle(config_path: str = "config.yaml") -> None:
    """Run update, indicators, screening, virtual fills, reports, and alerts sequentially."""
    try:
        _run_script(
            ["run_daily_cycle.py", "--config", config_path],
            timeout=7200,
            name="日次運用サイクル",
        )
    except subprocess.TimeoutExpired as error:
        message = f"日次運用サイクルがタイムアウトしました: timeout={error.timeout}"
        _notify_scheduler_failure(
            config_path,
            "scheduler_timeout",
            message,
            {"job": "daily_cycle", "timeout_seconds": error.timeout},
        )
        raise RuntimeError(message) from error


def parse_cron(cron_str: str) -> dict[str, str]:
    """Convert a five-field cron expression for APScheduler."""
    parts = cron_str.split()
    if len(parts) != 5:
        raise ValueError(f"無効なcron式: {cron_str}")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def build_job_functions(config_path: str) -> dict[str, Callable[[], None]]:
    """Return the only supported scheduler jobs."""
    return {
        "connection_check": lambda: job_connection_check(config_path),
        "daily_cycle": lambda: job_daily_cycle(config_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Moomoo 定期実行スケジューラ")
    parser.add_argument("--dry-run", action="store_true", help="ジョブ登録だけを検証")
    parser.add_argument("--list", action="store_true", help="ジョブ一覧を表示")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as error:
        print(f"[ERROR] {error}")
        return 1

    scheduler_config = config.get("scheduler", {})
    if not scheduler_config.get("enabled", False):
        print("[INFO] scheduler.enabled が false です")
        return 0

    jobs_config = scheduler_config.get("jobs", {})
    job_functions = build_job_functions(args.config)

    if args.list:
        for name, job_config in jobs_config.items():
            enabled = job_config.get("enabled", True)
            print(f"{name}: {'enabled' if enabled else 'disabled'} ({job_config.get('cron', '')})")
        return 0

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("[ERROR] apscheduler がインストールされていません")
        return 1

    timezone = scheduler_config.get("timezone", "Asia/Tokyo")
    scheduler = BlockingScheduler(timezone=timezone)

    for job_name, job_config in jobs_config.items():
        if not job_config.get("enabled", True):
            continue
        if job_name not in job_functions:
            raise ValueError(f"未対応のscheduler jobです: {job_name}")
        cron_str = job_config.get("cron", "")
        if not cron_str:
            raise ValueError(f"cronが空です: {job_name}")
        scheduler.add_job(
            job_functions[job_name],
            CronTrigger(**parse_cron(cron_str), timezone=timezone),
            id=job_name,
            name=job_name,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )

    if args.dry_run:
        print("[DRY-RUN] 登録ジョブ:")
        for job in scheduler.get_jobs():
            print(f"  {job.id}: {job.trigger}")
        return 0

    if not acquire_lock():
        print("[ERROR] 既にスケジューラが起動しています")
        return 1

    def shutdown(signum, frame) -> None:
        logger.info("スケジューラを停止します: signal=%s", signum)
        scheduler.shutdown()
        release_lock()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
