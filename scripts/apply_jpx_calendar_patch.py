"""Apply the JPX calendar integration, then remove temporary patch files."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_daily_update() -> None:
    path = ROOT / "daily_update.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "  - 土日は簡易判定するが、日本の祝日・休場日は考慮しない。\n"
        "  - 時刻比較はローカルタイム（JST前提）で行う。",
        "  - JPX公式休業日を含む固定カレンダーで取得要否を判定する。\n"
        "  - 対応年外は処理を止め、カレンダー更新を要求する。",
        "daily_update module note",
    )
    text = replace_once(
        text,
        "from src.indicators import calculate_indicators_batch, indicators_to_dataframe, add_relative_strength\n"
        "from src.quote_service import QuoteService, BATCH_SLEEP_SECONDS",
        "from src.indicators import calculate_indicators_batch, indicators_to_dataframe, add_relative_strength\n"
        "from src.market_calendar import JST, latest_expected_trading_day\n"
        "from src.quote_service import QuoteService, BATCH_SLEEP_SECONDS",
        "daily_update imports",
    )

    start = text.index("def should_skip_fetch(")
    end = text.index("\n\ndef fetch_and_save_daily_klines(", start)
    replacement = '''def should_skip_fetch(
    data_store: DataStore,
    code: str,
    today: str,
    reference_datetime: datetime | None = None,
) -> bool:
    """Return True when the latest bar covers the expected JPX session.

    When ``reference_datetime`` is omitted, ``today`` is treated as end-of-day
    JST. This keeps direct and test callers deterministic while the production
    fetch path passes the actual current JST time.
    """
    latest_date = get_latest_bar_date(data_store, code)
    if latest_date is None:
        return False

    if reference_datetime is None:
        reference_datetime = datetime.strptime(today, "%Y-%m-%d").replace(
            hour=23,
            minute=59,
            second=59,
            tzinfo=JST,
        )
    normalized_reference = (
        reference_datetime.replace(tzinfo=JST)
        if reference_datetime.tzinfo is None
        else reference_datetime.astimezone(JST)
    )
    if normalized_reference.strftime("%Y-%m-%d") != today:
        raise ValueError(
            "todayとreference_datetimeの日付が一致しません: "
            f"today={today}, reference={normalized_reference.isoformat()}"
        )

    expected_date = latest_expected_trading_day(normalized_reference).isoformat()
    return latest_date >= expected_date
'''
    text = text[:start] + replacement + text[end:]

    text = replace_once(
        text,
        '    today = datetime.now().strftime("%Y-%m-%d")\n',
        '    reference_now = datetime.now(JST)\n'
        '    today = reference_now.strftime("%Y-%m-%d")\n',
        "daily_update reference time",
    )
    text = replace_once(
        text,
        "        if not force and should_skip_fetch(data_store, code, today):",
        "        if not force and should_skip_fetch(\n"
        "            data_store,\n"
        "            code,\n"
        "            today,\n"
        "            reference_datetime=reference_now,\n"
        "        ):",
        "daily_update skip call",
    )
    path.write_text(text, encoding="utf-8")


def patch_data_freshness() -> None:
    path = ROOT / "src" / "data_freshness.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "注意:\n"
        "    現在の鮮度判定は暦日ベースです。祝日・休場日を含む正確な営業日判定ではありません。",
        "注意:\n"
        "    鮮度はチェックイン済みJPX営業日カレンダーの未取得取引日数で判定します。",
        "data_freshness module note",
    )
    text = replace_once(
        text,
        "from .config import Config\n",
        "from .config import Config\n"
        "from .market_calendar import (\n"
        "    JST,\n"
        "    count_missing_trading_days,\n"
        "    expected_trading_day_for_date,\n"
        "    latest_expected_trading_day,\n"
        ")\n",
        "data_freshness imports",
    )

    start = text.index("    def _status_for_latest_date(")
    end = text.index("    def check_freshness(", start)
    replacement = '''    def _status_for_latest_date(
        self,
        latest_date: Optional[str],
        max_stale_days: int,
        code: Optional[str],
        reference_date: Optional[str],
    ) -> FreshnessStatus:
        """Convert the latest stored date into a JPX-session freshness status."""
        code_label = f"{code}: " if code else ""

        try:
            if reference_date:
                reference_calendar_date = datetime.strptime(
                    reference_date,
                    "%Y-%m-%d",
                ).date()
                reference_value: str | datetime = reference_date
                expected_date = expected_trading_day_for_date(
                    reference_calendar_date
                )
            else:
                reference_now = datetime.now(JST)
                reference_calendar_date = reference_now.date()
                reference_value = reference_now
                expected_date = latest_expected_trading_day(reference_now)
        except ValueError as error:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=9999,
                message=f"{code_label}基準日または営業日カレンダーのエラー: {error}",
                level="error",
            )
        except RuntimeError as error:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=9999,
                message=f"{code_label}営業日カレンダーの読み込みエラー: {error}",
                level="error",
            )

        if latest_date is None:
            reference_label = (
                f"（基準日 {reference_date} 以前）" if reference_date else ""
            )
            return FreshnessStatus(
                is_fresh=False,
                latest_date=None,
                days_stale=9999,
                message=f"{code_label}データがありません{reference_label}",
                level="error",
            )

        try:
            latest = datetime.strptime(latest_date, "%Y-%m-%d").date()
        except ValueError:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=9999,
                message=f"{code_label}最新日の日付形式エラー: {latest_date}",
                level="error",
            )

        if latest > expected_date:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=-1,
                message=(
                    f"{code_label}期待取引日より未来のデータです"
                    f"（latest={latest_date}, expected={expected_date.isoformat()}, "
                    f"reference={reference_date or reference_calendar_date.isoformat()}）"
                ),
                level="error",
            )

        try:
            days_stale = count_missing_trading_days(latest, reference_value)
        except (ValueError, RuntimeError) as error:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=9999,
                message=f"{code_label}営業日差を計算できません: {error}",
                level="error",
            )

        expected_label = expected_date.isoformat()
        if days_stale <= max_stale_days:
            return FreshnessStatus(
                is_fresh=True,
                latest_date=latest_date,
                days_stale=days_stale,
                message=(
                    f"{code_label}データは最新です"
                    f"（{latest_date}、未取得営業日{days_stale}日、"
                    f"期待取引日{expected_label}）"
                ),
                level="ok",
            )

        if days_stale <= 30:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=days_stale,
                message=(
                    f"{code_label}データが営業日で{days_stale}日分古いです"
                    f"（{latest_date}、期待取引日{expected_label}）"
                ),
                level="warning",
            )

        if days_stale <= 180:
            return FreshnessStatus(
                is_fresh=False,
                latest_date=latest_date,
                days_stale=days_stale,
                message=(
                    f"{code_label}データが営業日で{days_stale}日分古いです"
                    f"（{latest_date}）。シグナル判定を停止します。"
                ),
                level="error",
            )

        return FreshnessStatus(
            is_fresh=False,
            latest_date=latest_date,
            days_stale=days_stale,
            message=(
                f"{code_label}データが営業日で{days_stale}日分古いです"
                f"（{latest_date}）。180営業日以上前のデータです。"
            ),
            level="error",
        )

'''
    text = text[:start] + replacement + text[end:]
    text = text.replace(
        "基準日以前のデータについて鮮度をチェックする（暦日ベース）。",
        "基準日以前のデータについてJPX営業日ベースで鮮度をチェックする。",
    )
    text = text.replace("古い日数: {status.days_stale}日", "未取得営業日: {status.days_stale}日")
    path.write_text(text, encoding="utf-8")


def patch_freshness_tests() -> None:
    path = ROOT / "tests" / "test_data_freshness_codes.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '    assert statuses["JP.1306"].days_stale == 9\n',
        '    assert statuses["JP.1306"].days_stale == 7\n',
        "freshness warning expectation",
    )
    addition = '''\n\ndef test_holiday_gap_has_zero_missing_trading_days(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_bar(config, "JP.1306", "2026-05-01")
    guard = DataFreshnessGuard(config)

    status = guard.check_freshness(
        code="JP.1306",
        reference_date="2026-05-06",
        max_stale_days=0,
    )

    assert status.level == "ok"
    assert status.days_stale == 0
    assert "期待取引日2026-05-01" in status.message


def test_missing_day_count_starts_after_golden_week(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_bar(config, "JP.1306", "2026-05-01")
    guard = DataFreshnessGuard(config)

    status = guard.check_freshness(
        code="JP.1306",
        reference_date="2026-05-07",
        max_stale_days=0,
    )

    assert status.level == "warning"
    assert status.days_stale == 1
'''
    if "test_holiday_gap_has_zero_missing_trading_days" in text:
        raise RuntimeError("freshness holiday tests already exist")
    text += addition
    path.write_text(text, encoding="utf-8")


def patch_pyright() -> None:
    path = ROOT / "pyrightconfig.json"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '    "src/data_freshness.py",\n',
        '    "src/data_freshness.py",\n    "src/market_calendar.py",\n',
        "pyright market calendar source",
    )
    text = replace_once(
        text,
        '    "tests/test_data_freshness_codes.py",\n',
        '    "tests/test_data_freshness_codes.py",\n'
        '    "tests/test_market_calendar.py",\n',
        "pyright market calendar tests",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_daily_update()
    patch_data_freshness()
    patch_freshness_tests()
    patch_pyright()
    (ROOT / "scripts" / "apply_jpx_calendar_patch.py").unlink()
    (ROOT / ".github" / "workflows" / "apply-jpx-calendar.yml").unlink()


if __name__ == "__main__":
    main()
