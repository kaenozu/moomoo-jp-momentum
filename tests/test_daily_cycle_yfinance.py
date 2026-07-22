"""日次サイクルのskip-fetchとyfinanceフォールバックを検証する。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

import run_daily_cycle
from src.config import Config
from src.quote_service import QuoteService


def _write_test_config(tmp_path: Path) -> Path:
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text(
        json.dumps(
            [
                {
                    "code": "JP.1306",
                    "name": "TOPIX連動ETF",
                    "market": "JP",
                    "role": "benchmark",
                    "tradable": False,
                },
                {
                    "code": "JP.7203",
                    "name": "トヨタ自動車",
                    "market": "JP",
                    "role": "trade_candidate",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "opend": {"host": "127.0.0.1", "port": 11111},
                "watchlist": {"symbols_file": str(symbols_path)},
                "database": {"path": str(tmp_path / "test.db")},
                "daily_cycle": {
                    "markets": ["JP"],
                    "fetch_mode": "latest",
                    "latest_bar_count": 30,
                    "indicator_bar_count": 120,
                },
                "virtual_trade": {"score_threshold_for_order": 70},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return config_path


class DummyConfig(Config):
    def __init__(self) -> None:
        self.config_path = "dummy.yaml"
        self._config = {}


def test_yfinance_ticker_conversion() -> None:
    assert QuoteService.to_yfinance_ticker("JP.1234") == "1234.T"
    with pytest.raises(ValueError, match="日本株コード"):
        QuoteService.to_yfinance_ticker("US.AAPL")
    with pytest.raises(ValueError, match="形式が不正"):
        QuoteService.to_yfinance_ticker("JP.ABCD")


def test_yfinance_filters_zero_and_100_percent_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = pd.to_datetime(
        ["2026-07-10", "2026-07-11", "2026-07-12", "2026-07-13"]
    )
    index.name = "Date"
    history = pd.DataFrame(
        {
            "Open": [100.0, 0.0, 109.0, 220.0],
            "High": [101.0, 0.0, 111.0, 221.0],
            "Low": [99.0, 0.0, 108.0, 219.0],
            "Close": [100.0, 0.0, 110.0, 220.0],
            "Volume": [10, 20, 30, 40],
        },
        index=index,
    )
    captured: dict[str, object] = {}

    class TickerStub:
        def __init__(self, ticker: str) -> None:
            captured["ticker"] = ticker

        def history(self, **kwargs: object) -> pd.DataFrame:
            captured.update(kwargs)
            return history

    monkeypatch.setattr("src.quote_service.yf.Ticker", TickerStub)

    service = QuoteService(DummyConfig())
    result = service.get_daily_klines_yfinance(
        "JP.1234",
        start_date="2026-07-10",
        end_date="2026-07-13",
    )

    assert captured["ticker"] == "1234.T"
    assert captured["start"] == "2026-07-10"
    assert captured["end"] == "2026-07-14"
    assert result["time_key"].tolist() == ["2026-07-10", "2026-07-12"]
    assert result["turnover"].tolist() == [1000.0, 3300.0]
    assert set(result["source"]) == {"yfinance"}
    assert set(result["turnover_source"]) == {"estimated"}


def test_skip_fetch_uses_no_api_and_stops_after_signals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_test_config(tmp_path)

    class ForbiddenApi:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("API related class must not be instantiated")

    class FreshnessGuardStub:
        def __init__(self, config: Config) -> None:
            self.config = config

        def check_freshness(self) -> SimpleNamespace:
            return SimpleNamespace(level="ok", days_stale=0, message="")

    class ScreenerStub:
        def __init__(self, config: Config) -> None:
            self.config = config

        def screen_candidates(self, date: str) -> list:
            assert date == "2026-07-10"
            return []

        def save_signals_to_db(self, candidates: list) -> int:
            return 1

    monkeypatch.setattr(run_daily_cycle, "OpenDConnection", ForbiddenApi)
    monkeypatch.setattr(run_daily_cycle, "QuoteService", ForbiddenApi)
    monkeypatch.setattr(run_daily_cycle, "VirtualTradeManager", ForbiddenApi)
    monkeypatch.setattr(run_daily_cycle, "AlertManager", ForbiddenApi)
    monkeypatch.setattr(run_daily_cycle, "DataFreshnessGuard", FreshnessGuardStub)
    monkeypatch.setattr(run_daily_cycle, "Screener", ScreenerStub)

    results = run_daily_cycle.run_cycle(
        "2026-07-10",
        config_path=str(config_path),
        skip_fetch=True,
    )

    assert results["connection"] is False
    assert results["daily_bars"] == 0
    assert results["signals"] == 1
    assert results["virtual_orders"] == 0
    assert results["fills"] == 0
    assert results["alerts"] == 0


def test_auto_provider_falls_back_to_yfinance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_test_config(tmp_path)

    class OpenDConnectionStub:
        def __init__(self, config: Config) -> None:
            self.config = config

        def connect(self) -> SimpleNamespace:
            return SimpleNamespace(
                connected=True,
                message="",
                quote_context=object(),
            )

        def disconnect(self) -> None:
            return None

    class QuoteServiceStub:
        moomoo_codes: list[str] = []
        yfinance_codes: list[tuple[str, str, str]] = []

        def __init__(self, config: Config, quote_context: object) -> None:
            self.config = config
            self.quote_context = quote_context

        def get_daily_klines_latest_only(
            self,
            code: str,
            num: int = 30,
        ) -> pd.DataFrame:
            self.moomoo_codes.append(code)
            return pd.DataFrame()

        def get_daily_klines_yfinance(
            self,
            code: str,
            start_date: str,
            end_date: str,
        ) -> pd.DataFrame:
            self.yfinance_codes.append((code, start_date, end_date))
            return pd.DataFrame(
                [
                    {
                        "time_key": "2026-07-10",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 1000,
                        "turnover": 100500.0,
                        "source": "yfinance",
                        "turnover_source": "estimated",
                    }
                ]
            )

    class FreshnessGuardStub:
        def __init__(self, config: Config) -> None:
            self.config = config

        def check_freshness(self) -> SimpleNamespace:
            return SimpleNamespace(level="ok", days_stale=0, message="")

    class ScreenerStub:
        def __init__(self, config: Config) -> None:
            self.config = config

        def screen_candidates(self, date: str) -> list:
            return []

        def save_signals_to_db(self, candidates: list) -> int:
            return 0

    class VirtualTradeManagerStub:
        def __init__(self, config: Config) -> None:
            self.config = config

        def get_cash(self, strategy_name: str) -> float:
            return 100000.0

        def process_fills(self, strategy_name: str, target_date: str) -> list:
            return []

        def generate_exits(self, strategy_name: str, target_date: str) -> list:
            return []

        def update_market_prices(
            self,
            strategy_name: str,
            target_date: str,
        ) -> int:
            return 0

        def save_equity_curve(
            self,
            strategy_name: str,
            target_date: str,
        ) -> None:
            return None

    class AlertManagerStub:
        def __init__(self, config: Config) -> None:
            self.config = config

        def run_all_checks(self) -> list:
            return []

    monkeypatch.setattr(run_daily_cycle, "OpenDConnection", OpenDConnectionStub)
    monkeypatch.setattr(run_daily_cycle, "QuoteService", QuoteServiceStub)
    monkeypatch.setattr(run_daily_cycle, "DataFreshnessGuard", FreshnessGuardStub)
    monkeypatch.setattr(run_daily_cycle, "Screener", ScreenerStub)
    monkeypatch.setattr(
        run_daily_cycle,
        "VirtualTradeManager",
        VirtualTradeManagerStub,
    )
    monkeypatch.setattr(run_daily_cycle, "AlertManager", AlertManagerStub)
    monkeypatch.setattr(
        run_daily_cycle,
        "calculate_indicators_batch",
        lambda data_dict, symbols_info: [],
    )
    monkeypatch.setattr(
        run_daily_cycle,
        "indicators_to_dataframe",
        lambda indicators: pd.DataFrame(),
    )
    monkeypatch.setattr(
        run_daily_cycle,
        "add_relative_strength",
        lambda indicators_df, benchmark_code: indicators_df,
    )
    monkeypatch.setattr(
        run_daily_cycle,
        "save_indicators_to_db",
        lambda data_store, indicators_df: 0,
    )
    monkeypatch.setattr(
        run_daily_cycle,
        "save_benchmark_prices_from_indicators",
        lambda data_store, indicators_df, benchmark_codes: 0,
    )

    results = run_daily_cycle.run_cycle(
        "2026-07-10",
        config_path=str(config_path),
        provider="auto",
    )

    assert results["daily_bars"] == 2
    assert results["moomoo_codes"] == 0
    assert results["yfinance_codes"] == 2
    assert results["fallback_codes"] == 2
    assert QuoteServiceStub.moomoo_codes == ["JP.1306", "JP.7203"]
    assert [item[0] for item in QuoteServiceStub.yfinance_codes] == [
        "JP.1306",
        "JP.7203",
    ]
    assert all(item[2] == "2026-07-10" for item in QuoteServiceStub.yfinance_codes)
