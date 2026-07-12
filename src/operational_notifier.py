"""Best-effort operational failure notifications.

This path intentionally does not use SQLite or ``AlertManager`` so failures in
normal data processing can still be reported. Notifications are disabled by
default and reuse the configured alerts webhook endpoint.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import requests

logger = logging.getLogger(__name__)


class ConfigLike(Protocol):
    """Minimum configuration interface required by the notifier."""

    def get(self, key_path: str, default: Any = None, /) -> Any: ...


def _read_bool(config: ConfigLike, key: str, default: bool) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key}はtrue/falseで指定してください: {value!r}")
    return value


class OperationalNotifier:
    """Send operational failures through the existing webhook endpoint."""

    def __init__(self, config: ConfigLike):
        self.enabled = _read_bool(config, "alerts.operational.enabled", False)
        self.webhook_enabled = _read_bool(config, "alerts.webhook.enabled", False)
        self.webhook_url = str(config.get("alerts.webhook.url", "") or "").strip()
        raw_timeout = config.get("alerts.operational.timeout_seconds", 10)
        try:
            self.timeout_seconds = float(raw_timeout)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "alerts.operational.timeout_secondsは正の数で指定してください"
            ) from error
        if self.timeout_seconds <= 0:
            raise ValueError(
                "alerts.operational.timeout_secondsは正の数で指定してください"
            )

    @property
    def active(self) -> bool:
        return self.enabled and self.webhook_enabled and bool(self.webhook_url)

    def send_failure(
        self,
        event_type: str,
        message: str,
        *,
        target_date: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Send a failure without raising on transport or request setup errors."""
        if not self.active:
            logger.info("運用異常通知は無効です: event=%s", event_type)
            return False

        normalized_context = context or {}
        context_text = json.dumps(
            normalized_context,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        text = "\n".join(
            (
                f"[OPERATIONAL_FAILURE] {event_type}",
                f"対象日: {target_date or 'N/A'}",
                f"メッセージ: {message}",
                f"コンテキスト: {context_text}",
            )
        )
        payload = {
            "text": text,
            "event_type": event_type,
            "target_date": target_date,
            "message": message,
            "context": normalized_context,
        }
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except Exception as error:
            logger.error(
                "運用異常Webhook送信エラー: event=%s error=%s",
                event_type,
                error,
            )
            return False

        logger.info("運用異常Webhook送信完了: event=%s", event_type)
        return True
