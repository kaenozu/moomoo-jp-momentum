"""日次サイクルの市場フィルタと対象選定を検証する。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

import run_daily_cycle
from src.config import Config
from src.data_store import DataStore


def _write_test_config(tmp_path: Path) -> Path:
    """JP/US混在ユニバースを使う最小設定を作成する。"""
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text(
        json.dumps(
            [
                {
                    "code": "JP.7203",
                    "name": "トヨタ自動車",
                    "market": "jp",
                    "role": "trade_candidate",
                },
                {
                    "code": "JP.1306",
                    "name": "TOPIX連動ETF",
                    "market": "JP",
                    "role": "benchmark",
                    "tradable": False,
                },
                {
                    "code": "US.AAPL",
                    "name": "Apple",
                    "market": "US",
                    "role": "trade_candidate",
                },
                {
                    "code": "JP.9999",
                    "name": "無効銘柄",
                    "market": "JP",
                    "enabled": False,
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
                },
                "virtual_trade": {"score_threshold_for_order": 70},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return config_path


def test_get_enabled_symbols_filters_jp_and_rejects_empty_markets(
    tmp_path: Path,
) -> None:
    """市場名の大文字小文字を吸収し、JP銘柄だけを返す。"""
    config_path = _write_test_config(tmp_path)
    config = Config(str(config_path))
    data_store = DataStore(config)
    data_store.sync_symbols_from_json()

    symbols = data_store.get_enabled_symbols(
        include_benchmarks=True,
        markets=["jp"],
    )

    assert [symbol.code for symbol in symbols] == ["JP.1306", "JP.7203"]
    with pytest.raises(ValueError, match="marketsは空にできません"):
        data_store.get_enabled_symbols(markets=[])


def test_dry_run_and_live_use_same_jp_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry-runと実運用が同じJP対象を選び、latest取得だけを使う。"""
    config_path = _write_test_config(tmp_path)

    class OpenDConnectionStub:
        def __init__(self, config: Config):
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
        requested: list[tuple[str, int]] = []

        def __init__(self, config: Config, quote_context: object):
            self.config = config
            self.quote_context = quote_context

        def get_daily_klines_latest_only(
            self,
            code: str,
            num: int = 30,
        ) -> pd.DataFrame:
            self.requested.append((code, num))
            return pd.DataFrame(
                [
                    {
                        "time_key": "2026-07-14",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 1000,
                        "turnover": 100500.0,
                    }
                ]
            )

    class FreshnessGuardStub:
        def __init__(self, config: Config):
            self.config = config

        def check_freshness(self) -> SimpleNamespace:
            return SimpleNamespace(
                level="ok",
                days_stale=0,
                message="",
            )

    class ScreenerStub:
        def __init__(self, config: Config):
            self.config = config

        def screen_candidates(self, date: str) -> list:
            return []

        def save_signals_to_db(self, candidates: list) -> int:
            return 0

    class VirtualTradeManagerStub:
        def __init__(self, config: Config):
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
        def __init__(self, config: Config):
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

    dry_run_results = run_daily_cycle.run_cycle(
        "2026-07-14",
        dry_run=True,
        config_path=str(config_path),
    )
    live_results = run_daily_cycle.run_cycle(
        "2026-07-14",
        dry_run=False,
        config_path=str(config_path),
    )

    assert dry_run_results["symbols"] == live_results["symbols"] == 2
    assert live_results["daily_bars"] == 2
    assert QuoteServiceStub.requested == [
        ("JP.1306", 30),
        ("JP.7203", 30),
    ]
