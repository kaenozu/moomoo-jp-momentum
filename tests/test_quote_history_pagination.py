"""Regression tests for Futu request_history_kline pagination."""

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
