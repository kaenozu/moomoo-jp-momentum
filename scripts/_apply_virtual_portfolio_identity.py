from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{relative}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def replace_all(relative: str, old: str, new: str, *, minimum: int = 1) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count < minimum:
        raise RuntimeError(f"{relative}: expected at least {minimum} matches, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


def write_new(relative: str, content: str) -> None:
    path = ROOT / relative
    if path.exists():
        raise RuntimeError(f"refusing to overwrite new file: {relative}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


write_new(
    "src/trading_identity.py",
    '''"""Resolve signal-algorithm and virtual-portfolio identifiers from config."""

from __future__ import annotations

from typing import Any, Protocol

DEFAULT_SIGNAL_STRATEGY = "momentum"
DEFAULT_VIRTUAL_PORTFOLIO = "default"


class ConfigLike(Protocol):
    def get(self, key_path: str, default: Any = None) -> Any: ...


def _read_identifier(config: ConfigLike, key: str, default: str) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key}は空でない文字列で指定してください")
    identifier = value.strip()
    if any(character.isspace() for character in identifier):
        raise ValueError(f"{key}に空白は使用できません: {identifier!r}")
    return identifier


def signal_strategy_name(config: ConfigLike) -> str:
    """Return the signal-generation algorithm identifier."""
    return _read_identifier(
        config,
        "signals.strategy_name",
        DEFAULT_SIGNAL_STRATEGY,
    )


def virtual_portfolio_name(config: ConfigLike) -> str:
    """Return the SQLite virtual-trading portfolio identifier."""
    return _read_identifier(
        config,
        "virtual_trade.portfolio_name",
        DEFAULT_VIRTUAL_PORTFOLIO,
    )
''',
)

# Signal strategy identity.
replace_once(
    "src/signals.py",
    "from .indicators import StockIndicators\n",
    "from .indicators import StockIndicators\nfrom .trading_identity import signal_strategy_name\n",
)
replace_once(
    "src/signals.py",
    '''    def __init__(self, config: Config):
        self.config = config
        self.screening_config = config.get("screening", {})
''',
    '''    def __init__(self, config: Config):
        self.config = config
        self.strategy_name = signal_strategy_name(config)
        self.screening_config = config.get("screening", {})
''',
)
replace_once(
    "src/signals.py",
    '''            date=indicators.date,
            signal_type="EXCLUDE",
            price_at_signal=indicators.close,
''',
    '''            date=indicators.date,
            signal_type="EXCLUDE",
            strategy_name=self.strategy_name,
            price_at_signal=indicators.close,
''',
)

# Daily cycle always uses the configured virtual portfolio.
replace_once(
    "run_daily_cycle.py",
    "from src.virtual_trade_integrity import IntegrityReport, VirtualTradeIntegrityChecker\n",
    "from src.virtual_trade_integrity import IntegrityReport, VirtualTradeIntegrityChecker\nfrom src.trading_identity import virtual_portfolio_name\n",
)
replace_once(
    "run_daily_cycle.py",
    '''    config = load_config(config_path)
    virtual_trade_enabled = _read_bool_setting(
''',
    '''    config = load_config(config_path)
    portfolio_name = virtual_portfolio_name(config)
    results["virtual_portfolio"] = portfolio_name
    virtual_trade_enabled = _read_bool_setting(
''',
)
replace_once(
    "run_daily_cycle.py",
    '''            order = manager.place_order(
                strategy_name="default",
                code=candidate.code,
''',
    '''            order = manager.place_order(
                strategy_name=portfolio_name,
                code=candidate.code,
''',
)
replace_once(
    "run_daily_cycle.py",
    '''        manager = VirtualTradeManager(config)
        fills = manager.process_fills("default", target_date)
        results["fills"] = len(fills)
        exits = manager.generate_exits("default", target_date)
        results["exits"] = len(exits)
        results["price_updates"] = manager.update_market_prices(
            "default",
            target_date,
        )
        manager.save_equity_curve("default", target_date)

        if integrity_enabled:
            integrity_report = _run_virtual_trade_integrity_gate(
                config,
                "default",
                target_date,
''',
    '''        manager = VirtualTradeManager(config)
        fills = manager.process_fills(portfolio_name, target_date)
        results["fills"] = len(fills)
        exits = manager.generate_exits(portfolio_name, target_date)
        results["exits"] = len(exits)
        results["price_updates"] = manager.update_market_prices(
            portfolio_name,
            target_date,
        )
        manager.save_equity_curve(portfolio_name, target_date)

        if integrity_enabled:
            integrity_report = _run_virtual_trade_integrity_gate(
                config,
                portfolio_name,
                target_date,
''',
)

# Virtual-order CLI separates source signal strategy from destination portfolio.
replace_once(
    "virtual_order.py",
    "from src.virtual_trade import VirtualTradeManager\n",
    "from src.virtual_trade import VirtualTradeManager\nfrom src.trading_identity import signal_strategy_name, virtual_portfolio_name\n",
)
replace_once(
    "virtual_order.py",
    '''def from_signals(manager: VirtualTradeManager, config, date: str, strategy: str):
    from src.data_store import DataStore
    DataStore(config).sync_symbols_from_json(config.watchlist_file)

    screener = Screener(config)
    candidates = screener.screen_candidates(date=None if date == "latest" else date)
    vt_config = config.get("virtual_trade", {})
    score_threshold = vt_config.get("score_threshold_for_order", 70)
    cash = manager.get_cash(strategy)
    created = 0

    for c in candidates:
        if c.signal_type != "BUY_CANDIDATE":
            continue
        if c.strategy_name != strategy:
            continue
        if c.role != "trade_candidate" or not c.tradable:
            continue
        if c.score < score_threshold:
            continue
        if c.close and c.close > cash:
            continue
        order = manager.place_order(
            strategy_name=strategy,
            code=c.code,
            side="BUY",
            quantity=1,
            order_type="MARKET_SIM",
            submitted_at=c.date,
        )
        if order:
            created += 1
            cash -= c.close or 0

    print(f"戦略 {strategy}: {created} 件の仮想注文を作成しました")
''',
    '''def from_signals(
    manager: VirtualTradeManager,
    config,
    date: str,
    portfolio: str,
    signal_strategy: str,
):
    from src.data_store import DataStore
    DataStore(config).sync_symbols_from_json(config.watchlist_file)

    screener = Screener(config)
    candidates = screener.screen_candidates(date=None if date == "latest" else date)
    vt_config = config.get("virtual_trade", {})
    score_threshold = vt_config.get("score_threshold_for_order", 70)
    cash = manager.get_cash(portfolio)
    created = 0

    for c in candidates:
        if c.signal_type != "BUY_CANDIDATE":
            continue
        if c.strategy_name != signal_strategy:
            continue
        if c.role != "trade_candidate" or not c.tradable:
            continue
        if c.score < score_threshold:
            continue
        if c.close and c.close > cash:
            continue
        order = manager.place_order(
            strategy_name=portfolio,
            code=c.code,
            side="BUY",
            quantity=1,
            order_type="MARKET_SIM",
            submitted_at=c.date,
        )
        if order:
            created += 1
            cash -= c.close or 0

    print(
        f"シグナル戦略 {signal_strategy} → 仮想portfolio {portfolio}: "
        f"{created} 件の仮想注文を作成しました"
    )
''',
)
replace_once(
    "virtual_order.py",
    '    parser.add_argument("--strategy", default="default", help="戦術名")\n',
    '''    parser.add_argument(
        "--portfolio",
        "--strategy",
        dest="portfolio",
        default=None,
        help="仮想取引portfolio名（--strategyは互換alias）",
    )
    parser.add_argument(
        "--signal-strategy",
        default=None,
        help="シグナル生成戦略名",
    )
''',
)
replace_once(
    "virtual_order.py",
    '''    manager = VirtualTradeManager(config)

    if args.list:
''',
    '''    portfolio = args.portfolio or virtual_portfolio_name(config)
    signal_strategy = args.signal_strategy or signal_strategy_name(config)
    manager = VirtualTradeManager(config)

    if args.list:
''',
)
replace_all("virtual_order.py", "args.strategy", "portfolio", minimum=8)
replace_once(
    "virtual_order.py",
    '''    if args.from_signals:
        from_signals(manager, config, args.date or "latest", portfolio)
        return 0
''',
    '''    if args.from_signals:
        from_signals(
            manager,
            config,
            args.date or "latest",
            portfolio,
            signal_strategy,
        )
        return 0
''',
)

# Streamlit virtual-trade views use the configured portfolio explicitly.
replace_once(
    "app.py",
    "from src.trade_log import TradeLog\n",
    "from src.trade_log import TradeLog\nfrom src.trading_identity import virtual_portfolio_name\n",
)
replace_once(
    "app.py",
    '''    st.header("仮想トレード")
    st.warning("これはアプリ内の仮想注文です。moomooには注文を送信しません。")
    manager = VirtualTradeManager(load_config_cached())
''',
    '''    st.header("仮想トレード")
    st.warning("これはアプリ内の仮想注文です。moomooには注文を送信しません。")
    config = load_config_cached()
    portfolio = virtual_portfolio_name(config)
    st.caption(f"仮想portfolio: {portfolio}")
    manager = VirtualTradeManager(config)
''',
)
replace_once(
    "app.py",
    '''        positions = manager.get_positions()
''',
    '''        positions = manager.get_positions(portfolio)
''',
)
replace_all("app.py", "manager.generate_exits('default')", "manager.generate_exits(portfolio)")
replace_all("app.py", "manager.update_market_prices('default')", "manager.update_market_prices(portfolio)")
replace_once("app.py", "        orders = manager.get_pending_orders()\n", "        orders = manager.get_pending_orders(portfolio)\n")
replace_once("app.py", "        fills = manager.get_fills()\n", "        fills = manager.get_fills(portfolio)\n")
replace_once("app.py", "        perf = manager.get_strategy_performance()\n", "        perf = manager.get_strategy_performance(portfolio)\n")
replace_once("app.py", "        curve = manager.get_equity_curve()\n", "        curve = manager.get_equity_curve(portfolio)\n")
replace_once(
    "app.py",
    '''            manager = VirtualTradeManager(load_config_cached())
            show_performance(manager, "default")
''',
    '''            config = load_config_cached()
            portfolio = virtual_portfolio_name(config)
            manager = VirtualTradeManager(config)
            show_performance(manager, portfolio)
''',
)

# Recovery CLI requires an explicit portfolio; --strategy remains a deprecated alias.
replace_once(
    "database_backup.py",
    '    restore.add_argument("--strategy", default="momentum")\n',
    '''    restore.add_argument(
        "--portfolio",
        "--strategy",
        dest="portfolio_name",
        required=True,
        help="検証対象の仮想portfolio名（--strategyは互換alias）",
    )
''',
)
replace_once(
    "database_backup.py",
    '''                    strategy_name=args.strategy,
                    as_of_date=args.as_of_date,
                    dry_run=args.dry_run,
''',
    '''                    portfolio_name=args.portfolio_name,
                    as_of_date=args.as_of_date,
                    dry_run=args.dry_run,
                    require_history=True,
''',
)
replace_once(
    "src/database_backup.py",
    '''        *,
        strategy_name: str = "momentum",
        as_of_date: str | None = None,
        dry_run: bool = False,
''',
    '''        *,
        portfolio_name: str,
        as_of_date: str | None = None,
        dry_run: bool = False,
        require_history: bool = False,
''',
)
replace_once(
    "src/database_backup.py",
    "            return checker.run(strategy_name, as_of_date)\n",
    '''            return checker.run(
                portfolio_name,
                as_of_date,
                require_history=require_history,
            )
''',
)

# Integrity checker detects empty wrong-portfolio selection before deeper checks.
replace_once(
    "src/virtual_trade_integrity.py",
    "from .market_calendar import is_trading_day\n",
    "from .market_calendar import is_trading_day\nfrom .trading_identity import virtual_portfolio_name\n",
)
replace_once(
    "src/virtual_trade_integrity.py",
    '''        return True

    def _load_fills(
''',
    '''        return True

    @staticmethod
    def _portfolio_inventory(
        connection: sqlite3.Connection,
    ) -> dict[str, dict[str, int]]:
        inventory: dict[str, dict[str, int]] = {}
        for table_name in (
            "virtual_orders",
            "virtual_fills",
            "virtual_positions",
            "virtual_equity_curve",
        ):
            rows = connection.execute(
                f"SELECT strategy_name, COUNT(*) AS row_count "
                f"FROM {table_name} GROUP BY strategy_name"
            ).fetchall()
            for row in rows:
                name = str(row["strategy_name"])
                inventory.setdefault(name, {})[table_name] = int(row["row_count"])
        return inventory

    def _validate_portfolio_selection(
        self,
        report: IntegrityReport,
        inventory: dict[str, dict[str, int]],
        portfolio_name: str,
        *,
        require_history: bool,
    ) -> bool:
        requested = inventory.get(portfolio_name, {})
        requested_rows = sum(requested.values())
        nonempty = {
            name: counts
            for name, counts in inventory.items()
            if sum(counts.values()) > 0
        }
        report.checked["portfolio_count"] = len(nonempty)
        report.checked["portfolio_rows"] = requested_rows
        if requested_rows > 0:
            return True
        if nonempty:
            self._add(
                report,
                "error",
                "portfolio.empty_selection",
                "指定portfolioに履歴がありませんが、別portfolioには履歴があります",
                requested=portfolio_name,
                available=nonempty,
            )
            return False
        severity = "error" if require_history else "warning"
        self._add(
            report,
            severity,
            "portfolio.no_virtual_trade_history",
            "仮想取引履歴がまだ存在しません",
            requested=portfolio_name,
        )
        return not require_history

    def _load_fills(
''',
)
replace_once(
    "src/virtual_trade_integrity.py",
    '''    def run(
        self,
        strategy_name: str,
        as_of_date: str | None = None,
    ) -> IntegrityReport:
''',
    '''    def run(
        self,
        strategy_name: str,
        as_of_date: str | None = None,
        *,
        require_history: bool = False,
    ) -> IntegrityReport:
''',
)
replace_once(
    "src/virtual_trade_integrity.py",
    '''                if not self._validate_schema(connection, report):
                    return report
                has_commission = (
''',
    '''                if not self._validate_schema(connection, report):
                    return report
                inventory = self._portfolio_inventory(connection)
                if not self._validate_portfolio_selection(
                    report,
                    inventory,
                    strategy_name,
                    require_history=require_history,
                ):
                    return report
                has_commission = (
''',
)
replace_once(
    "src/virtual_trade_integrity.py",
    '    parser.add_argument("--strategy", default="momentum")\n',
    '''    parser.add_argument(
        "--portfolio",
        "--strategy",
        dest="portfolio_name",
        default=None,
        help="検査対象の仮想portfolio名（--strategyは互換alias）",
    )
    parser.add_argument(
        "--require-history",
        action="store_true",
        help="仮想取引履歴が0件の場合もエラーにする",
    )
''',
)
replace_once(
    "src/virtual_trade_integrity.py",
    '''    report = VirtualTradeIntegrityChecker(config).run(
        args.strategy,
        args.as_of_date,
    )
''',
    '''    portfolio = args.portfolio_name or virtual_portfolio_name(config)
    report = VirtualTradeIntegrityChecker(config).run(
        portfolio,
        args.as_of_date,
        require_history=args.require_history,
    )
''',
)

# Production drill: portfolio is explicit and mandatory.
replace_once(
    "scripts/sqlite_backup_recovery_drill.ps1",
    '''    [string]$Strategy = "momentum",

    [string]$Python = "python",
''',
    '''    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Portfolio,

    [string]$Python = "python",
''',
)
replace_all("scripts/sqlite_backup_recovery_drill.ps1", "$Strategy", "$Portfolio", minimum=2)
replace_all("scripts/sqlite_backup_recovery_drill.ps1", '"--strategy", $Portfolio', '"--portfolio", $Portfolio', minimum=3)
replace_once(
    "scripts/sqlite_backup_recovery_drill.ps1",
    '''    journal_mode = $JournalMode
    filesystem_space = @(
''',
    '''    journal_mode = $JournalMode
    virtual_portfolio = $Portfolio
    filesystem_space = @(
''',
)
replace_once(
    "scripts/sqlite_backup_recovery_drill.ps1",
    '''    live_db = $LiveDb
    drill_config_sha256 = $DrillConfigHash
''',
    '''    live_db = $LiveDb
    virtual_portfolio = $Portfolio
    drill_config_sha256 = $DrillConfigHash
''',
)
replace_once(
    "scripts/sqlite_backup_recovery_drill.ps1",
    '        "strategy": strategy,\n',
    '        "portfolio": strategy,\n',
)

# Config and docs expose both identities.
replace_once(
    "config.example.yaml",
    '''signals:
  ma_periods:
''',
    '''signals:
  strategy_name: momentum
  ma_periods:
''',
)
replace_once(
    "config.example.yaml",
    '''virtual_trade:
  enabled: true
''',
    '''virtual_trade:
  enabled: true
  portfolio_name: default
''',
)
replace_all("README.md", "--strategy momentum", "--portfolio default", minimum=2)
replace_once(
    "README.md",
    '''### アプリ内仮想トレード

moomooには注文を送信しません。SQLite上で仮想注文・仮想約定・仮想ポジション・仮想cashを管理します。
''',
    '''### アプリ内仮想トレード

moomooには注文を送信しません。SQLite上で仮想注文・仮想約定・仮想ポジション・仮想cashを管理します。`signals.strategy_name`はシグナル生成アルゴリズム、`virtual_trade.portfolio_name`は仮想取引台帳を表し、別の識別子として扱います。
''',
)

# Existing recovery tests now use non-empty `default` portfolio history.
replace_all("tests/test_database_backup_drill.py", '"--strategy",\n        "momentum",', '"--portfolio",\n        "default",', minimum=3)
replace_once(
    "tests/test_database_backup_drill.py",
    '''        connection.executescript(CREATE_TABLES_SQL)
        connection.commit()
''',
    '''        connection.executescript(CREATE_TABLES_SQL)
        connection.execute(
            "INSERT INTO symbols (code, name, role, tradable, type) "
            "VALUES ('JP.7203', 'Toyota', 'trade_candidate', 1, 'stock')"
        )
        connection.execute(
            "INSERT INTO daily_bars "
            "(code, date, open, high, low, close, volume, turnover) "
            "VALUES ('JP.7203', '2026-07-01', 100, 100, 100, 100, 1000, 100000)"
        )
        connection.execute(
            "INSERT INTO virtual_orders "
            "(id, strategy_name, code, side, quantity, order_type, status, "
            "submitted_at, filled_at, fill_price) "
            "VALUES (1, 'default', 'JP.7203', 'BUY', 1, 'MARKET_SIM', "
            "'FILLED', '2026-07-01', '2026-07-01', 100)"
        )
        connection.execute(
            "INSERT INTO virtual_fills "
            "(id, order_id, strategy_name, code, side, quantity, price, "
            "filled_at, fill_mode, commission) "
            "VALUES (1, 1, 'default', 'JP.7203', 'BUY', 1, 100, "
            "'2026-07-01', 'next_day_open', 0)"
        )
        connection.execute(
            "INSERT INTO virtual_positions "
            "(strategy_name, code, quantity, avg_cost, market_price, "
            "market_value, unrealized_pl, realized_pl) "
            "VALUES ('default', 'JP.7203', 1, 100, 100, 100, 0, 0)"
        )
        connection.execute(
            "INSERT INTO virtual_equity_curve "
            "(strategy_name, date, cash, position_value, total_equity, daily_return) "
            "VALUES ('default', '2026-07-01', 149900, 100, 150000, 0)"
        )
        connection.commit()
''',
)
replace_once(
    "tests/test_database_backup_drill.py",
    '''    dry_run_exit, dry_run_output = _run_cli(
''',
    '''    wrong_portfolio_exit, _ = _run_cli(
        capsys,
        "--config",
        str(config_path),
        "restore",
        str(secondary_backup),
        str(restore_path),
        "--portfolio",
        "momentum",
        "--dry-run",
    )
    assert wrong_portfolio_exit != 0
    assert not restore_path.exists()

    dry_run_exit, dry_run_output = _run_cli(
''',
)

# Windows simulation fixture and invocation.
replace_all("tests/run_database_backup_drill_windows.ps1", '"-Strategy", "momentum"', '"-Portfolio", "default"', minimum=3)
replace_once(
    "tests/run_database_backup_drill_windows.ps1",
    '''    connection.executescript(CREATE_TABLES_SQL)
    connection.commit()
''',
    '''    connection.executescript(CREATE_TABLES_SQL)
    connection.execute(
        "INSERT INTO symbols (code, name, role, tradable, type) "
        "VALUES ('JP.7203', 'Toyota', 'trade_candidate', 1, 'stock')"
    )
    connection.execute(
        "INSERT INTO daily_bars "
        "(code, date, open, high, low, close, volume, turnover) "
        "VALUES ('JP.7203', '2026-07-01', 100, 100, 100, 100, 1000, 100000)"
    )
    connection.execute(
        "INSERT INTO virtual_orders "
        "(id, strategy_name, code, side, quantity, order_type, status, "
        "submitted_at, filled_at, fill_price) "
        "VALUES (1, 'default', 'JP.7203', 'BUY', 1, 'MARKET_SIM', "
        "'FILLED', '2026-07-01', '2026-07-01', 100)"
    )
    connection.execute(
        "INSERT INTO virtual_fills "
        "(id, order_id, strategy_name, code, side, quantity, price, "
        "filled_at, fill_mode, commission) "
        "VALUES (1, 1, 'default', 'JP.7203', 'BUY', 1, 100, "
        "'2026-07-01', 'next_day_open', 0)"
    )
    connection.execute(
        "INSERT INTO virtual_positions "
        "(strategy_name, code, quantity, avg_cost, market_price, "
        "market_value, unrealized_pl, realized_pl) "
        "VALUES ('default', 'JP.7203', 1, 100, 100, 100, 0, 0)"
    )
    connection.execute(
        "INSERT INTO virtual_equity_curve "
        "(strategy_name, date, cash, position_value, total_equity, daily_return) "
        "VALUES ('default', '2026-07-01', 149900, 100, 150000, 0)"
    )
    connection.commit()
''',
)
replace_once(
    "tests/run_database_backup_drill_windows.ps1",
    '''        "initial_cash": 150000,
        "commission": 0,
''',
    '''        "initial_cash": 150000,
        "commission": 0,
        "portfolio_name": "default",
''',
)

# Main tests workflow recovery fixture.
replace_all(".github/workflows/tests.yml", "-Strategy momentum", "-Portfolio default")
replace_once(
    ".github/workflows/tests.yml",
    '''              connection.executescript(CREATE_TABLES_SQL)
              connection.commit()
''',
    '''              connection.executescript(CREATE_TABLES_SQL)
              connection.execute(
                  "INSERT INTO symbols (code, name, role, tradable, type) "
                  "VALUES ('JP.7203', 'Toyota', 'trade_candidate', 1, 'stock')"
              )
              connection.execute(
                  "INSERT INTO daily_bars "
                  "(code, date, open, high, low, close, volume, turnover) "
                  "VALUES ('JP.7203', '2026-07-01', 100, 100, 100, 100, 1000, 100000)"
              )
              connection.execute(
                  "INSERT INTO virtual_orders "
                  "(id, strategy_name, code, side, quantity, order_type, status, "
                  "submitted_at, filled_at, fill_price) "
                  "VALUES (1, 'default', 'JP.7203', 'BUY', 1, 'MARKET_SIM', "
                  "'FILLED', '2026-07-01', '2026-07-01', 100)"
              )
              connection.execute(
                  "INSERT INTO virtual_fills "
                  "(id, order_id, strategy_name, code, side, quantity, price, "
                  "filled_at, fill_mode, commission) "
                  "VALUES (1, 1, 'default', 'JP.7203', 'BUY', 1, 100, "
                  "'2026-07-01', 'next_day_open', 0)"
              )
              connection.execute(
                  "INSERT INTO virtual_positions "
                  "(strategy_name, code, quantity, avg_cost, market_price, "
                  "market_value, unrealized_pl, realized_pl) "
                  "VALUES ('default', 'JP.7203', 1, 100, 100, 100, 0, 0)"
              )
              connection.execute(
                  "INSERT INTO virtual_equity_curve "
                  "(strategy_name, date, cash, position_value, total_equity, daily_return) "
                  "VALUES ('default', '2026-07-01', 149900, 100, 150000, 0)"
              )
              connection.commit()
''',
)
replace_once(
    ".github/workflows/tests.yml",
    '''                  "initial_cash": 150000,
                  "commission": 0,
''',
    '''                  "initial_cash": 150000,
                  "commission": 0,
                  "portfolio_name": "default",
''',
)

write_new(
    "tests/test_virtual_portfolio_identity.py",
    '''from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from src.models import CREATE_TABLES_SQL
from src.trading_identity import signal_strategy_name, virtual_portfolio_name
from src.virtual_trade_integrity import VirtualTradeIntegrityChecker


class StubConfig:
    def __init__(self, database_path: Path, values: dict[str, Any] | None = None):
        self.database_path = str(database_path)
        self.values = values or {}

    def get(self, key_path: str, default: Any = None) -> Any:
        return self.values.get(key_path, default)


def create_database(path: Path, *, portfolio: str | None) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(CREATE_TABLES_SQL)
        if portfolio is None:
            return
        connection.execute(
            "INSERT INTO virtual_orders "
            "(id, strategy_name, code, side, quantity, order_type, status, submitted_at) "
            "VALUES (1, ?, 'JP.7203', 'BUY', 1, 'MARKET_SIM', 'PENDING', '2026-07-01')",
            (portfolio,),
        )
        connection.commit()


def test_identity_defaults_and_overrides(tmp_path: Path) -> None:
    config = StubConfig(tmp_path / "unused.db")
    assert signal_strategy_name(config) == "momentum"
    assert virtual_portfolio_name(config) == "default"

    configured = StubConfig(
        tmp_path / "unused.db",
        {
            "signals.strategy_name": "quality-low-risk",
            "virtual_trade.portfolio_name": "paper-jp",
        },
    )
    assert signal_strategy_name(configured) == "quality-low-risk"
    assert virtual_portfolio_name(configured) == "paper-jp"


def test_wrong_empty_portfolio_fails_when_another_has_history(tmp_path: Path) -> None:
    database = tmp_path / "portfolio.db"
    create_database(database, portfolio="default")
    checker = VirtualTradeIntegrityChecker(
        StubConfig(database, {"virtual_trade": {"initial_cash": 150000}})
    )

    report = checker.run("momentum", require_history=True)

    assert report.exit_code == 2
    assert any(
        finding.code == "portfolio.empty_selection" for finding in report.errors
    )
    assert report.checked["portfolio_rows"] == 0


def test_no_history_is_distinct_and_strict_mode_fails(tmp_path: Path) -> None:
    database = tmp_path / "empty.db"
    create_database(database, portfolio=None)
    checker = VirtualTradeIntegrityChecker(
        StubConfig(database, {"virtual_trade": {"initial_cash": 150000}})
    )

    normal = checker.run("default")
    strict = checker.run("default", require_history=True)

    assert normal.exit_code == 1
    assert any(
        finding.code == "portfolio.no_virtual_trade_history"
        for finding in normal.warnings
    )
    assert strict.exit_code == 2
    assert any(
        finding.code == "portfolio.no_virtual_trade_history"
        for finding in strict.errors
    )
''',
)

replace_once(
    "pyrightconfig.json",
    '    "src/virtual_trade.py",\n',
    '    "src/virtual_trade.py",\n    "src/trading_identity.py",\n',
)
replace_once(
    "pyrightconfig.json",
    '    "tests/test_virtual_integration.py",\n',
    '    "tests/test_virtual_integration.py",\n    "tests/test_virtual_portfolio_identity.py",\n',
)

print("virtual portfolio identity patch staged")
