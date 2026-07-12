"""
データモデル定義

ファイルパス: src/models.py
何をするか: SQLiteテーブル定義とデータクラスの定義
なぜ存在するか: データベーススキーマとデータ構造を一元管理するため
関連ファイル: data_store.py, config.py
"""

from dataclasses import dataclass
from typing import Optional


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS symbols (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'JP',
    type TEXT NOT NULL DEFAULT 'stock',
    role TEXT NOT NULL DEFAULT 'trade_candidate',
    tradable INTEGER NOT NULL DEFAULT 1,
    sector TEXT,
    benchmark_group TEXT,
    notes TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    price REAL,
    open REAL,
    high REAL,
    low REAL,
    volume INTEGER,
    turnover REAL,
    FOREIGN KEY (code) REFERENCES symbols(code)
);

CREATE TABLE IF NOT EXISTS daily_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    turnover REAL,
    source TEXT NOT NULL DEFAULT 'moomoo',
    turnover_source TEXT NOT NULL DEFAULT 'actual',
    FOREIGN KEY (code) REFERENCES symbols(code),
    UNIQUE(code, date)
);

CREATE TABLE IF NOT EXISTS intraday_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    ktype TEXT NOT NULL DEFAULT 'K_1M',
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    turnover REAL,
    FOREIGN KEY (code) REFERENCES symbols(code),
    UNIQUE(code, timestamp, ktype)
);

CREATE TABLE IF NOT EXISTS indicators (
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    close REAL,
    volume INTEGER,
    turnover REAL,
    daily_return REAL,
    ma5 REAL,
    ma25 REAL,
    high_20d REAL,
    distance_from_high_20d REAL,
    volume_ma20 REAL,
    volume_ratio REAL,
    return_5d REAL,
    return_20d REAL,
    return_60d REAL,
    history_days INTEGER,
    return_5d_vs_benchmark REAL,
    return_20d_vs_benchmark REAL,
    return_60d_vs_benchmark REAL,
    relative_strength_rank INTEGER,
    volume_ratio_percentile REAL,
    volume_ratio_rank INTEGER,
    relative_volume_ratio REAL,
    market_median_volume_ratio REAL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    strategy_name TEXT NOT NULL DEFAULT 'momentum',
    score REAL,
    reason TEXT,
    risk_warnings TEXT,
    price_at_signal REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(strategy_name, code, date)
);

CREATE TABLE IF NOT EXISTS trades_manual (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    executed_at TEXT NOT NULL,
    reason TEXT,
    exit_rule TEXT,
    memo TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (code) REFERENCES symbols(code)
);

CREATE TABLE IF NOT EXISTS benchmark_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark_code TEXT NOT NULL,
    date TEXT NOT NULL,
    close REAL,
    daily_return REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(benchmark_code, date)
);

CREATE TABLE IF NOT EXISTS signal_backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    code TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    signal_price REAL,
    future_price REAL,
    stock_return REAL,
    benchmark_code TEXT,
    benchmark_return REAL,
    excess_return REAL,
    max_drawdown REAL,
    max_runup REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (signal_id) REFERENCES signals(id),
    FOREIGN KEY (code) REFERENCES symbols(code)
);

CREATE TABLE IF NOT EXISTS performance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    portfolio_value REAL,
    benchmark_value REAL,
    cash REAL,
    memo TEXT
);

CREATE TABLE IF NOT EXISTS alert_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    message TEXT,
    sent_to TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(code, date, alert_type)
);

CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    code TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    order_type TEXT NOT NULL,
    status TEXT NOT NULL,
    trd_env TEXT NOT NULL DEFAULT 'SIMULATE',
    submitted_at TEXT,
    updated_at TEXT,
    raw_response TEXT
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    cost_price REAL NOT NULL,
    market_value REAL,
    unrealized_pl REAL,
    trd_env TEXT NOT NULL DEFAULT 'SIMULATE',
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(code, trd_env)
);

CREATE TABLE IF NOT EXISTS paper_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    code TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    filled_at TEXT NOT NULL,
    trd_env TEXT NOT NULL DEFAULT 'SIMULATE',
    raw_response TEXT
);

CREATE TABLE IF NOT EXISTS virtual_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    code TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    order_type TEXT NOT NULL,
    limit_price REAL,
    status TEXT NOT NULL,
    signal_id INTEGER,
    exit_reason TEXT,
    submitted_at TEXT NOT NULL,
    filled_at TEXT,
    cancelled_at TEXT,
    fill_price REAL,
    fill_reason TEXT,
    reserved_amount REAL,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS virtual_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    code TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    avg_cost REAL NOT NULL,
    market_price REAL,
    market_value REAL,
    unrealized_pl REAL,
    realized_pl REAL DEFAULT 0,
    updated_at TEXT,
    UNIQUE(strategy_name, code)
);

CREATE TABLE IF NOT EXISTS virtual_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE,
    strategy_name TEXT NOT NULL,
    code TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    filled_at TEXT NOT NULL,
    fill_mode TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS virtual_equity_curve (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    date TEXT NOT NULL,
    cash REAL,
    position_value REAL,
    total_equity REAL,
    daily_return REAL,
    benchmark_code TEXT,
    benchmark_return REAL,
    excess_return REAL,
    created_at TEXT,
    UNIQUE(strategy_name, date)
);

CREATE INDEX IF NOT EXISTS idx_quotes_code ON quotes(code);
CREATE INDEX IF NOT EXISTS idx_quotes_timestamp ON quotes(timestamp);
CREATE INDEX IF NOT EXISTS idx_daily_bars_code ON daily_bars(code);
CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(date);
CREATE INDEX IF NOT EXISTS idx_indicators_code ON indicators(code);
CREATE INDEX IF NOT EXISTS idx_indicators_date ON indicators(date);
CREATE INDEX IF NOT EXISTS idx_signals_code ON signals(code);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date);
CREATE INDEX IF NOT EXISTS idx_trades_manual_code ON trades_manual(code);
CREATE INDEX IF NOT EXISTS idx_benchmark_prices_code ON benchmark_prices(benchmark_code);
CREATE INDEX IF NOT EXISTS idx_benchmark_prices_date ON benchmark_prices(date);
CREATE INDEX IF NOT EXISTS idx_signal_backtests_code ON signal_backtests(code);
CREATE UNIQUE INDEX IF NOT EXISTS idx_virtual_orders_pending ON virtual_orders(strategy_name, code, side, COALESCE(substr(submitted_at, 1, 10), '')) WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS idx_virtual_fills_order ON virtual_fills(order_id);

-- バックテスト管理
CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_name TEXT,
    strategy_name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    initial_cash REAL NOT NULL DEFAULT 100000,
    final_equity REAL,
    total_return_pct REAL,
    max_drawdown_pct REAL,
    win_rate REAL,
    profit_factor REAL,
    trade_count INTEGER,
    benchmark_2559_return REAL,
    excess_vs_2559 REAL,
    benchmark_1306_return REAL,
    excess_vs_1306 REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS backtest_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    code TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    order_type TEXT NOT NULL,
    limit_price REAL,
    status TEXT NOT NULL,
    signal_date TEXT,
    submitted_at TEXT,
    filled_at TEXT,
    fill_price REAL,
    exit_reason TEXT,
    FOREIGN KEY (run_id) REFERENCES backtest_runs(id)
);

CREATE TABLE IF NOT EXISTS backtest_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    code TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    filled_at TEXT NOT NULL,
    fill_mode TEXT,
    FOREIGN KEY (run_id) REFERENCES backtest_runs(id)
);

CREATE TABLE IF NOT EXISTS backtest_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    code TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    avg_cost REAL NOT NULL,
    market_price REAL,
    market_value REAL,
    unrealized_pl REAL,
    realized_pl REAL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES backtest_runs(id),
    UNIQUE(run_id, strategy_name, code)
);

CREATE TABLE IF NOT EXISTS backtest_equity_curve (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    date TEXT NOT NULL,
    cash REAL,
    position_value REAL,
    total_equity REAL,
    benchmark_2559_value REAL,
    benchmark_1306_value REAL,
    drawdown_pct REAL,
    UNIQUE(run_id, strategy_name, date)
);

CREATE INDEX IF NOT EXISTS idx_backtest_orders_run ON backtest_orders(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_fills_run ON backtest_fills(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_positions_run ON backtest_positions(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_equity_run ON backtest_equity_curve(run_id);
"""


@dataclass
class Symbol:
    """銘柄情報"""
    code: str
    name: str
    market: str = "JP"
    type: str = "stock"
    role: str = "trade_candidate"
    tradable: bool = True
    sector: Optional[str] = None
    benchmark_group: Optional[str] = None
    notes: Optional[str] = None
    enabled: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Quote:
    code: str
    timestamp: str
    price: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[int] = None
    turnover: Optional[float] = None


@dataclass
class DailyBar:
    code: str
    date: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    turnover: Optional[float] = None
    source: str = "moomoo"
    turnover_source: str = "actual"


@dataclass
class Indicator:
    code: str
    date: str
    close: Optional[float] = None
    daily_return: Optional[float] = None
    ma5: Optional[float] = None
    ma25: Optional[float] = None
    high_20d: Optional[float] = None
    distance_from_high_20d: Optional[float] = None
    volume_ma20: Optional[float] = None
    volume_ratio: Optional[float] = None
    return_5d: Optional[float] = None
    return_5d_vs_benchmark: Optional[float] = None
    return_20d_vs_benchmark: Optional[float] = None
    return_60d_vs_benchmark: Optional[float] = None
    turnover: Optional[float] = None
    updated_at: Optional[str] = None


@dataclass
class Signal:
    code: str
    date: str
    signal_type: str
    score: Optional[float] = None
    reason: Optional[str] = None
    risk_warnings: Optional[str] = None
    price_at_signal: Optional[float] = None
    created_at: Optional[str] = None


@dataclass
class TradeManual:
    code: str
    side: str
    quantity: int
    price: float
    executed_at: str
    reason: Optional[str] = None
    exit_rule: Optional[str] = None
    memo: Optional[str] = None


@dataclass
class BenchmarkPrice:
    benchmark_code: str
    date: str
    price: Optional[float] = None


@dataclass
class PerformanceSnapshot:
    date: str
    portfolio_value: Optional[float] = None
    benchmark_value: Optional[float] = None
    cash: Optional[float] = None
    memo: Optional[str] = None
