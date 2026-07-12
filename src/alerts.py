"""
アラート管理モジュール

ファイルパス: src/alerts.py
何をするか: アラートの生成・送信・記録を行う
なぜ存在するか: 重要なイベントをユーザーに通知するため
関連ファイル: config.py, data_store.py
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

from .config import Config
from .market_calendar import JST

logger = logging.getLogger(__name__)


def _resolve_target_date(target_date: str | None) -> str:
    if target_date is None:
        return datetime.now(JST).strftime("%Y-%m-%d")
    try:
        parsed = datetime.strptime(target_date, "%Y-%m-%d")
    except (TypeError, ValueError) as error:
        raise ValueError(f"target_dateはYYYY-MM-DD形式で指定してください: {target_date!r}") from error
    if parsed.strftime("%Y-%m-%d") != target_date:
        raise ValueError(f"target_dateはYYYY-MM-DD形式で指定してください: {target_date!r}")
    return target_date


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
        self.config = config
        self.db_path = Path(config.database_path)
        self.alerts_config = config.get("alerts", {})

        self.enabled = self.alerts_config.get("enabled", False)
        self.console_enabled = self.alerts_config.get("console", True)
        self.file_enabled = self.alerts_config.get("file", True)
        self.webhook_enabled = self.alerts_config.get("webhook", {}).get("enabled", False)
        self.webhook_url = self.alerts_config.get("webhook", {}).get("url", "")
        self.score_threshold = self.alerts_config.get("score_threshold", 90)
        self.notify_new_candidates = self.alerts_config.get("notify_new_candidates", True)
        self.notify_sell_watch = self.alerts_config.get("notify_sell_watch", True)
        self.notify_stale_data = self.alerts_config.get("notify_stale_data", True)

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得する"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _is_already_sent(self, code: str, date: str, alert_type: str) -> bool:
        """アラートが既に送信済みかを確認する"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM alert_logs
                WHERE code = ? AND date = ? AND alert_type = ?
                """,
                (code, date, alert_type),
            )
            return cursor.fetchone()[0] > 0

    def _save_alert_log(
        self,
        code: str,
        date: str,
        alert_type: str,
        message: str,
        sent_to: str,
    ) -> None:
        """アラートログを保存する"""
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
                logger.error("アラートログ保存エラー: %s", e)

    def send_console(self, alert: Alert) -> None:
        """コンソールにアラートを出力する"""
        print(f"\n[ALERT] {alert.alert_type}")
        print(f"  銘柄: {alert.code}")
        print(f"  日付: {alert.date}")
        print(f"  メッセージ: {alert.message}")
        print("  注意: これは売買指示ではありません。最終判断は手動で行ってください。")

    def send_file(self, alert: Alert) -> None:
        """ファイルにアラートを出力する"""
        output_dir = Path(self.config.get("report.output_dir", "reports"))
        output_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y%m%d")
        filepath = output_dir / f"alerts_{date_str}.txt"

        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"\n[{alert.alert_type}] {alert.date}\n")
            f.write(f"  銘柄: {alert.code}\n")
            f.write(f"  メッセージ: {alert.message}\n")
            f.write("  注意: これは売買指示ではありません。最終判断は手動で行ってください。\n")

        logger.info("アラート出力: %s", filepath)

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
                    "注意: これは売買指示ではありません。"
                )
            }
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            logger.info("Webhook送信完了: %s", alert.code)
        except Exception as e:
            logger.error("Webhook送信エラー: %s", e)

    def send_alert(self, alert: Alert) -> bool:
        """アラートを送信する（重複防止付き）"""
        if self._is_already_sent(alert.code, alert.date, alert.alert_type):
            logger.debug("アラート済み: %s %s", alert.code, alert.alert_type)
            return False

        if self.console_enabled:
            self.send_console(alert)

        if self.file_enabled:
            self.send_file(alert)

        if self.webhook_enabled:
            self.send_webhook(alert)

        sent_to = []
        if self.console_enabled:
            sent_to.append("console")
        if self.file_enabled:
            sent_to.append("file")
        if self.webhook_enabled:
            sent_to.append("webhook")

        self._save_alert_log(
            alert.code,
            alert.date,
            alert.alert_type,
            alert.message,
            ",".join(sent_to),
        )

        return True

    def check_new_candidates(self, target_date: str | None = None) -> list[Alert]:
        """指定対象日の新しい買い候補をチェックする。"""
        if not self.notify_new_candidates:
            return []

        alerts = []
        today = _resolve_target_date(target_date)

        with self._get_connection() as conn:
            candidates = conn.execute(
                """
                SELECT s.code, s.date, s.score, s.reason, sym.name
                FROM signals s
                LEFT JOIN symbols sym ON s.code = sym.code
                WHERE s.signal_type = 'BUY_CANDIDATE'
                  AND s.date = ?
                """,
                (today,),
            ).fetchall()

        for c in candidates:
            code = c["code"]
            score = c["score"]
            name = c["name"] or ""
            reason = c["reason"] or ""
            score_text = f"{score:.0f}" if score is not None else "N/A"

            if score is not None and score >= self.score_threshold:
                alert_type = "HIGH_SCORE_CANDIDATE"
                title = "高スコア候補検出"
            else:
                alert_type = "NEW_CANDIDATE"
                title = "候補検出"

            message = (
                f"{title}: {name}\n"
                f"スコア: {score_text}\n"
                f"理由: {reason}\n"
                "注意: これは売買指示ではありません。最終判断は手動で行ってください。"
            )

            alerts.append(Alert(code=code, date=today, alert_type=alert_type, message=message))

        return alerts

    def check_sell_watch(self, target_date: str | None = None) -> list[Alert]:
        """指定対象日の売り警戒をチェックする。"""
        if not self.notify_sell_watch:
            return []

        alerts = []
        today = _resolve_target_date(target_date)

        with self._get_connection() as conn:
            positions = conn.execute(
                """
                SELECT code, SUM(CASE WHEN side = 'BUY' THEN quantity ELSE -quantity END) as qty
                FROM trades_manual
                GROUP BY code
                HAVING qty > 0
                """
            ).fetchall()

            for pos in positions:
                code = pos["code"]
                signal = conn.execute(
                    """
                    SELECT signal_type, reason FROM signals
                    WHERE code = ? AND date = ?
                    """,
                    (code, today),
                ).fetchone()

                if signal and signal["signal_type"] == "EXCLUDE":
                    alerts.append(Alert(
                        code=code,
                        date=today,
                        alert_type="SELL_WATCH",
                        message=(
                            f"保有銘柄に売り警戒: {code}\n"
                            f"理由: {signal['reason']}\n"
                            "注意: これは売買指示ではありません。最終判断は手動で行ってください。"
                        ),
                    ))

        return alerts

    def check_data_freshness(self, target_date: str | None = None) -> list[Alert]:
        """指定対象日を基準にデータ鮮度をチェックする。"""
        if not self.notify_stale_data:
            return []

        from .data_freshness import DataFreshnessGuard

        resolved_date = _resolve_target_date(target_date)
        guard = DataFreshnessGuard(self.config)
        status = guard.check_freshness(reference_date=resolved_date)

        if status.level in ["warning", "error"]:
            return [Alert(
                code="SYSTEM",
                date=resolved_date,
                alert_type="STALE_DATA",
                message=(
                    f"データ鮮度警告: {status.message}\n"
                    f"最新日付: {status.latest_date}\n"
                    f"古い日数: {status.days_stale}日"
                ),
            )]

        return []

    def run_all_checks(self, target_date: str | None = None) -> list[Alert]:
        """指定対象日について全アラートチェックを実行する。"""
        if not self.enabled:
            logger.info("alerts.enabled=false のためアラート送信をスキップします")
            return []

        resolved_date = _resolve_target_date(target_date)
        all_alerts = []
        all_alerts.extend(self.check_new_candidates(resolved_date))
        all_alerts.extend(self.check_sell_watch(resolved_date))
        all_alerts.extend(self.check_data_freshness(resolved_date))

        sent_alerts = []
        for alert in all_alerts:
            if self.send_alert(alert):
                sent_alerts.append(alert)

        logger.info("アラートチェック完了: %s件送信", len(sent_alerts))
        return sent_alerts
