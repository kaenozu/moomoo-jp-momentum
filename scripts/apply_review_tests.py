"""Add and align regression tests for the full-source review fixes."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


path = ROOT / "tests/test_daily_cycle.py"
if path.exists():
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '        assert results.get("connection") is True\n'
        '        assert results.get("symbols", 0) > 0\n',
        '        assert results.get("connection_attempted") is False\n'
        '        assert results.get("database_write_attempted") is False\n'
        '        assert results.get("symbols", 0) > 0\n',
    )
    path.write_text(text, encoding="utf-8")

path = ROOT / "tests/test_daily_cycle_market_filter.py"
if path.exists():
    text = path.read_text(encoding="utf-8")
    marker = '''        def get_cash(self, strategy_name: str) -> float:
            return 100000.0
'''
    addition = '''        def get_cash(self, strategy_name: str) -> float:
            return 100000.0

        def get_available_cash(
            self, strategy_name: str, as_of_date: str | None = None
        ) -> float:
            return 100000.0
'''
    if marker in text and "def get_available_cash" not in text:
        text = text.replace(marker, addition, 1)
    path.write_text(text, encoding="utf-8")

write(
    "tests/test_full_review_regressions.py",
    '''"""Regression coverage for full-source review findings."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import yaml

from src.config import Config
from src.data_store import DataStore
from src.quote_service import QuoteService
from src.strategies import StrategyRegistry
from src.strategies.etf_rotation import ETFRotationStrategy
from src.virtual_report import VirtualReportGenerator
from src.virtual_trade import VirtualTradeManager


def make_config(tmp_path: Path, cash: float = 1000.0) -> Config:
    db_path = tmp_path / "test.db"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text("[]", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "database": {"path": str(db_path)},
                "watchlist": {"symbols_file": str(symbols_path)},
                "virtual_trade": {
                    "enabled": True,
                    "initial_cash": cash,
                    "max_position_amount": cash,
                    "max_total_positions": 5,
                    "max_position_per_symbol": 1,
                    "commission": 0,
                    "slippage_bps": 0,
                },
                "universe": {
                    "min_trade_price": 1,
                    "max_trade_price": 100000,
                },
                "strategies": {
                    "etf_rotation": {
                        "codes": ["JP.2559", "JP.1306"],
                    }
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return Config(str(config_path))


def seed_symbol_and_bar(store: DataStore, code: str, close: float) -> None:
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO symbols(code,name,market,type,role,tradable) "
            "VALUES(?,?,?,?,?,1)",
            (code, code, "JP", "stock", "trade_candidate"),
        )
        conn.execute(
            "INSERT INTO daily_bars(code,date,open,high,low,close,volume,turnover) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (code, "2026-07-01", close, close, close, close, 1000, close * 1000),
        )


def test_strategy_registry_is_populated_without_side_effect_imports(
    tmp_path: Path,
) -> None:
    assert {"momentum", "quality_low_risk", "etf_rotation"}.issubset(
        set(StrategyRegistry.list_names())
    )
    strategy = ETFRotationStrategy(make_config(tmp_path))
    assert strategy._is_etf("JP.2559") is True
    assert strategy._is_etf("JP.2501") is False


def test_pending_buys_reserve_cash(tmp_path: Path) -> None:
    config = make_config(tmp_path, cash=1000)
    store = DataStore(config)
    seed_symbol_and_bar(store, "JP.1111", 700)
    seed_symbol_and_bar(store, "JP.2222", 700)
    manager = VirtualTradeManager(config)
    assert manager.place_order(
        "default", "JP.1111", "BUY", 1, submitted_at="2026-07-01"
    )
    assert manager.get_available_cash("default", "2026-07-01") == 300
    assert manager.place_order(
        "default", "JP.2222", "BUY", 1, submitted_at="2026-07-01"
    ) is None


def test_closed_trades_are_paired_per_symbol(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = DataStore(config)
    with sqlite3.connect(store.db_path) as conn:
        for code in ("JP.A", "JP.B"):
            conn.execute(
                "INSERT INTO symbols(code,name,market,type,role,tradable) "
                "VALUES(?,?,?,?,?,1)",
                (code, code, "JP", "stock", "trade_candidate"),
            )
        orders = [
            (1, "JP.A", "BUY", None),
            (2, "JP.B", "BUY", None),
            (3, "JP.B", "SELL", "stop_loss"),
        ]
        for order_id, code, side, reason in orders:
            conn.execute(
                "INSERT INTO virtual_orders("
                "id,strategy_name,code,side,quantity,order_type,status,"
                "exit_reason,submitted_at) "
                "VALUES(?, 'default', ?, ?, 1, 'MARKET_SIM', 'FILLED', ?, "
                "'2026-07-01')",
                (order_id, code, side, reason),
            )
        fills = [
            (1, 1, "JP.A", "BUY", 100.0, "2026-07-01"),
            (2, 2, "JP.B", "BUY", 200.0, "2026-07-01"),
            (3, 3, "JP.B", "SELL", 220.0, "2026-07-02"),
        ]
        for fill_id, order_id, code, side, price, date in fills:
            conn.execute(
                "INSERT INTO virtual_fills("
                "id,order_id,strategy_name,code,side,quantity,price,filled_at) "
                "VALUES(?, ?, 'default', ?, ?, 1, ?, ?)",
                (fill_id, order_id, code, side, price, date),
            )
    trades = VirtualReportGenerator(config).get_closed_trades("default")
    assert len(trades) == 1
    assert trades[0].code == "JP.B"
    assert trades[0].entry_price == 200.0
    assert trades[0].realized_pl == 20.0


def test_history_pagination_forwards_page_key(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    context = MagicMock()
    first = pd.DataFrame({"time_key": ["2026-07-01"], "close": [100.0]})
    second = pd.DataFrame({"time_key": ["2026-07-02"], "close": [101.0]})
    context.request_history_kline.side_effect = [
        (0, first, "next-key"),
        (0, second, None),
    ]
    service = QuoteService(config, context)
    result = service.get_daily_klines("JP.1306", num=2)
    assert result["time_key"].tolist() == ["2026-07-01", "2026-07-02"]
    assert (
        context.request_history_kline.call_args_list[1]
        .kwargs["page_req_key"]
        == "next-key"
    )
''',
)

print("review regression tests applied")
