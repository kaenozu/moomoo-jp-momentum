from __future__ import annotations

import json
import re
from pathlib import Path


QUOTE_SERVICE = Path("src/quote_service.py")
text = QUOTE_SERVICE.read_text(encoding="utf-8")

helper = '''
    def _request_history_kline_pages(
        self,
        code: str,
        num: int,
        start: Optional[str],
        end: Optional[str],
        log_label: str,
    ) -> pd.DataFrame:
        """Fetch all requested history pages using Futu continuation keys."""
        if num <= 0:
            return pd.DataFrame()

        pages: list[pd.DataFrame] = []
        remaining = num
        page_req_key = None
        seen_page_keys: set[str] = set()
        page_number = 1

        while remaining > 0:
            batch_size = min(remaining, MAX_KLINE_PER_REQUEST)
            ret, data, next_page_req_key = self.ctx.request_history_kline(
                code,
                ktype=KLType.K_DAY,
                max_count=batch_size,
                start=start,
                end=end,
                page_req_key=page_req_key,
            )

            if ret != RET_OK:
                logger.error(
                    "日足取得失敗%s: %s - page=%s - %s",
                    log_label,
                    code,
                    page_number,
                    data,
                )
                return pd.DataFrame()

            if not isinstance(data, pd.DataFrame):
                logger.error(
                    "日足取得失敗%s: %s - page=%s - DataFrameではありません",
                    log_label,
                    code,
                    page_number,
                )
                return pd.DataFrame()

            if data.empty:
                if next_page_req_key is not None:
                    logger.error(
                        "日足取得失敗%s: %s - page=%s - "
                        "空ページに継続キーが返されました",
                        log_label,
                        code,
                        page_number,
                    )
                    return pd.DataFrame()
                break

            pages.append(data)
            remaining -= len(data)

            if remaining <= 0 or next_page_req_key is None:
                break

            key_marker = repr(next_page_req_key)
            if key_marker in seen_page_keys:
                logger.error(
                    "日足取得失敗%s: %s - page=%s - 継続キーが循環しました",
                    log_label,
                    code,
                    page_number,
                )
                return pd.DataFrame()

            seen_page_keys.add(key_marker)
            page_req_key = next_page_req_key
            page_number += 1

        if not pages:
            return pd.DataFrame()

        combined = pd.concat(pages, ignore_index=True)
        if "time_key" in combined.columns:
            combined = combined.drop_duplicates(subset=["time_key"], keep="first")
        else:
            combined = combined.drop_duplicates(keep="first")

        return combined.iloc[:num].reset_index(drop=True)

'''

anchor = "    def get_stock_snapshot(self, codes: list[str]) -> pd.DataFrame:\n"
if helper.strip() not in text:
    if text.count(anchor) != 1:
        raise RuntimeError("QuoteService helper insertion anchor not found exactly once")
    text = text.replace(anchor, helper + anchor, 1)

get_daily = '''    def get_daily_klines(
        self,
        code: str,
        num: int = 120,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """日足ローソク足をFutuの継続キーを辿って取得する。"""
        logger.info("日足取得: %s (num=%s)", code, num)
        data = self._request_history_kline_pages(
            code,
            num,
            start,
            end,
            "",
        )
        logger.info("日足取得完了: %s - %s件", code, len(data))
        return data

'''
text, count = re.subn(
    r"    def get_daily_klines\(.*?\n    def get_cur_daily_klines",
    get_daily + "    def get_cur_daily_klines",
    text,
    count=1,
    flags=re.DOTALL,
)
if count != 1:
    raise RuntimeError(f"get_daily_klines replacement count={count}")

history_only = '''    def get_daily_klines_history_only(
        self,
        code: str,
        num: int = 120,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """購読枠を消費せず、Futuの継続キーを辿って日足を取得する。"""
        logger.info(
            "日足取得(history): %s (num=%s, start=%s, end=%s)",
            code,
            num,
            start,
            end,
        )
        data = self._request_history_kline_pages(
            code,
            num,
            start,
            end,
            "(history)",
        )
        logger.info("日足取得完了(history): %s - %s件", code, len(data))
        return data

'''
text, count = re.subn(
    r"    def get_daily_klines_history_only\(.*?\n    def get_daily_klines_latest_only",
    history_only + "    def get_daily_klines_latest_only",
    text,
    count=1,
    flags=re.DOTALL,
)
if count != 1:
    raise RuntimeError(f"history-only replacement count={count}")

QUOTE_SERVICE.write_text(text, encoding="utf-8")


tests = '''"""Regression tests for Futu request_history_kline pagination."""

from collections.abc import Callable
from typing import Any

import pandas as pd
import pytest

from src.quote_service import QuoteService


class DummyConfig:
    def get(self, _key_path: str, default: Any = None) -> Any:
        return default


class SequencedHistoryContext:
    def __init__(self, responses: list[tuple[int, object, object]]) -> None:
        self.responses = iter(responses)
        self.calls: list[dict[str, object]] = []

    def request_history_kline(self, code: str, **kwargs: object):
        self.calls.append({"code": code, **kwargs})
        return next(self.responses)


def _page(start: str, periods: int) -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=periods, freq="D")
    return pd.DataFrame(
        {
            "time_key": dates.strftime("%Y-%m-%d"),
            "close": range(periods),
        }
    )


def _service(
    responses: list[tuple[int, object, object]],
) -> tuple[QuoteService, SequencedHistoryContext]:
    context = SequencedHistoryContext(responses)
    service = QuoteService(DummyConfig(), context)  # type: ignore[arg-type]
    return service, context


@pytest.mark.parametrize(
    "method_name",
    ["get_daily_klines", "get_daily_klines_history_only"],
)
def test_history_methods_forward_page_req_key(method_name: str) -> None:
    first = _page("2020-01-01", 1000)
    second = _page("2022-09-27", 500)
    service, context = _service(
        [
            (0, first, b"page-2"),
            (0, second, None),
        ]
    )

    method = getattr(service, method_name)
    result = method(
        "JP.7203",
        num=1500,
        start="2020-01-01",
        end="2026-01-01",
    )

    assert len(result) == 1500
    assert len(context.calls) == 2
    assert context.calls[0]["page_req_key"] is None
    assert context.calls[1]["page_req_key"] == b"page-2"
    assert context.calls[0]["start"] == context.calls[1]["start"] == "2020-01-01"
    assert context.calls[0]["end"] == context.calls[1]["end"] == "2026-01-01"
    assert context.calls[0]["max_count"] == 1000
    assert context.calls[1]["max_count"] == 500


def test_short_page_with_continuation_fetches_next_page() -> None:
    first = _page("2020-01-01", 600)
    second = _page("2021-08-23", 400)
    service, context = _service(
        [
            (0, first, "continue"),
            (0, second, None),
        ]
    )

    result = service.get_daily_klines_history_only("JP.1306", num=1000)

    assert len(result) == 1000
    assert len(context.calls) == 2
    assert context.calls[1]["page_req_key"] == "continue"
    assert context.calls[1]["max_count"] == 400


def test_later_page_failure_discards_partial_history() -> None:
    service, context = _service(
        [
            (0, _page("2020-01-01", 1000), "continue"),
            (1, "temporary failure", None),
        ]
    )

    result = service.get_daily_klines_history_only("JP.1306", num=1500)

    assert result.empty
    assert len(context.calls) == 2


def test_repeated_continuation_key_fails_without_infinite_loop() -> None:
    service, context = _service(
        [
            (0, _page("2020-01-01", 1000), "same-key"),
            (0, _page("2022-09-27", 300), "same-key"),
        ]
    )

    result = service.get_daily_klines_history_only("JP.1306", num=1500)

    assert result.empty
    assert len(context.calls) == 2


def test_empty_page_with_continuation_key_is_rejected() -> None:
    service, context = _service([(0, pd.DataFrame(), "unexpected-key")])

    result = service.get_daily_klines_history_only("JP.1306", num=100)

    assert result.empty
    assert len(context.calls) == 1
'''
Path("tests/test_quote_history_pagination.py").write_text(tests, encoding="utf-8")

pyright_path = Path("pyrightconfig.json")
pyright = json.loads(pyright_path.read_text(encoding="utf-8"))
test_path = "tests/test_quote_history_pagination.py"
if test_path not in pyright["include"]:
    insert_at = pyright["include"].index("tests/test_regressions.py") + 1
    pyright["include"].insert(insert_at, test_path)
pyright_path.write_text(
    json.dumps(pyright, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
