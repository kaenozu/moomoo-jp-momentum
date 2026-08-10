"""`grid_etf_v1`専用のSQLite状態ストア。

既存のvirtual_*表は同一銘柄の複数グリッド段を一意に表現できないため、
この戦略だけが使う名前空間を持つ。既存momentumの行・schemaは変更しない。
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .grid_etf import GridBar, GridBarResult, GridConfig, GridEtfV1


class GridEtfStateStore:
    """グリッド状態・約定・資産曲線を戦略名と銘柄別に保存する。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS grid_etf_states (
                    strategy_name TEXT NOT NULL,
                    code TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(strategy_name, code)
                );
                CREATE TABLE IF NOT EXISTS grid_etf_fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    side TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    UNIQUE(strategy_name, code, date, side, level)
                );
                CREATE TABLE IF NOT EXISTS grid_etf_equity_curve (
                    strategy_name TEXT NOT NULL,
                    code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    equity REAL NOT NULL,
                    reserved_cash REAL NOT NULL,
                    stopped INTEGER NOT NULL,
                    PRIMARY KEY(strategy_name, code, date)
                );
                """
            )

    def save(self, strategy_name: str, code: str, strategy: GridEtfV1) -> None:
        state = strategy.snapshot()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO grid_etf_states(strategy_name, code, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(strategy_name, code) DO UPDATE SET
                    state_json=excluded.state_json,
                    updated_at=excluded.updated_at
                """,
                (strategy_name, code, json.dumps(state, ensure_ascii=False, sort_keys=True), datetime.now().isoformat()),
            )

    def load(self, strategy_name: str, code: str, config: GridConfig) -> GridEtfV1 | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM grid_etf_states WHERE strategy_name=? AND code=?",
                (strategy_name, code),
            ).fetchone()
        if row is None:
            return None
        return GridEtfV1.from_snapshot(config, json.loads(row["state_json"]))

    def apply_bar(self, strategy_name: str, code: str, config: GridConfig, bar: GridBar) -> GridBarResult:
        strategy = self.load(strategy_name, code, config) or GridEtfV1(config)
        if strategy._bars and bar.date <= strategy._bars[-1].date:
            raise ValueError(f"同一戦略・銘柄の日付を重複処理できません: {bar.date}")
        result = strategy.on_bar(bar)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO grid_etf_equity_curve
                (strategy_name, code, date, equity, reserved_cash, stopped)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (strategy_name, code, bar.date, result.equity, result.reserved_cash, int(result.stopped)),
            )
            for fill in result.fills:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO grid_etf_fills
                    (strategy_name, code, date, side, level, quantity, price)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (strategy_name, code, fill.date, fill.side.value, fill.level, fill.quantity, fill.price),
                )
        self.save(strategy_name, code, strategy)
        return result
