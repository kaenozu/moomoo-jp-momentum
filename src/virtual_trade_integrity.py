"""Read-only integrity checks for SQLite-backed virtual trading state.

Usage::

    python -m src.virtual_trade_integrity --config config.yaml --strategy momentum

Exit codes are 0 for clean, 1 for warnings only, and 2 when errors are found.
The checker opens SQLite in read-only mode and never runs schema migrations.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .config import Config
from .market_calendar import is_trading_day


@dataclass(frozen=True)
class IntegrityFinding:
    """One actionable integrity result."""

    severity: str
    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntegrityReport:
    """Aggregated integrity results for one strategy."""

    strategy_name: str
    findings: list[IntegrityFinding] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> list[IntegrityFinding]:
        return [item for item in self.findings if item.severity == "error"]

    @property
    def warnings(self) -> list[IntegrityFinding]:
        return [item for item in self.findings if item.severity == "warning"]

    @property
    def exit_code(self) -> int:
        if self.errors:
            return 2
        if self.warnings:
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "exit_code": self.exit_code,
            "checked": dict(self.checked),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [asdict(item) for item in self.findings],
        }


@dataclass
class _ReplayPosition:
    quantity: int = 0
    avg_cost: float = 0.0
    realized_pl: float = 0.0
    last_price: float = 0.0


class VirtualTradeIntegrityChecker:
    """Inspect virtual-trade state without modifying the database."""

    REQUIRED_TABLES = {
        "virtual_orders",
        "virtual_fills",
        "virtual_positions",
        "virtual_equity_curve",
        "daily_bars",
    }

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)
        virtual_config = config.get("virtual_trade", {})
        self.initial_cash = float(virtual_config.get("initial_cash", 100000))
        self.legacy_commission = float(virtual_config.get("commission", 0))

    def _connect_read_only(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(f"データベースが見つかりません: {self.db_path}")
        uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _add(
        report: IntegrityReport,
        severity: str,
        finding_code: str,
        message: str,
        **context: Any,
    ) -> None:
        report.findings.append(
            IntegrityFinding(severity, finding_code, message, context)
        )

    @staticmethod
    def _parse_date(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        if len(text) < 10:
            return None
        candidate = text[:10]
        try:
            datetime.strptime(candidate, "%Y-%m-%d")
        except ValueError:
            return None
        return candidate

    def _check_trading_date(
        self,
        report: IntegrityReport,
        value: Any,
        *,
        severity: str,
        code: str,
        entity: str,
        entity_id: Any,
    ) -> None:
        parsed = self._parse_date(value)
        if parsed is None:
            self._add(
                report,
                "error",
                f"{code}.invalid_date",
                f"{entity}の日付形式が不正です",
                id=entity_id,
                value=value,
            )
            return
        try:
            trading_day = is_trading_day(parsed)
        except (RuntimeError, ValueError) as error:
            self._add(
                report,
                "error",
                f"{code}.calendar_error",
                f"{entity}の日付を営業日判定できません: {error}",
                id=entity_id,
                date=parsed,
            )
            return
        if not trading_day:
            self._add(
                report,
                severity,
                f"{code}.non_trading_day",
                f"{entity}がJPX休場日に記録されています",
                id=entity_id,
                date=parsed,
            )

    @staticmethod
    def _table_columns(
        connection: sqlite3.Connection,
        table_name: str,
    ) -> set[str]:
        return {
            str(row[1])
            for row in connection.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
        }

    def _validate_schema(
        self,
        connection: sqlite3.Connection,
        report: IntegrityReport,
    ) -> bool:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = sorted(self.REQUIRED_TABLES - tables)
        for table_name in missing:
            self._add(
                report,
                "error",
                "schema.missing_table",
                "仮想取引テーブルがありません",
                table=table_name,
            )
        if missing:
            return False
        if "commission" not in self._table_columns(connection, "virtual_fills"):
            self._add(
                report,
                "error",
                "schema.missing_fill_commission",
                "virtual_fills.commissionがありません。マイグレーションが必要です",
            )
            return False
        return True

    def _load_fills(
        self,
        connection: sqlite3.Connection,
        strategy_name: str,
        as_of_date: str | None,
        has_commission: bool,
    ) -> list[sqlite3.Row]:
        commission_select = "commission" if has_commission else "NULL AS commission"
        date_filter = (
            "AND COALESCE(substr(filled_at, 1, 10), '') <= ?"
            if as_of_date
            else ""
        )
        params: list[Any] = [strategy_name]
        if as_of_date:
            params.append(as_of_date)
        return connection.execute(
            f"""
            SELECT id, order_id, strategy_name, code, side, quantity, price,
                   filled_at, fill_mode, {commission_select}
            FROM virtual_fills
            WHERE strategy_name = ? {date_filter}
            ORDER BY COALESCE(filled_at, ''), id
            """,
            params,
        ).fetchall()

    def _commission_for_fill(
        self,
        report: IntegrityReport,
        row: sqlite3.Row,
    ) -> tuple[float, bool]:
        raw = row["commission"]
        if raw is None:
            self._add(
                report,
                "warning",
                "fill.legacy_commission",
                "旧fillに手数料が保存されていないため現在設定を使用します",
                fill_id=row["id"],
                fallback_commission=self.legacy_commission,
            )
            return self.legacy_commission, True
        try:
            value = float(raw)
        except (TypeError, ValueError):
            self._add(
                report,
                "error",
                "fill.invalid_commission",
                "fillの手数料が数値ではありません",
                fill_id=row["id"],
                value=raw,
            )
            return 0.0, False
        if value < 0:
            self._add(
                report,
                "error",
                "fill.invalid_commission",
                "fillの手数料が負数です",
                fill_id=row["id"],
                value=value,
            )
            return 0.0, False
        return value, True

    def _check_order_fill_links(
        self,
        connection: sqlite3.Connection,
        report: IntegrityReport,
        strategy_name: str,
        as_of_date: str | None,
    ) -> None:
        order_date_filter = (
            "AND COALESCE(substr(o.filled_at, 1, 10), '') <= ?"
            if as_of_date
            else ""
        )
        params: list[Any] = [strategy_name]
        if as_of_date:
            params.append(as_of_date)
        missing_fills = connection.execute(
            f"""
            SELECT o.id, o.code, o.filled_at
            FROM virtual_orders o
            LEFT JOIN virtual_fills f ON f.order_id = o.id
            WHERE o.strategy_name = ? AND o.status = 'FILLED'
              AND f.id IS NULL {order_date_filter}
            ORDER BY o.id
            """,
            params,
        ).fetchall()
        for row in missing_fills:
            self._add(
                report,
                "error",
                "order.missing_fill",
                "FILLED注文に対応するfillがありません",
                order_id=row["id"],
                code=row["code"],
            )

        fill_date_filter = (
            "AND COALESCE(substr(f.filled_at, 1, 10), '') <= ?"
            if as_of_date
            else ""
        )
        params = [strategy_name]
        if as_of_date:
            params.append(as_of_date)
        orphan_fills = connection.execute(
            f"""
            SELECT f.id, f.order_id, f.code, o.status
            FROM virtual_fills f
            LEFT JOIN virtual_orders o ON o.id = f.order_id
            WHERE f.strategy_name = ?
              AND (o.id IS NULL OR o.strategy_name <> f.strategy_name
                   OR o.status <> 'FILLED')
              {fill_date_filter}
            ORDER BY f.id
            """,
            params,
        ).fetchall()
        for row in orphan_fills:
            self._add(
                report,
                "error",
                "fill.orphan_or_status_mismatch",
                "fillに対応するFILLED注文がありません",
                fill_id=row["id"],
                order_id=row["order_id"],
                order_status=row["status"],
            )

        report.checked["missing_fills"] = len(missing_fills)
        report.checked["orphan_fills"] = len(orphan_fills)

    def _replay(
        self,
        report: IntegrityReport,
        fills: list[sqlite3.Row],
    ) -> tuple[float, dict[str, _ReplayPosition], bool]:
        cash = self.initial_cash
        positions: dict[str, _ReplayPosition] = {}
        complete = True

        for row in fills:
            self._check_trading_date(
                report,
                row["filled_at"],
                severity="error",
                code="fill",
                entity="fill",
                entity_id=row["id"],
            )
            try:
                side = str(row["side"])
                code = str(row["code"])
                quantity = int(row["quantity"])
                price = float(row["price"])
            except (TypeError, ValueError):
                self._add(
                    report,
                    "error",
                    "fill.invalid_values",
                    "fillのside/code/quantity/priceを解釈できません",
                    fill_id=row["id"],
                )
                complete = False
                continue
            commission, commission_valid = self._commission_for_fill(report, row)
            if quantity <= 0 or price < 0 or side not in {"BUY", "SELL"}:
                self._add(
                    report,
                    "error",
                    "fill.invalid_values",
                    "fillのside・数量・価格が不正です",
                    fill_id=row["id"],
                    side=side,
                    quantity=quantity,
                    price=price,
                )
                complete = False
                continue
            if not commission_valid:
                complete = False
                continue

            gross = price * quantity
            state = positions.setdefault(code, _ReplayPosition())
            if side == "BUY":
                new_quantity = state.quantity + quantity
                state.avg_cost = (
                    state.avg_cost * state.quantity + gross
                ) / new_quantity
                state.quantity = new_quantity
                state.last_price = price
                cash -= gross + commission
                continue

            cash += gross - commission
            if quantity > state.quantity:
                self._add(
                    report,
                    "error",
                    "position.sell_exceeds_fill_history",
                    "SELL数量がそれ以前のBUY fillから再構築できる数量を超えています",
                    fill_id=row["id"],
                    code=code,
                    sell_quantity=quantity,
                    available_quantity=state.quantity,
                )
                complete = False
                continue
            state.quantity -= quantity
            state.realized_pl += (price - state.avg_cost) * quantity - commission
            state.last_price = price

        return cash, positions, complete

    def _check_current_positions(
        self,
        connection: sqlite3.Connection,
        report: IntegrityReport,
        strategy_name: str,
        replayed: dict[str, _ReplayPosition],
        complete: bool,
        as_of_date: str | None,
    ) -> None:
        rows = connection.execute(
            """
            SELECT code, quantity, avg_cost, realized_pl
            FROM virtual_positions
            WHERE strategy_name = ?
            ORDER BY code
            """,
            (strategy_name,),
        ).fetchall()
        report.checked["position_rows"] = len(rows)
        report.checked["position_comparison_skipped_future_fills"] = 0
        if as_of_date is not None:
            future_fill = connection.execute(
                """
                SELECT 1 FROM virtual_fills
                WHERE strategy_name = ?
                  AND COALESCE(substr(filled_at, 1, 10), '') > ?
                LIMIT 1
                """,
                (strategy_name, as_of_date),
            ).fetchone()
            report.checked["position_comparison_skipped_future_fills"] = int(
                future_fill is not None
            )
            if future_fill is not None:
                return
        if not complete:
            self._add(
                report,
                "warning",
                "position.comparison_skipped",
                "fill履歴が不完全なため現在ポジションとの比較を省略しました",
            )
            return

        cached = {str(row["code"]): row for row in rows}
        expected_codes = set(replayed)
        if set(cached) != expected_codes:
            self._add(
                report,
                "error",
                "position.code_set_mismatch",
                "fill再生とvirtual_positionsの銘柄集合が一致しません",
                cached=sorted(cached),
                replayed=sorted(expected_codes),
            )
        for code in sorted(set(cached) & expected_codes):
            row = cached[code]
            state = replayed[code]
            quantity = int(row["quantity"])
            avg_cost = float(row["avg_cost"])
            realized_pl = float(row["realized_pl"] or 0.0)
            if quantity != state.quantity:
                self._add(
                    report,
                    "error",
                    "position.quantity_mismatch",
                    "ポジション数量がfill再生結果と一致しません",
                    code=code,
                    cached=quantity,
                    replayed=state.quantity,
                )
            if abs(avg_cost - state.avg_cost) > 0.01:
                self._add(
                    report,
                    "error",
                    "position.avg_cost_mismatch",
                    "平均取得単価がfill再生結果と一致しません",
                    code=code,
                    cached=avg_cost,
                    replayed=state.avg_cost,
                )
            if abs(realized_pl - state.realized_pl) > 0.01:
                self._add(
                    report,
                    "error",
                    "position.realized_pl_mismatch",
                    "実現損益がfill再生結果と一致しません",
                    code=code,
                    cached=realized_pl,
                    replayed=state.realized_pl,
                )

    def _position_value(
        self,
        connection: sqlite3.Connection,
        positions: dict[str, _ReplayPosition],
        target_date: str,
    ) -> float:
        value = 0.0
        for code, state in positions.items():
            if state.quantity <= 0:
                continue
            row = connection.execute(
                """
                SELECT close FROM daily_bars
                WHERE code = ? AND date <= ? AND close IS NOT NULL
                ORDER BY date DESC LIMIT 1
                """,
                (code, target_date),
            ).fetchone()
            market_price = (
                float(row["close"])
                if row and row["close"] is not None
                else state.last_price or state.avg_cost
            )
            value += market_price * state.quantity
        return value

    def _check_latest_equity(
        self,
        connection: sqlite3.Connection,
        report: IntegrityReport,
        strategy_name: str,
        as_of_date: str | None,
        has_commission: bool,
    ) -> None:
        date_filter = "AND date <= ?" if as_of_date else ""
        params: list[Any] = [strategy_name]
        if as_of_date:
            params.append(as_of_date)
        row = connection.execute(
            f"""
            SELECT id, date, cash, position_value, total_equity
            FROM virtual_equity_curve
            WHERE strategy_name = ? {date_filter}
            ORDER BY date DESC LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            report.checked["equity_rows"] = 0
            return
        report.checked["equity_rows"] = 1
        self._check_trading_date(
            report,
            row["date"],
            severity="error",
            code="equity",
            entity="equity行",
            entity_id=row["id"],
        )
        target_date = str(row["date"])
        fills = self._load_fills(
            connection,
            strategy_name,
            target_date,
            has_commission,
        )
        equity_report = IntegrityReport(strategy_name=strategy_name)
        expected_cash, positions, complete = self._replay(equity_report, fills)
        if not complete:
            self._add(
                report,
                "warning",
                "equity.comparison_skipped",
                "fill履歴が不完全なためequity比較を省略しました",
                date=target_date,
            )
            return
        expected_position_value = self._position_value(
            connection,
            positions,
            target_date,
        )
        expected_total = expected_cash + expected_position_value
        comparisons = (
            ("cash", row["cash"], expected_cash),
            ("position_value", row["position_value"], expected_position_value),
            ("total_equity", row["total_equity"], expected_total),
        )
        for field_name, raw_actual, expected in comparisons:
            actual = float(raw_actual or 0.0)
            if abs(actual - expected) > 0.01:
                self._add(
                    report,
                    "error",
                    f"equity.{field_name}_mismatch",
                    f"equityの{field_name}がfill再生結果と一致しません",
                    date=target_date,
                    stored=actual,
                    replayed=expected,
                )

    def _check_order_dates(
        self,
        connection: sqlite3.Connection,
        report: IntegrityReport,
        strategy_name: str,
        as_of_date: str | None,
    ) -> None:
        date_filter = (
            "AND COALESCE(substr(submitted_at, 1, 10), '') <= ?"
            if as_of_date
            else ""
        )
        params: list[Any] = [strategy_name]
        if as_of_date:
            params.append(as_of_date)
        rows = connection.execute(
            f"""
            SELECT id, submitted_at FROM virtual_orders
            WHERE strategy_name = ? {date_filter}
            ORDER BY id
            """,
            params,
        ).fetchall()
        report.checked["order_rows"] = len(rows)
        for row in rows:
            self._check_trading_date(
                report,
                row["submitted_at"],
                severity="warning",
                code="order",
                entity="注文",
                entity_id=row["id"],
            )

    def run(
        self,
        strategy_name: str,
        as_of_date: str | None = None,
    ) -> IntegrityReport:
        report = IntegrityReport(strategy_name=strategy_name)
        if as_of_date is not None and self._parse_date(as_of_date) != as_of_date:
            self._add(
                report,
                "error",
                "input.invalid_as_of_date",
                "--as-ofはYYYY-MM-DD形式で指定してください",
                value=as_of_date,
            )
            return report

        try:
            with closing(self._connect_read_only()) as connection:
                if not self._validate_schema(connection, report):
                    return report
                has_commission = (
                    "commission"
                    in self._table_columns(connection, "virtual_fills")
                )
                self._check_order_fill_links(
                    connection,
                    report,
                    strategy_name,
                    as_of_date,
                )
                self._check_order_dates(
                    connection,
                    report,
                    strategy_name,
                    as_of_date,
                )
                fills = self._load_fills(
                    connection,
                    strategy_name,
                    as_of_date,
                    has_commission,
                )
                report.checked["fill_rows"] = len(fills)
                _, positions, complete = self._replay(report, fills)
                self._check_current_positions(
                    connection,
                    report,
                    strategy_name,
                    positions,
                    complete,
                    as_of_date,
                )
                self._check_latest_equity(
                    connection,
                    report,
                    strategy_name,
                    as_of_date,
                    has_commission,
                )
        except (FileNotFoundError, sqlite3.Error) as error:
            self._add(
                report,
                "error",
                "database.open_failed",
                str(error),
            )
        return report


def _print_human(report: IntegrityReport) -> None:
    print(f"Virtual trade integrity: strategy={report.strategy_name}")
    print(
        f"errors={len(report.errors)} warnings={len(report.warnings)} "
        f"checked={report.checked}"
    )
    if not report.findings:
        print("OK: 不整合は見つかりませんでした")
        return
    for finding in report.findings:
        context = f" {finding.context}" if finding.context else ""
        print(
            f"[{finding.severity.upper()}] {finding.code}: "
            f"{finding.message}{context}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SQLite仮想取引データを読み取り専用で整合性検査する"
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--strategy", default="momentum")
    parser.add_argument("--as-of", default=None, dest="as_of_date")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)

    try:
        config = Config(args.config)
    except (FileNotFoundError, ValueError) as error:
        print(str(error))
        return 2

    report = VirtualTradeIntegrityChecker(config).run(
        args.strategy,
        args.as_of_date,
    )
    if args.json_output:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
