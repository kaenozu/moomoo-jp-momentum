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
from contextlib import closing
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
        on_or_before: Optional[str] = None,
    ) -> Optional[str]:
        """銘柄と基準日を限定してテーブルの最新日付を取得する。"""
        if not self.db_path.exists():
            return None

        table_name = self._validate_table_name(table_name)
        conditions: list[str] = []
        params: list[str] = []

        if code:
            conditions.append("code = ?")
            params.append(code)
        if on_or_before:
            conditions.append("date <= ?")
            params.append(on_or_before)

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT MAX(date) FROM {table_name}{where_clause}"

        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def get_latest_data_dates(
        self,
        table_name: str = "daily_bars",
        on_or_before: Optional[str] = None,
    ) -> dict[str, str]:
        """全銘柄の最新日を1回の集約クエリで取得する。"""
        if not self.db_path.exists():
            return {}

        table_name = self._validate_table_name(table_name)
        where_clause = " WHERE date <= ?" if on_or_before else ""
        params = [on_or_before] if on_or_before else []
        sql = (
            f"SELECT code, MAX(date) FROM {table_name}"
            f"{where_clause} GROUP BY code"
        )

        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(sql, params).fetchall()

        return {
            str(code): str(latest_date)
            for code, latest_date in rows
            if code and latest_date
        }

    def _status_for_latest_date(
        self,
        latest_date: Optional[str],
        max_stale_days: int,
        code: Optional[str],
        reference_date: Optional[str],
    ) -> FreshnessStatus:
        """取得済みの最新日を鮮度ステータスへ変換する。"""
        code_label = f"{code}: " if code else ""

        try:
            reference = (
                datetime.strptime(reference_date, "%Y-%m-%d")
                if reference_date
                else datetime.now()
            )
        except ValueError:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=9999,
                message=f"{code_label}基準日の日付形式エラー: {reference_date}",
                level="error",
            )

        if latest_date is None:
            reference_label = f"（基準日 {reference_date} 以前）" if reference_date else ""
            return FreshnessStatus(
                is_fresh=False,
                latest_date=None,
                days_stale=9999,
                message=f"{code_label}データがありません{reference_label}",
                level="error",
            )

        try:
            latest = datetime.strptime(latest_date, "%Y-%m-%d")
        except ValueError:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=9999,
                message=f"{code_label}最新日の日付形式エラー: {latest_date}",
                level="error",
            )

        days_stale = (reference - latest).days
        if days_stale < 0:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=days_stale,
                message=(
                    f"{code_label}基準日より未来のデータです"
                    f"（latest={latest_date}, reference={reference_date or 'now'}）"
                ),
                level="error",
            )

        if days_stale <= max_stale_days:
            return FreshnessStatus(
                is_fresh=True,
                latest_date=latest_date,
                days_stale=days_stale,
                message=(
                    f"{code_label}データは最新です"
                    f"（{latest_date}、暦日差{days_stale}日）"
                ),
                level="ok",
            )

        if days_stale <= 30:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=days_stale,
                message=(
                    f"{code_label}データが暦日で{days_stale}日分古いです"
                    f"（{latest_date}）"
                ),
                level="warning",
            )

        if days_stale <= 180:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=days_stale,
                message=(
                    f"{code_label}データが暦日で{days_stale}日分古いです"
                    f"（{latest_date}）。シグナル判定を停止します。"
                ),
                level="error",
            )

        return FreshnessStatus(
            is_fresh=False,
            latest_date=latest_date,
            days_stale=days_stale,
            message=(
                f"{code_label}データが暦日で{days_stale}日分古いです"
                f"（{latest_date}）。半年以上前のデータです。"
            ),
            level="error",
        )

    def check_freshness(
        self,
        max_stale_days: int = 5,
        table_name: str = "daily_bars",
        code: Optional[str] = None,
        reference_date: Optional[str] = None,
    ) -> FreshnessStatus:
        """基準日以前のデータについて鮮度をチェックする（暦日ベース）。"""
        latest_date = self.get_latest_data_date(
            table_name,
            code,
            on_or_before=reference_date,
        )
        return self._status_for_latest_date(
            latest_date,
            max_stale_days=max_stale_days,
            code=code,
            reference_date=reference_date,
        )

    def check_required_codes_freshness(
        self,
        codes: list[str],
        reference_date: str,
        max_stale_days: int = 5,
        table_name: str = "daily_bars",
    ) -> dict[str, FreshnessStatus]:
        """必須銘柄を個別に確認し、DB上の別銘柄の最新日で代用しない。"""
        normalized_codes = sorted({code.strip() for code in codes if code.strip()})
        latest_dates = self.get_latest_data_dates(
            table_name=table_name,
            on_or_before=reference_date,
        )
        return {
            code: self._status_for_latest_date(
                latest_dates.get(code),
                max_stale_days=max_stale_days,
                code=code,
                reference_date=reference_date,
            )
            for code in normalized_codes
        }

    def assert_required_codes_fresh_or_stop(
        self,
        codes: list[str],
        reference_date: str,
        max_stale_days: int = 5,
        table_name: str = "daily_bars",
    ) -> dict[str, FreshnessStatus]:
        """必須銘柄の欠損またはerrorレベルの古さがあれば処理を停止する。"""
        statuses = self.check_required_codes_freshness(
            codes,
            reference_date=reference_date,
            max_stale_days=max_stale_days,
            table_name=table_name,
        )
        if not statuses:
            raise SystemError("データ鮮度の確認対象銘柄が0件です")

        errors = {
            code: status
            for code, status in statuses.items()
            if status.level == "error"
        }
        if errors:
            details = "; ".join(
                f"{code}={status.message}"
                for code, status in list(errors.items())[:20]
            )
            if len(errors) > 20:
                details += f"; ほか{len(errors) - 20}件"
            raise SystemError(
                "必須銘柄のデータ鮮度エラーのため処理を停止します: "
                f"{details}"
            )

        warnings = [
            status for status in statuses.values() if status.level == "warning"
        ]
        for status in warnings:
            logger.warning(status.message)

        logger.info(
            "必須銘柄のデータ鮮度確認完了: required=%d, fresh=%d, warning=%d, "
            "reference=%s",
            len(statuses),
            sum(status.is_fresh for status in statuses.values()),
            len(warnings),
            reference_date,
        )
        return statuses

    def assert_fresh_data_or_stop(
        self,
        allow_stale: bool = False,
        table_name: str = "daily_bars",
    ) -> FreshnessStatus:
        """データが新鮮でない場合はエラーを出す。"""
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
        """全テーブルの鮮度レポートを生成する。"""
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
            except Exception as error:
                report[table] = {
                    "latest_date": None,
                    "days_stale": 9999,
                    "level": "error",
                    "message": str(error),
                }

        return report
