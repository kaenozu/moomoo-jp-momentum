"""
アラート管理モジュール

ファイルパス: src/alerts.py
何をするか: アラートの生成・送信・記録を行う
なぜ存在するか: 重要なイベントをユーザーに通知するため
関連ファイル: config.py, data_store.py

アラート条件:
- 新しい買い候補が出た
- スコア90点以上の候補が出た
- 保有銘柄に売り警戒が出た
- データ鮮度が古い
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """アラート"""
    code: str
    date: str
    alert_type: str
    message: str
    sent_to: str = ""


class AlertManager:
    """アラート管理クラス"""

    def __init__(self, config: Config):
        """
        Args:
            config: 設定オブジェクト
        """
        self.config = config
        self.db_path = Path(config.database_path)
        self.alerts_config = config.get("alerts", {})

        # 設定値
        self.console_enabled = self.alerts_config.get("console", True)
        self.file_enabled = self.alerts_config.get("file", True)
        self.webhook_enabled = self.alerts_config.get("webhook", {}).get("enabled", False)
        self.webhook_url = self.alerts_config.get("webhook", {}).get("url", "")
        self.score_threshold = self.alerts_config.get("score_threshold", 90)

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得する"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _is_already_sent(
        self,
        code: str,
        date: str,
        alert_type: str,
    ) -> bool:
        """
        アラートが既に送信済みかを確認する

        Args:
            code: 銘柄コード
            date: 日付
            alert_type: アラート種別

        Returns:
            bool: 送信済みならTrue
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM alert_logs
                WHERE code = ? AND date = ? AND alert_type = ?
                """,
                (code, date, alert_type),
            )
            count = cursor.fetchone()[0]
            return count > 0

    def _save_alert_log(
        self,
        code: str,
        date: str,
        alert_type: str,
        message: str,
        sent_to: str,
    ) -> None:
        """
        アラートログを保存する

        Args:
            code: 銘柄コード
            date: 日付
            alert_type: アラート種別
            message: メッセージ
            sent_to: 送信先
        """
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO alert_logs
                    (code, date, alert_type, message, sent_to, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (code, date, alert_type, message, sent_to, now),
                )
            except sqlite3.Error as e:
                logger.error(f"アラートログ保存エラー: {e}")

    def send_console(self, alert: Alert) -> None:
        """コンソールにアラートを出力する"""
        print(f"\n[ALERT] {alert.alert_type}")
        print(f"  銘柄: {alert.code}")
        print(f"  日付: {alert.date}")
        print(f"  メッセージ: {alert.message}")
        print(f"  注意: これは売買指示ではありません。最終判断は手動で行ってください。")

    def send_file(self, alert: Alert) -> None:
        """ファイルにアラートを出力する"""
        output_dir = Path("reports")
        output_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y%m%d")
        filepath = output_dir / f"alerts_{date_str}.txt"

        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"\n[{alert.alert_type}] {alert.date}\n")
            f.write(f"  銘柄: {alert.code}\n")
            f.write(f"  メッセージ: {alert.message}\n")
            f.write(f"  注意: これは売買指示ではありません。最終判断は手動で行ってください。\n")

        logger.info(f"アラート出力: {filepath}")

    def send_webhook(self, alert: Alert) -> None:
        """Webhookにアラートを送信する"""
        if not self.webhook_enabled or not self.webhook_url:
            return

        try:
            payload = {
                "text": (
                    f"[{alert.alert_type}] {alert.code}\n"
                    f"日付: {alert.date}\n"
                    f"メッセージ: {alert.message}\n"
                    f"注意: これは売買指示ではありません。"
                )
            }
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            logger.info(f"Webhook送信完了: {alert.code}")
        except Exception as e:
            logger.error(f"Webhook送信エラー: {e}")

    def send_alert(self, alert: Alert) -> bool:
        """
        アラートを送信する（重複防止付き）

        Args:
            alert: アラート

        Returns:
            bool: 送信したらTrue
        """
        # 重複チェック
        if self._is_already_sent(alert.code, alert.date, alert.alert_type):
            logger.debug(f"アラート済み: {alert.code} {alert.alert_type}")
            return False

        # 送信
        if self.console_enabled:
            self.send_console(alert)

        if self.file_enabled:
            self.send_file(alert)

        if self.webhook_enabled:
            self.send_webhook(alert)

        # ログ保存
        sent_to = "console"
        if self.file_enabled:
            sent_to += ",file"
        if self.webhook_enabled:
            sent_to += ",webhook"

        self._save_alert_log(
            alert.code,
            alert.date,
            alert.alert_type,
            alert.message,
            sent_to,
        )

        return True

    def check_new_candidates(self) -> list[Alert]:
        """
        新しい買い候補をチェックする

        Returns:
            list[Alert]: 新規アラートのリスト
        """
        alerts = []
        today = datetime.now().strftime("%Y-%m-%d")

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT s.code, s.date, s.score, s.reason, sym.name
                FROM signals s
                LEFT JOIN symbols sym ON s.code = sym.code
                WHERE s.signal_type = 'BUY_CANDIDATE'
                AND s.date = ?
                """,
                (today,),
            )
            candidates = cursor.fetchall()

        for c in candidates:
            code = c["code"]
            score = c["score"]
            name = c["name"] or ""
            reason = c["reason"] or ""

            # スコア閾値チェック
            if score and score >= self.score_threshold:
                alert_type = "HIGH_SCORE_CANDIDATE"
                message = (
                    f"高スコア候補検出: {name}\n"
                    f"スコア: {score:.0f}\n"
                    f"理由: {reason}\n"
                    f"注意: これは売買指示ではありません。最終判断は手動で行ってください。"
                )
            else:
                alert_type = "NEW_CANDIDATE"
                message = (
                    f"候補検出: {name}\n"
                    f"スコア: {score:.0f}\n"
                    f"理由: {reason}\n"
                    f"注意: これは売買指示ではありません。最終判断は手動で行ってください。"
                )

            alert = Alert(
                code=code,
                date=today,
                alert_type=alert_type,
                message=message,
            )
            alerts.append(alert)

        return alerts

    def check_sell_watch(self) -> list[Alert]:
        """
        売り警戒をチェックする

        Returns:
            list[Alert]: 売り警戒アラートのリスト
        """
        alerts = []
        today = datetime.now().strftime("%Y-%m-%d")

        with self._get_connection() as conn:
            # 保有銘柄を取得
            cursor = conn.execute(
                """
                SELECT code, SUM(CASE WHEN side = 'BUY' THEN quantity ELSE -quantity END) as qty
                FROM trades_manual
                GROUP BY code
                HAVING qty > 0
                """
            )
            positions = cursor.fetchall()

        for pos in positions:
            code = pos["code"]

            # シグナルを確認
            cursor = conn.execute(
                """
                SELECT signal_type, reason FROM signals
                WHERE code = ? AND date = ?
                """,
                (code, today),
            )
            signal = cursor.fetchone()

            if signal and signal["signal_type"] == "EXCLUDE":
                alert = Alert(
                    code=code,
                    date=today,
                    alert_type="SELL_WATCH",
                    message=(
                        f"保有銘柄に売り警戒: {code}\n"
                        f"理由: {signal['reason']}\n"
                        f"注意: これは売買指示ではありません。最終判断は手動で行ってください。"
                    ),
                )
                alerts.append(alert)

        return alerts

    def check_data_freshness(self) -> list[Alert]:
        """
        データ鮮度をチェックする

        Returns:
            list[Alert]: データ鮮度アラートのリスト
        """
        from .data_freshness import DataFreshnessGuard
        guard = DataFreshnessGuard(self.config)
        status = guard.check_freshness()

        if status.level in ["warning", "error"]:
            alert = Alert(
                code="SYSTEM",
                date=datetime.now().strftime("%Y-%m-%d"),
                alert_type="STALE_DATA",
                message=(
                    f"データ鮮度警告: {status.message}\n"
                    f"最新日付: {status.latest_date}\n"
                    f"古い日数: {status.days_stale}日"
                ),
            )
            return [alert]

        return []

    def run_all_checks(self) -> list[Alert]:
        """
        全アラートチェックを実行する

        Returns:
            list[Alert]: 送信したアラートのリスト
        """
        all_alerts = []

        # 新規候補チェック
        new_candidates = self.check_new_candidates()
        all_alerts.extend(new_candidates)

        # 売り警戒チェック
        sell_watch = self.check_sell_watch()
        all_alerts.extend(sell_watch)

        # データ鮮度チェック
        stale_data = self.check_data_freshness()
        all_alerts.extend(stale_data)

        # 送信
        sent_alerts = []
        for alert in all_alerts:
            if self.send_alert(alert):
                sent_alerts.append(alert)

        logger.info(f"アラートチェック完了: {len(sent_alerts)}件送信")
        return sent_alerts
