"""Apply JPX calendar follow-up fixes, then remove temporary files."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    daily_path = ROOT / "daily_update.py"
    daily = daily_path.read_text(encoding="utf-8")
    daily = replace_once(
        daily,
        "from datetime import datetime, timedelta\n",
        "from datetime import datetime\n",
        "remove unused timedelta import",
    )
    daily_path.write_text(daily, encoding="utf-8")

    calendar_path = ROOT / "src" / "market_calendar.py"
    calendar = calendar_path.read_text(encoding="utf-8")
    calendar = replace_once(
        calendar,
        "        latest = _coerce_date(latest_date)\n"
        "        if isinstance(reference_date, datetime):\n",
        "        latest = _coerce_date(latest_date)\n"
        "        if not self.is_trading_day(latest):\n"
        "            raise ValueError(\n"
        "                \"最新データ日がJPX取引日ではありません: \"\n"
        "                f\"{latest.isoformat()}\"\n"
        "            )\n"
        "        if isinstance(reference_date, datetime):\n",
        "validate latest session",
    )
    calendar_path.write_text(calendar, encoding="utf-8")

    test_path = ROOT / "tests" / "test_market_calendar.py"
    tests = test_path.read_text(encoding="utf-8")
    marker = "\n\ndef test_unsupported_year_is_rejected_explicitly() -> None:\n"
    addition = '''\n\ndef test_missing_days_rejects_non_trading_latest_date() -> None:
    with pytest.raises(ValueError, match="JPX取引日ではありません"):
        count_missing_trading_days("2026-07-20", "2026-07-21")
'''
    tests = replace_once(
        tests,
        marker,
        addition + marker,
        "add non-trading latest-date test",
    )
    test_path.write_text(tests, encoding="utf-8")

    (ROOT / "scripts" / "apply_jpx_calendar_followup.py").unlink()
    (ROOT / ".github" / "workflows" / "apply-jpx-calendar-followup.yml").unlink()


if __name__ == "__main__":
    main()
