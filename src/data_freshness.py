"""
データ鮮度ガードモジュール

ファイルパス: src/data_freshness.py
何をするか: データの鮮度をチェックし、古いデータでの処理を防止する
なぜ存在するか: 古いデータで誤ったシグナル判定を行うことを防ぐため

注意:
    現在の鮮度判定は暦日ベースです。祝日・休場日を含む正確な営業日判定ではありません。
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)

_ALLOWED_TABLES = {"daily_bars", "indicators", "signals"}


@dataclass
class FreshnessStatus:
    """鮮度ステータス"""
    is_fresh: bool
    latest_date: Optional[str]
    days_stale: int
    message: str
    level: str


class DataFreshnessGuard:
    """データ鮮度ガードクラス"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)

    def _validate_table_name(self, table_name: str) -> str:
        if table_name not in _ALLOWED_TABLES:
            raise ValueError(f"許可されていないテーブル名です: {table_name}")
        return table_name

    def get_latest_data_date(
        self,
        table_name: str = "daily_bars",
        code: Optional[str] = None,
    ) -> Optional[str]:
        """テーブルの最新日付を取得する"""
        if not self.db_path.exists():
            return None

        table_name = self._validate_table_name(table_name)

        with sqlite3.connect(self.db_path) as conn:
            if code:
                cursor = conn.execute(
                    f"SELECT MAX(date) FROM {table_name} WHERE code = ?",
                    (code,),
                )
            else:
                cursor = conn.execute(f"SELECT MAX(date) FROM {table_name}")

            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def check_freshness(
        self,
        max_stale_days: int = 5,
        table_name: str = "daily_bars",
        code: Optional[str] = None,
    ) -> FreshnessStatus:
        """データの鮮度をチェックする（暦日ベース）"""
        latest_date = self.get_latest_data_date(table_name, code)

        if latest_date is None:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=None,
                days_stale=9999,
                message="データがありません",
                level="error",
            )

        try:
            latest = datetime.strptime(latest_date, "%Y-%m-%d")
            now = datetime.now()
            days_stale = (now - latest).days
        except ValueError:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=9999,
                message=f"日付形式エラー: {latest_date}",
                level="error",
            )

        if days_stale <= max_stale_days:
            return FreshnessStatus(
                is_fresh=True,
                latest_date=latest_date,
                days_stale=days_stale,
                message=f"データは最新です（{latest_date}、暦日差{days_stale}日）",
                level="ok",
            )

        if days_stale <= 30:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=days_stale,
                message=f"データが暦日で{days_stale}日分古いです（{latest_date}）",
                level="warning",
            )

        if days_stale <= 180:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=days_stale,
                message=f"データが暦日で{days_stale}日分古いです（{latest_date}）。シグナル判定を停止します。",
                level="error",
            )

        return FreshnessStatus(
            is_fresh=False,
            latest_date=latest_date,
            days_stale=days_stale,
            message=f"データが暦日で{days_stale}日分古いです（{latest_date}）。半年以上前のデータです。",
            level="error",
        )

    def assert_fresh_data_or_stop(
        self,
        allow_stale: bool = False,
        table_name: str = "daily_bars",
    ) -> FreshnessStatus:
        """データが新鮮でない場合はエラーを出す"""
        status = self.check_freshness(table_name=table_name)

        if status.level == "ok":
            logger.info(status.message)
            return status

        if status.level == "warning":
            logger.warning(status.message)
            if not allow_stale:
                logger.warning("古いデータです。--allow-stale で強制実行できます。")
            return status

        if status.level == "error":
            logger.error(status.message)
            if not allow_stale:
                raise SystemError(
                    f"データが古すぎるため処理を停止します: {status.message}\n"
                    f"最新日付: {status.latest_date}\n"
                    f"古い日数: {status.days_stale}日\n"
                    f"強制実行する場合は --allow-stale オプションを使用してください"
                )
            logger.warning("古いデータでも処理を続行します（--allow-stale）")
            return status

        return status

    def get_freshness_report(self) -> dict:
        """全テーブルの鮮度レポートを生成する"""
        report = {}

        for table in sorted(_ALLOWED_TABLES):
            try:
                status = self.check_freshness(table_name=table)
                report[table] = {
                    "latest_date": status.latest_date,
                    "days_stale": status.days_stale,
                    "level": status.level,
                    "message": status.message,
                }
            except Exception as e:
                report[table] = {
                    "latest_date": None,
                    "days_stale": 9999,
                    "level": "error",
                    "message": str(e),
                }

        return report
