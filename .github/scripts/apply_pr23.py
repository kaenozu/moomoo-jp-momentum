from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Operational notifier: independent of SQLite and normal alert logging.
# ---------------------------------------------------------------------------
write(
    "src/operational_notifier.py",
    '''"""Best-effort operational failure notifications.

This path intentionally does not use SQLite or ``AlertManager`` so failures in
normal data processing can still be reported. Notifications are disabled by
default and reuse the configured alerts webhook endpoint.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from .config import Config

logger = logging.getLogger(__name__)


def _read_bool(config: Config, key: str, default: bool) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key}はtrue/falseで指定してください: {value!r}")
    return value


class OperationalNotifier:
    """Send operational failures through the existing webhook endpoint."""

    def __init__(self, config: Config):
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
        """Send a failure without raising on transport errors."""
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
        payload = {
            "text": (
                f"[OPERATIONAL_FAILURE] {event_type}\n"
                f"対象日: {target_date or 'N/A'}\n"
                f"メッセージ: {message}\n"
                f"コンテキスト: {context_text}"
            ),
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
        except requests.RequestException as error:
            logger.error(
                "運用異常Webhook送信エラー: event=%s error=%s",
                event_type,
                error,
            )
            return False

        logger.info("運用異常Webhook送信完了: event=%s", event_type)
        return True
''',
)


# ---------------------------------------------------------------------------
# Daily cycle: JPX closure guard, failure classification/notification, and
# target-date-aware normal alerts.
# ---------------------------------------------------------------------------
path = "run_daily_cycle.py"
text = read(path)
text = replace_once(
    text,
    "from src.indicators import calculate_indicators_batch, indicators_to_dataframe\nfrom src.quote_service import QuoteService\n",
    "from src.indicators import calculate_indicators_batch, indicators_to_dataframe\n"
    "from src.market_calendar import JST, get_jpx_calendar\n"
    "from src.operational_notifier import OperationalNotifier\n"
    "from src.quote_service import QuoteService\n",
    "run_daily_cycle imports",
)
text = replace_once(
    text,
    '''class DailyCycleStoppedError(RuntimeError):
    """Expected operational stop caused by a failed daily-cycle guard."""
''',
    '''class DailyCycleStoppedError(RuntimeError):
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
''',
    "DailyCycleStoppedError",
)
text = replace_once(
    text,
    '''        raise DailyCycleStoppedError(
            "仮想取引整合性チェックでエラーを検出しました: "
            f"strategy={strategy_name}, date={target_date}, "
            f"errors={error_count}, warnings={warning_count}"
        )
''',
    '''        raise DailyCycleStoppedError(
            "仮想取引整合性チェックでエラーを検出しました: "
            f"strategy={strategy_name}, date={target_date}, "
            f"errors={error_count}, warnings={warning_count}",
            event_type="integrity_failure",
            context={"errors": error_count, "warnings": warning_count},
        )
''',
    "integrity error classification",
)
text = replace_once(
    text,
    '''        raise DailyCycleStoppedError(
            "仮想取引整合性チェックの警告を厳格設定によりエラー扱いします: "
            f"strategy={strategy_name}, date={target_date}, "
            f"warnings={warning_count}"
        )
''',
    '''        raise DailyCycleStoppedError(
            "仮想取引整合性チェックの警告を厳格設定によりエラー扱いします: "
            f"strategy={strategy_name}, date={target_date}, "
            f"warnings={warning_count}",
            event_type="integrity_warning_strict",
            context={"warnings": warning_count},
        )
''',
    "integrity warning classification",
)
text = replace_once(
    text,
    '''        "integrity_warnings": 0,
        "integrity_exit_code": 0,
    }
''',
    '''        "integrity_warnings": 0,
        "integrity_exit_code": 0,
        "calendar_checked": False,
        "is_trading_day": False,
        "cycle_skipped": False,
        "skip_reason": "",
    }
''',
    "daily result calendar schema",
)
text = replace_once(
    text,
    '''    results["virtual_trade_enabled"] = virtual_trade_enabled
    results["integrity_check_enabled"] = integrity_enabled
    results["integrity_fail_on_warning"] = integrity_fail_on_warning

    if dry_run:
''',
    '''    results["virtual_trade_enabled"] = virtual_trade_enabled
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
''',
    "daily calendar guard",
)
text = replace_once(
    text,
    '''    if not status.connected:
        logger.error("OpenD接続失敗: %s", status.message)
        return results
''',
    '''    if not status.connected:
        logger.error("OpenD接続失敗: %s", status.message)
        raise DailyCycleStoppedError(
            f"OpenD接続失敗: {status.message}",
            event_type="opend_connection_failure",
            context={"status_message": status.message},
        )
''',
    "OpenD failure classification",
)
text = replace_once(
    text,
    '''        freshness_by_code = _assert_cycle_data_freshness(
            config,
            codes,
            target_date,
        )
''',
    '''        try:
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
''',
    "freshness failure classification",
)
text = replace_once(
    text,
    "        alerts = alert_manager.run_all_checks()\n",
    "        alerts = alert_manager.run_all_checks(target_date=target_date)\n",
    "target-date alert call",
)
text = replace_once(
    text,
    '''        default=datetime.now().strftime("%Y-%m-%d"),
''',
    '''        default=_default_target_date(),
''',
    "JST CLI default",
)
text = replace_once(
    text,
    '''    except (SystemError, DailyCycleStoppedError) as error:
        logger.error("日次サイクル停止: %s", error)
        return 1
    except Exception as error:
        logger.error("日次サイクル失敗: %s", error)
        return 1
''',
    '''    except DailyCycleStoppedError as error:
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
''',
    "main failure notifications",
)
text = replace_once(
    text,
    '''    print(
        "\\n[DONE] dry-run 完了"
        if args.dry_run
        else f"\\n[DONE] 日次サイクル完了: {args.date}"
    )
''',
    '''    if args.dry_run:
        print("\\n[DONE] dry-run 完了")
    elif results.get("cycle_skipped"):
        print(f"\\n[SKIP] JPX休場日のため処理なし: {args.date}")
    else:
        print(f"\\n[DONE] 日次サイクル完了: {args.date}")
''',
    "skip completion output",
)
write(path, text)


# ---------------------------------------------------------------------------
# Normal alerts: use the requested processing date, not wall-clock date.
# ---------------------------------------------------------------------------
path = "src/alerts.py"
text = read(path)
text = replace_once(
    text,
    "from .config import Config\n",
    "from .config import Config\nfrom .market_calendar import JST\n",
    "alerts JST import",
)
text = replace_once(
    text,
    '''logger = logging.getLogger(__name__)


@dataclass
''',
    '''logger = logging.getLogger(__name__)


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
''',
    "alerts target resolver",
)
text = replace_once(
    text,
    '    def check_new_candidates(self) -> list[Alert]:\n        """新しい買い候補をチェックする"""\n',
    '    def check_new_candidates(self, target_date: str | None = None) -> list[Alert]:\n        """指定対象日の新しい買い候補をチェックする。"""\n',
    "new candidate signature",
)
text = replace_once(
    text,
    '        today = datetime.now().strftime("%Y-%m-%d")\n\n        with self._get_connection() as conn:\n',
    '        today = _resolve_target_date(target_date)\n\n        with self._get_connection() as conn:\n',
    "new candidate date",
)
text = replace_once(
    text,
    '    def check_sell_watch(self) -> list[Alert]:\n        """売り警戒をチェックする"""\n',
    '    def check_sell_watch(self, target_date: str | None = None) -> list[Alert]:\n        """指定対象日の売り警戒をチェックする。"""\n',
    "sell watch signature",
)
# The second wall-clock assignment belongs to sell-watch.
old = '        today = datetime.now().strftime("%Y-%m-%d")\n\n        with self._get_connection() as conn:\n'
if text.count(old) != 1:
    raise RuntimeError(f"sell watch date: expected one remaining match, found {text.count(old)}")
text = text.replace(old, '        today = _resolve_target_date(target_date)\n\n        with self._get_connection() as conn:\n', 1)
text = replace_once(
    text,
    '    def check_data_freshness(self) -> list[Alert]:\n        """データ鮮度をチェックする"""\n',
    '    def check_data_freshness(self, target_date: str | None = None) -> list[Alert]:\n        """指定対象日を基準にデータ鮮度をチェックする。"""\n',
    "freshness alert signature",
)
text = replace_once(
    text,
    '''        guard = DataFreshnessGuard(self.config)
        status = guard.check_freshness()

        if status.level in ["warning", "error"]:
            return [Alert(
                code="SYSTEM",
                date=datetime.now().strftime("%Y-%m-%d"),
''',
    '''        resolved_date = _resolve_target_date(target_date)
        guard = DataFreshnessGuard(self.config)
        status = guard.check_freshness(reference_date=resolved_date)

        if status.level in ["warning", "error"]:
            return [Alert(
                code="SYSTEM",
                date=resolved_date,
''',
    "freshness alert target date",
)
text = replace_once(
    text,
    '    def run_all_checks(self) -> list[Alert]:\n        """全アラートチェックを実行する"""\n',
    '    def run_all_checks(self, target_date: str | None = None) -> list[Alert]:\n        """指定対象日について全アラートチェックを実行する。"""\n',
    "run all alerts signature",
)
text = replace_once(
    text,
    '''        all_alerts = []
        all_alerts.extend(self.check_new_candidates())
        all_alerts.extend(self.check_sell_watch())
        all_alerts.extend(self.check_data_freshness())
''',
    '''        resolved_date = _resolve_target_date(target_date)
        all_alerts = []
        all_alerts.extend(self.check_new_candidates(resolved_date))
        all_alerts.extend(self.check_sell_watch(resolved_date))
        all_alerts.extend(self.check_data_freshness(resolved_date))
''',
    "run all alert date propagation",
)
write(path, text)


# ---------------------------------------------------------------------------
# Scheduler: notify only failures that the child cannot notify itself.
# ---------------------------------------------------------------------------
path = "scheduler.py"
text = read(path)
text = replace_once(
    text,
    "from src.config import load_config\n",
    "from src.config import load_config\n"
    "from src.market_calendar import JST\n"
    "from src.operational_notifier import OperationalNotifier\n",
    "scheduler imports",
)
text = replace_once(
    text,
    '''def job_connection_check(config_path: str = "config.yaml") -> None:
    """Verify OpenD connectivity without running the data pipeline."""
    from src.connection import OpenDConnection

    config = load_config(config_path)
    with OpenDConnection(config) as connection:
        status = connection.connect()
        if not status.connected:
            raise RuntimeError(f"OpenD接続失敗: {status.message}")
    logger.info("OpenD接続確認成功")


def job_daily_cycle(config_path: str = "config.yaml") -> None:
    """Run update, indicators, screening, virtual fills, reports, and alerts sequentially."""
    _run_script(
        ["run_daily_cycle.py", "--config", config_path],
        timeout=7200,
        name="日次運用サイクル",
    )
''',
    '''def _notify_scheduler_failure(
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
''',
    "scheduler operational notification",
)
# datetime is needed by the notification helper.
text = replace_once(
    text,
    "import sys\nfrom collections.abc import Callable\n",
    "import sys\nfrom collections.abc import Callable\nfrom datetime import datetime\n",
    "scheduler datetime import",
)
write(path, text)


# ---------------------------------------------------------------------------
# Configuration and documentation.
# ---------------------------------------------------------------------------
for config_path in ("config.example.yaml", "tests/fixtures/config.test.yaml"):
    text = read(config_path)
    text = replace_once(
        text,
        '''  webhook:
    enabled: false
    url: ''
''',
        '''  webhook:
    enabled: false
    url: ''
  operational:
    enabled: false
    timeout_seconds: 10
''',
        f"{config_path} operational config",
    )
    write(config_path, text)

readme = read("README.md")
marker = "## 運用上の安全策"
if marker not in readme:
    readme += '''

## 運用上の安全策

- `run_daily_cycle.py` はJPX休場日にはOpenDやSQLiteへ接続せず正常スキップします。
- 通常アラートは `--date` で指定した対象日を使用します。
- 運用異常Webhookは既定で無効です。利用時は次を設定します。

```yaml
alerts:
  webhook:
    enabled: true
    url: "https://example.invalid/webhook"
  operational:
    enabled: true
    timeout_seconds: 10
```

運用異常通知はSQLiteへ依存せず、OpenD接続失敗、データ鮮度停止、仮想取引整合性停止、想定外例外、scheduler timeoutを通知対象にします。
'''
write("README.md", readme)


# ---------------------------------------------------------------------------
# Regression tests.
# ---------------------------------------------------------------------------
write(
    "tests/test_daily_cycle_operational_guards.py",
    '''"""Daily-cycle market closure and operational notification regressions."""

from __future__ import annotations

import sys
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

import run_daily_cycle
from src.market_calendar import JST, UnsupportedCalendarYear


def test_jpx_holiday_skips_before_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    class _UnexpectedConnection:
        def __init__(self, *_args: Any, **_kwargs: Any):
            raise AssertionError("休場日はOpenDへ接続してはいけません")

    monkeypatch.setattr(run_daily_cycle, "OpenDConnection", _UnexpectedConnection)
    results = run_daily_cycle.run_cycle(
        "2026-07-20",
        config_path="tests/fixtures/config.test.yaml",
    )

    assert results["calendar_checked"] is True
    assert results["is_trading_day"] is False
    assert results["cycle_skipped"] is True
    assert results["skip_reason"] == "jpx_market_closed"
    assert results["connection_attempted"] is False
    assert results["database_write_attempted"] is False


def test_weekend_dry_run_is_a_clean_skip() -> None:
    results = run_daily_cycle.run_cycle(
        "2026-07-12",
        dry_run=True,
        config_path="tests/fixtures/config.test.yaml",
    )
    assert results["cycle_skipped"] is True
    assert results["is_trading_day"] is False


def test_trading_day_dry_run_reports_calendar_state() -> None:
    results = run_daily_cycle.run_cycle(
        "2026-07-13",
        dry_run=True,
        config_path="tests/fixtures/config.test.yaml",
    )
    assert results["calendar_checked"] is True
    assert results["is_trading_day"] is True
    assert results["cycle_skipped"] is False
    assert results["skip_reason"] == ""


def test_unsupported_calendar_year_is_not_silently_skipped() -> None:
    with pytest.raises(UnsupportedCalendarYear):
        run_daily_cycle.run_cycle(
            "2028-01-04",
            dry_run=True,
            config_path="tests/fixtures/config.test.yaml",
        )


def test_opend_failure_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Connection:
        def __init__(self, _config: Any):
            pass

        def connect(self) -> SimpleNamespace:
            return SimpleNamespace(connected=False, message="offline", quote_context=None)

    monkeypatch.setattr(run_daily_cycle, "OpenDConnection", _Connection)
    with pytest.raises(run_daily_cycle.DailyCycleStoppedError) as caught:
        run_daily_cycle.run_cycle(
            "2026-07-13",
            config_path="tests/fixtures/config.test.yaml",
        )
    assert caught.value.event_type == "opend_connection_failure"


def test_main_notifies_classified_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str]] = []

    def fail_cycle(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        raise run_daily_cycle.DailyCycleStoppedError(
            "integrity failed",
            event_type="integrity_failure",
        )

    def notify(
        _config_path: str,
        event_type: str,
        target_date: str,
        message: str,
        _context: dict[str, object] | None = None,
    ) -> bool:
        calls.append((event_type, target_date, message))
        return True

    monkeypatch.setattr(run_daily_cycle, "run_cycle", fail_cycle)
    monkeypatch.setattr(run_daily_cycle, "_notify_operational_failure", notify)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_daily_cycle.py", "--date", "2026-07-13", "--config", "x.yaml"],
    )

    assert run_daily_cycle.main() == 1
    assert calls == [("integrity_failure", "2026-07-13", "integrity failed")]


def test_default_target_date_uses_jst(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == JST
            return cls(2026, 7, 13, 0, 5, tzinfo=JST)

    monkeypatch.setattr(run_daily_cycle, "datetime", _FixedDateTime)
    assert run_daily_cycle._default_target_date() == "2026-07-13"
''',
)

write(
    "tests/test_operational_notifier.py",
    '''"""Operational notifier and scheduler notification regressions."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
import requests

import scheduler
from src.operational_notifier import OperationalNotifier


class _ConfigStub:
    def __init__(self, values: dict[str, Any]):
        self.values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def _enabled_config() -> _ConfigStub:
    return _ConfigStub(
        {
            "alerts.operational.enabled": True,
            "alerts.webhook.enabled": True,
            "alerts.webhook.url": "https://example.invalid/hook",
            "alerts.operational.timeout_seconds": 4,
        }
    )


def test_operational_notifier_posts_structured_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

    def post(url: str, *, json: dict[str, Any], timeout: float) -> _Response:
        captured.update(url=url, json=json, timeout=timeout)
        return _Response()

    monkeypatch.setattr("src.operational_notifier.requests.post", post)
    notifier = OperationalNotifier(_enabled_config())

    assert notifier.send_failure(
        "integrity_failure",
        "broken",
        target_date="2026-07-13",
        context={"errors": 2},
    ) is True
    assert captured["url"] == "https://example.invalid/hook"
    assert captured["timeout"] == 4.0
    assert captured["json"]["event_type"] == "integrity_failure"
    assert captured["json"]["context"] == {"errors": 2}


def test_operational_notifier_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.operational_notifier.requests.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not post")),
    )
    notifier = OperationalNotifier(_ConfigStub({}))
    assert notifier.send_failure("x", "y") is False


def test_operational_notifier_transport_error_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("src.operational_notifier.requests.post", fail)
    assert OperationalNotifier(_enabled_config()).send_failure("x", "y") is False


def test_scheduler_timeout_notifies_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def timeout(*_args: Any, **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=7200)

    def notify(
        _config_path: str,
        event_type: str,
        message: str,
        _context: dict[str, object] | None = None,
    ) -> bool:
        calls.append((event_type, message))
        return True

    monkeypatch.setattr(scheduler, "_run_script", timeout)
    monkeypatch.setattr(scheduler, "_notify_scheduler_failure", notify)

    with pytest.raises(RuntimeError, match="タイムアウト"):
        scheduler.job_daily_cycle("config.yaml")
    assert calls and calls[0][0] == "scheduler_timeout"


def test_connection_check_failure_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Connection:
        def __init__(self, _config: Any):
            pass

        def __enter__(self) -> "_Connection":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def connect(self) -> SimpleNamespace:
            return SimpleNamespace(connected=False, message="offline")

    monkeypatch.setattr("src.connection.OpenDConnection", _Connection)
    monkeypatch.setattr(scheduler, "load_config", lambda _path: _ConfigStub({}))
    monkeypatch.setattr(
        scheduler,
        "_notify_scheduler_failure",
        lambda _path, event_type, _message, _context=None: calls.append(event_type) or True,
    )

    with pytest.raises(RuntimeError, match="OpenD接続失敗"):
        scheduler.job_connection_check("config.yaml")
    assert calls == ["opend_connection_check_failure"]
''',
)

write(
    "tests/test_alert_target_date.py",
    '''"""Normal alert generation must follow the requested processing date."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from src.alerts import AlertManager
from src.data_freshness import FreshnessStatus


class _ConfigStub:
    def __init__(self, database_path: Path):
        self.database_path = str(database_path)
        self.values: dict[str, Any] = {
            "alerts": {
                "enabled": True,
                "console": False,
                "file": False,
                "webhook": {"enabled": False, "url": ""},
                "score_threshold": 90,
                "notify_new_candidates": True,
                "notify_sell_watch": True,
                "notify_stale_data": True,
            },
            "report.output_dir": str(database_path.parent / "reports"),
        }

    def get(self, key: str, default: Any = None) -> Any:
        if key == "alerts":
            return self.values["alerts"]
        return self.values.get(key, default)


def _manager(tmp_path: Path) -> AlertManager:
    database = tmp_path / "alerts.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE symbols (code TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE signals (
                code TEXT, date TEXT, score REAL, reason TEXT, signal_type TEXT
            );
            CREATE TABLE trades_manual (code TEXT, side TEXT, quantity INTEGER);
            CREATE TABLE alert_logs (
                code TEXT, date TEXT, alert_type TEXT, message TEXT,
                sent_to TEXT, created_at TEXT,
                UNIQUE(code, date, alert_type)
            );
            INSERT INTO symbols VALUES ('JP.7203', 'Toyota');
            INSERT INTO signals VALUES (
                'JP.7203', '2026-07-10', 95, 'momentum', 'BUY_CANDIDATE'
            );
            INSERT INTO signals VALUES (
                'JP.7203', '2026-07-13', 10, 'excluded', 'EXCLUDE'
            );
            INSERT INTO trades_manual VALUES ('JP.7203', 'BUY', 1);
            """
        )
    return AlertManager(_ConfigStub(database))


def test_candidate_alert_uses_target_date(tmp_path: Path) -> None:
    alerts = _manager(tmp_path).check_new_candidates("2026-07-10")
    assert len(alerts) == 1
    assert alerts[0].date == "2026-07-10"
    assert alerts[0].code == "JP.7203"


def test_sell_watch_uses_target_date(tmp_path: Path) -> None:
    alerts = _manager(tmp_path).check_sell_watch("2026-07-13")
    assert len(alerts) == 1
    assert alerts[0].date == "2026-07-13"


def test_freshness_alert_uses_target_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str | None] = []

    class _Guard:
        def __init__(self, _config: Any):
            pass

        def check_freshness(self, reference_date: str | None = None) -> FreshnessStatus:
            captured.append(reference_date)
            return FreshnessStatus(
                is_fresh=False,
                latest_date="2026-07-09",
                days_stale=1,
                message="stale",
                level="warning",
            )

    monkeypatch.setattr("src.data_freshness.DataFreshnessGuard", _Guard)
    alerts = _manager(tmp_path).check_data_freshness("2026-07-10")
    assert captured == ["2026-07-10"]
    assert alerts[0].date == "2026-07-10"


def test_run_all_checks_propagates_one_resolved_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        manager,
        "check_new_candidates",
        lambda target_date=None: calls.append(("candidate", target_date)) or [],
    )
    monkeypatch.setattr(
        manager,
        "check_sell_watch",
        lambda target_date=None: calls.append(("sell", target_date)) or [],
    )
    monkeypatch.setattr(
        manager,
        "check_data_freshness",
        lambda target_date=None: calls.append(("freshness", target_date)) or [],
    )

    assert manager.run_all_checks("2026-07-10") == []
    assert calls == [
        ("candidate", "2026-07-10"),
        ("sell", "2026-07-10"),
        ("freshness", "2026-07-10"),
    ]
''',
)

# Existing exact-dictionary dry-run regression needs the stable calendar keys.
path = "tests/test_regressions.py"
text = read(path)
text = replace_once(
    text,
    '''        "integrity_warnings": 0,
        "integrity_exit_code": 0,
        "symbols": 2,
''',
    '''        "integrity_warnings": 0,
        "integrity_exit_code": 0,
        "calendar_checked": True,
        "is_trading_day": True,
        "cycle_skipped": False,
        "skip_reason": "",
        "symbols": 2,
''',
    "dry-run expected calendar schema",
)
write(path, text)

# Include new files in the focused Pyright configuration.
path = "pyrightconfig.json"
config = json.loads(read(path))
include = config.setdefault("include", [])
for item in (
    "src/operational_notifier.py",
    "tests/test_daily_cycle_operational_guards.py",
    "tests/test_operational_notifier.py",
    "tests/test_alert_target_date.py",
):
    if item not in include:
        include.append(item)
write(path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")

# Remove the one-shot applicator and its workflow from the resulting commit.
(ROOT / ".github/scripts/apply_pr23.py").unlink()
workflow = ROOT / ".github/workflows/apply-pr23.yml"
if workflow.exists():
    workflow.unlink()
