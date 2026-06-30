"""
定期実行スケジューラ

ファイルパス: scheduler.py
何をするか: APSchedulerを使って定期ジョブを実行する
なぜ存在するか: 日次更新・候補抽出・レポート生成を自動化するため
関連ファイル: src/config.py

注意:
    - scheduler.enabled が false の場合は起動しない
    - 自動売買は行わない
    - API発注は行わない
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_lock_file = Path("data/scheduler.lock")


def acquire_lock() -> bool:
    """ロックを取得する"""
    _lock_file.parent.mkdir(parents=True, exist_ok=True)

    if _lock_file.exists():
        try:
            with open(_lock_file, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return False
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass

    with open(_lock_file, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def release_lock() -> None:
    """ロックを解放する"""
    if _lock_file.exists():
        _lock_file.unlink()


def _run_script(args: list[str], timeout: int, name: str) -> None:
    """サブプロセスでスクリプトを実行する"""
    try:
        result = subprocess.run(
            [sys.executable, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            logger.info("%s完了", name)
        else:
            logger.error("%s失敗: stdout=%s stderr=%s", name, result.stdout[-500:], result.stderr[-500:])
    except Exception as e:
        logger.error("%sエラー: %s", name, e)


def job_connection_check() -> None:
    """接続確認ジョブ"""
    logger.info("接続確認ジョブを実行します")
    try:
        from src.connection import OpenDConnection

        config = load_config("config.yaml")
        with OpenDConnection(config) as conn:
            status = conn.connect()
            if status.connected:
                logger.info("接続確認成功")
            else:
                logger.error("接続失敗: %s", status.message)
    except Exception as e:
        logger.error("接続確認エラー: %s", e)


def job_daily_update() -> None:
    """日次更新ジョブ"""
    logger.info("日次更新ジョブを実行します")
    _run_script(["daily_update.py", "--force"], timeout=600, name="日次更新")


def job_screen_candidates() -> None:
    """候補抽出ジョブ"""
    logger.info("候補抽出ジョブを実行します")
    _run_script(["screen_candidates.py", "--csv", "--html", "--save"], timeout=300, name="候補抽出")


def job_performance_report() -> None:
    """パフォーマンスレポートジョブ"""
    logger.info("パフォーマンスレポートジョブを実行します")
    _run_script(["performance_report.py", "--csv", "--html"], timeout=300, name="パフォーマンスレポート")


def job_send_alerts() -> None:
    """アラート送信ジョブ"""
    logger.info("アラート送信ジョブを実行します")
    _run_script(["send_alerts.py"], timeout=120, name="アラート送信")


def job_weekly_report() -> None:
    """週次レポートジョブ"""
    logger.info("週次レポートジョブを実行します")
    _run_script(["generate_reports.py", "--weekly"], timeout=600, name="週次レポート")


def parse_cron(cron_str: str) -> dict:
    """cron式をAPSchedulerのパラメータに変換する"""
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


def main() -> int:
    """メイン関数"""
    parser = argparse.ArgumentParser(description="Moomoo 定期実行スケジューラ")
    parser.add_argument("--dry-run", action="store_true", help="テスト実行（ジョブを登録するが起動しない）")
    parser.add_argument("--list", action="store_true", help="ジョブ一覧を表示")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo 定期実行スケジューラ")
    print("=" * 60)

    try:
        config = load_config(args.config)
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    scheduler_config = config.get("scheduler", {})
    if not scheduler_config.get("enabled", False):
        print("[INFO] scheduler.enabled が false です")
        print("  設定ファイルで scheduler.enabled を true にしてください")
        return 0

    jobs_config = scheduler_config.get("jobs", {})

    if args.list:
        print("\nジョブ一覧:")
        for job_name, job_config in jobs_config.items():
            enabled = job_config.get("enabled", True)
            cron = job_config.get("cron", "")
            status = "有効" if enabled else "無効"
            print(f"  {job_name}: {status} ({cron})")
        return 0

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("[ERROR] apscheduler がインストールされていません")
        print("  pip install apscheduler")
        return 1

    timezone = scheduler_config.get("timezone", "Asia/Tokyo")
    scheduler = BlockingScheduler(timezone=timezone)

    job_funcs = {
        "connection_check": job_connection_check,
        "daily_update": job_daily_update,
        "screen_candidates": job_screen_candidates,
        "performance_report": job_performance_report,
        "send_alerts": job_send_alerts,
        "weekly_report": job_weekly_report,
    }

    for job_name, job_config in jobs_config.items():
        if not job_config.get("enabled", True):
            continue

        cron_str = job_config.get("cron", "")
        if not cron_str:
            continue

        if job_name not in job_funcs:
            logger.warning("不明なジョブ: %s", job_name)
            continue

        try:
            trigger = CronTrigger(**parse_cron(cron_str), timezone=timezone)
            scheduler.add_job(job_funcs[job_name], trigger, id=job_name, name=job_name)
            logger.info("ジョブ登録: %s (%s)", job_name, cron_str)
        except Exception as e:
            logger.error("ジョブ登録エラー: %s - %s", job_name, e)

    if args.dry_run:
        print("\n[DRY-RUN] ジョブ一覧:")
        for job in scheduler.get_jobs():
            print(f"  {job.id}: {job.trigger}")
        print("\n[DRY-RUN] 実行はしません")
        return 0

    if not acquire_lock():
        print("[ERROR] 既にスケジューラが起動しています")
        return 1

    def shutdown(signum, frame):
        logger.info("スケジューラを停止します")
        scheduler.shutdown()
        release_lock()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("\n[OK] スケジューラを起動します")
    print("  停止するには Ctrl+C を押してください")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        release_lock()

    return 0


if __name__ == "__main__":
    sys.exit(main())
