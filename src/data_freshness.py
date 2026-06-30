"""
データ鮮度ガードモジュール

ファイルパス: src/data_freshness.py
何をするか: データの鮮度をチェックし、古いデータでの処理を防止する
なぜ存在するか: 古いデータで誤ったシグナル判定を行うことを防ぐため
関連ファイル: config.py, data_store.py

判定ルール:
- 最新日足が5営業日以上古い場合: シグナル判定を停止
- 最新日足が30日以上古い場合: レポート出力に警告
- 最新日足が半年古い場合: 候補一覧を出さずにエラー終了
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class FreshnessStatus:
    """鮮度ステータス"""
    is_fresh: bool
    latest_date: Optional[str]
    days_stale: int
    message: str
    level: str  # "ok", "warning", "error"


class DataFreshnessGuard:
    """データ鮮度ガードクラス"""

    def __init__(self, config: Config):
        """
        Args:
            config: 設定オブジェクト
        """
        self.config = config
        self.db_path = Path(config.database_path)

    def get_latest_data_date(
        self,
        table_name: str = "daily_bars",
        code: Optional[str] = None,
    ) -> Optional[str]:
        """
        テーブルの最新日付を取得する

        Args:
            table_name: テーブル名
            code: 銘柄コード（Noneなら全銘柄）

        Returns:
            str: 最新日付（YYYY-MM-DD）。データがない場合はNone
        """
        if not self.db_path.exists():
            return None

        with sqlite3.connect(self.db_path) as conn:
            if code:
                cursor = conn.execute(
                    f"SELECT MAX(date) FROM {table_name} WHERE code = ?",
                    (code,),
                )
            else:
                cursor = conn.execute(
                    f"SELECT MAX(date) FROM {table_name}"
                )

            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def check_freshness(
        self,
        max_stale_days: int = 5,
        table_name: str = "daily_bars",
        code: Optional[str] = None,
    ) -> FreshnessStatus:
        """
        データの鮮度をチェックする

        Args:
            max_stale_days: 許容する最大遅延日数
            table_name: チェックするテーブル
            code: 銘柄コード

        Returns:
            FreshnessStatus: 鮮度ステータス
        """
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

        # 判定
        if days_stale <= max_stale_days:
            return FreshnessStatus(
                is_fresh=True,
                latest_date=latest_date,
                days_stale=days_stale,
                message=f"データは最新です（{latest_date}）",
                level="ok",
            )
        elif days_stale <= 30:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=days_stale,
                message=f"データが{days_stale}日分古いです（{latest_date}）",
                level="warning",
            )
        elif days_stale <= 180:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=days_stale,
                message=f"データが{days_stale}日分古いです（{latest_date}）。シグナル判定を停止します。",
                level="error",
            )
        else:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=days_stale,
                message=f"データが{days_stale}日分古いです（{latest_date}）。半年以上前のデータです。",
                level="error",
            )

    def assert_fresh_data_or_stop(
        self,
        allow_stale: bool = False,
        table_name: str = "daily_bars",
    ) -> FreshnessStatus:
        """
        データが新鮮でない場合はエラーを出す

        Args:
            allow_stale: 古いデータでも処理を許可するか
            table_name: チェックするテーブル

        Returns:
            FreshnessStatus: 鮮度ステータス

        Raises:
            SystemError: データが古く、allow_stale=Falseの場合
        """
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
            else:
                logger.warning("古いデータでも処理を続行します（--allow-stale）")
            return status

        return status

    def get_freshness_report(self) -> dict:
        """
        全テーブルの鮮度レポートを生成する

        Returns:
            dict: 鮮度レポート
        """
        tables = ["daily_bars", "indicators", "signals"]
        report = {}

        for table in tables:
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
