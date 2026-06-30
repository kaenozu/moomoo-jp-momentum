"""
定期実行スケジューラ

ファイルパス: scheduler.py
何をするか: APSchedulerを使って定期ジョブを実行する
なぜ存在するか: 日次更新・候補抽出・レポート生成を自動化するため
関連ファイル: src/config.py

使い方:
    python scheduler.py              # 起動
    python scheduler.py --dry-run    # テスト実行
    python scheduler.py --list       # ジョブ一覧表示

注意:
    - scheduler.enabled が false の場合は起動しない
    - 自動売買は行わない
    - API発注は行わない
"""

import argparse
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 二重起動防止
_lock_file = Path("data/scheduler.lock")


def acquire_lock() -> bool:
    """ロックを取得する"""
    if _lock_file.exists():
        try:
            with open(_lock_file, "r") as f:
                pid = int(f.read().strip())
            # プロセスが生きているか確認
            import os
            os.kill(pid, 0)
            return False  # 既に起動中
        except (ValueError, ProcessLookupError, PermissionError):
            pass

    with open(_lock_file, "w") as f:
        f.write(str(os.getpid()))
    return True


def release_lock() -> None:
    """ロックを解放する"""
    if _lock_file.exists():
        _lock_file.unlink()


def job_connection_check() -> None:
    """接続確認ジョブ"""
    logger.info("接続確認ジョブを実行します")
    try:
        from src.config import load_config
        from src.connection import OpenDConnection

        config = load_config("config.yaml")
        with OpenDConnection(config) as conn:
            status = conn.connect()
            if status.connected:
                logger.info("接続確認成功")
            else:
                logger.error(f"接続失敗: {status.message}")
    except Exception as e:
        logger.error(f"接続確認エラー: {e}")


def job_daily_update() -> None:
    """日次更新ジョブ"""
    logger.info("日次更新ジョブを実行します")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "daily_update.py", "--force"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            logger.info("日次更新完了")
        else:
            logger.error(f"日次更新失敗: {result.stderr}")
    except Exception as e:
        logger.error(f"日次更新エラー: {e}")


def job_screen_candidates() -> None:
    """候補抽出ジョブ"""
    logger.info("候補抽出ジョブを実行します")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "screen_candidates.py", "--csv", "--html", "--save"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            logger.info("候補抽出完了")
        else:
            logger.error(f"候補抽出失敗: {result.stderr}")
    except Exception as e:
        logger.error(f"候補抽出エラー: {e}")


def job_performance_report() -> None:
    """パフォーマンスレポートジョブ"""
    logger.info("パフォーマンスレポートジョブを実行します")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "performance_report.py", "--csv", "--html"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            logger.info("パフォーマンスレポート完了")
        else:
            logger.error(f"パフォーマンスレポート失敗: {result.stderr}")
    except Exception as e:
        logger.error(f"パフォーマンスレポートエラー: {e}")


def job_send_alerts() -> None:
    """アラート送信ジョブ"""
    logger.info("アラート送信ジョブを実行します")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "send_alerts.py"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("アラート送信完了")
        else:
            logger.error(f"アラート送信失敗: {result.stderr}")
    except Exception as e:
        logger.error(f"アラート送信エラー: {e}")


def job_weekly_report() -> None:
    """週次レポートジョブ"""
    logger.info("週次レポートジョブを実行します")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "generate_reports.py", "--weekly"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            logger.info("週次レポート完了")
        else:
            logger.error(f"週次レポート失敗: {result.stderr}")
    except Exception as e:
        logger.error(f"週次レポートエラー: {e}")


def parse_cron(cron_str: str) -> dict:
    """
    cron式をパースする

    Args:
        cron_str: cron式（例: "45 8 * * 1-5"）

    Returns:
        dict: APSchedulerのパラメータ
    """
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
    parser = argparse.ArgumentParser(
        description="Moomoo 定期実行スケジューラ"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="テスト実行（ジョブを登録するが起動しない）",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="ジョブ一覧を表示",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="設定ファイルパス",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo 定期実行スケジューラ")
    print("=" * 60)

    # 設定読み込み
    try:
        config = load_config(args.config)
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    # スケジューラ設定確認
    scheduler_config = config.get("scheduler", {})
    if not scheduler_config.get("enabled", False):
        print("[INFO] scheduler.enabled が false です")
        print("  設定ファイルで scheduler.enabled を true にしてください")
        return 0

    # ジョブ一覧表示
    if args.list:
        print("\nジョブ一覧:")
        jobs_config = scheduler_config.get("jobs", {})
        for job_name, job_config in jobs_config.items():
            enabled = job_config.get("enabled", True)
            cron = job_config.get("cron", "")
            status = "有効" if enabled else "無効"
            print(f"  {job_name}: {status} ({cron})")
        return 0

    # ジョブ登録
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("[ERROR] apscheduler がインストールされていません")
        print("  pip install apscheduler")
        return 1

    scheduler = BlockingScheduler()
    jobs_config = scheduler_config.get("jobs", {})
    timezone = scheduler_config.get("timezone", "Asia/Tokyo")

    # ジョブ定義
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
            logger.warning(f"不明なジョブ: {job_name}")
            continue

        try:
            cron_params = parse_cron(cron_str)
            trigger = CronTrigger(**cron_params, timezone=timezone)
            scheduler.add_job(
                job_funcs[job_name],
                trigger,
                id=job_name,
                name=job_name,
            )
            logger.info(f"ジョブ登録: {job_name} ({cron_str})")
        except Exception as e:
            logger.error(f"ジョブ登録エラー: {job_name} - {e}")

    # テスト実行
    if args.dry_run:
        print("\n[DRY-RUN] ジョブ一覧:")
        for job in scheduler.get_jobs():
            print(f"  {job.id}: {job.trigger}")
        print("\n[DRY-RUN] 実行はしません")
        return 0

    # ロック取得
    import os
    if not acquire_lock():
        print("[ERROR] 既にスケジューラが起動しています")
        return 1

    # 終了ハンドラ
    def shutdown(signum, frame):
        logger.info("スケジューラを停止します")
        scheduler.shutdown()
        release_lock()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # 起動
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
