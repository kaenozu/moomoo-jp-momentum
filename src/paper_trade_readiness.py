"""mp20仮想ペーパートレードの設定生成と受入ゲート。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from .config import Config

MP20_MAX_POSITIONS = 20
CURRENT_STOP_LOSS_PCT = 5.0


@dataclass(frozen=True)
class ReadinessCheck:
    """1つのペーパートレード受入条件。"""

    name: str
    status: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PaperTradeReadiness:
    """ペーパートレード開始可否と根拠。"""

    ready: bool
    checks: tuple[ReadinessCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "checks": [check.to_dict() for check in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _ensure_mapping(value: Any, key: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a mapping")
    return dict(value)


def build_mp20_paper_config(base: Mapping[str, Any]) -> dict[str, Any]:
    """既存設定を保持しつつ、mp20仮想運用用の安全な上書きを適用する。"""

    # YAML由来のネストを完全に分離するため、シリアライズでdeep copyする。
    profile = yaml.safe_load(yaml.safe_dump(dict(base), allow_unicode=True)) or {}
    if not isinstance(profile, dict):
        raise ValueError("configuration root must be a mapping")

    backtest = _ensure_mapping(profile.get("backtest"), "backtest")
    backtest["max_positions"] = MP20_MAX_POSITIONS
    backtest["stop_loss_pct"] = CURRENT_STOP_LOSS_PCT
    profile["backtest"] = backtest

    virtual_trade = _ensure_mapping(profile.get("virtual_trade"), "virtual_trade")
    virtual_trade.update(
        {
            "enabled": True,
            "max_total_positions": MP20_MAX_POSITIONS,
            "max_position_per_symbol": 1,
            "market_fill_mode": "next_day_open",
        }
    )
    profile["virtual_trade"] = virtual_trade

    # JP株の注文API経路は使用しない。SQLite仮想売買のみを許可する。
    paper_trade = _ensure_mapping(profile.get("paper_trade"), "paper_trade")
    paper_trade.update(
        {
            "enabled": False,
            "jp_api_simulate_supported": False,
            "allow_market_order": False,
        }
    )
    profile["paper_trade"] = paper_trade

    # 初回受入は明示実行とし、自動スケジュールを継承しない。
    scheduler = _ensure_mapping(profile.get("scheduler"), "scheduler")
    scheduler["enabled"] = False
    profile["scheduler"] = scheduler
    return profile


def write_mp20_paper_config(
    base_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """ベース設定から専用設定ファイルを生成する。"""

    source = Path(base_path)
    destination = Path(output_path)
    if not source.exists():
        raise FileNotFoundError(f"base config not found: {source}")
    if source.resolve() == destination.resolve():
        raise ValueError("output config must be different from base config")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"output config already exists: {destination}")

    loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, Mapping):
        raise ValueError("configuration root must be a mapping")
    profile = build_mp20_paper_config(loaded)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        yaml.safe_dump(profile, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return destination


def _check(
    checks: list[ReadinessCheck],
    condition: bool,
    name: str,
    success: str,
    failure: str,
    *,
    failure_status: str = "error",
) -> None:
    checks.append(
        ReadinessCheck(
            name=name,
            status="pass" if condition else failure_status,
            message=success if condition else failure,
        )
    )


def evaluate_paper_trade_readiness(
    config: Config,
    *,
    require_paths: bool = True,
) -> PaperTradeReadiness:
    """mp20仮想運用を開始してよい設定か検査する。"""

    checks: list[ReadinessCheck] = []
    virtual_enabled = config.get("virtual_trade.enabled", True) is True
    _check(
        checks,
        virtual_enabled,
        "virtual_trade_enabled",
        "SQLite仮想売買が有効です",
        "virtual_trade.enabled=true が必要です",
    )

    paper_disabled = config.get("paper_trade.enabled", False) is False
    _check(
        checks,
        paper_disabled,
        "api_paper_trade_disabled",
        "API SIMULATE取引は無効です",
        "paper_trade.enabled=false にしてください",
    )
    market_order_disabled = config.get("paper_trade.allow_market_order", False) is False
    _check(
        checks,
        market_order_disabled,
        "api_market_order_disabled",
        "API成行注文は無効です",
        "paper_trade.allow_market_order=false にしてください",
    )
    jp_simulate_disabled = (
        config.get("paper_trade.jp_api_simulate_supported", False) is False
    )
    _check(
        checks,
        jp_simulate_disabled,
        "jp_api_simulate_disabled",
        "JP API SIMULATEを使用しません",
        "paper_trade.jp_api_simulate_supported=false にしてください",
    )

    backtest_positions = int(config.get("backtest.max_positions", 0))
    virtual_positions = int(config.get("virtual_trade.max_total_positions", 0))
    _check(
        checks,
        backtest_positions == MP20_MAX_POSITIONS,
        "backtest_mp20",
        "バックテスト候補はmax_positions=20です",
        f"backtest.max_positions={MP20_MAX_POSITIONS} にしてください",
    )
    _check(
        checks,
        virtual_positions == MP20_MAX_POSITIONS,
        "virtual_trade_mp20",
        "仮想運用も最大20銘柄です",
        f"virtual_trade.max_total_positions={MP20_MAX_POSITIONS} にしてください",
    )
    _check(
        checks,
        int(config.get("virtual_trade.max_position_per_symbol", 0)) == 1,
        "single_position_per_symbol",
        "1銘柄あたり1ポジションです",
        "virtual_trade.max_position_per_symbol=1 にしてください",
    )
    _check(
        checks,
        math_isclose(
            float(config.get("backtest.stop_loss_pct", 0.0)),
            CURRENT_STOP_LOSS_PCT,
        ),
        "validated_stop_loss",
        "検証済みのstop_loss_pct=5.0です",
        "backtest.stop_loss_pct=5.0 を維持してください",
    )
    _check(
        checks,
        str(config.get("virtual_trade.market_fill_mode", "")) == "next_day_open",
        "next_day_open_fill",
        "翌営業日始値で仮想約定します",
        "virtual_trade.market_fill_mode=next_day_open にしてください",
    )

    initial_cash = float(config.get("virtual_trade.initial_cash", 0.0))
    max_position_amount = float(
        config.get("virtual_trade.max_position_amount", 0.0)
    )
    _check(
        checks,
        initial_cash > 0,
        "positive_initial_cash",
        "初期仮想cashは正数です",
        "virtual_trade.initial_cash は正数にしてください",
    )
    _check(
        checks,
        0 < max_position_amount <= initial_cash,
        "bounded_position_amount",
        "1銘柄上限は初期cash以内です",
        "virtual_trade.max_position_amount は0より大きくinitial_cash以下にしてください",
    )

    if require_paths:
        database_path = Path(config.database_path)
        watchlist_path = Path(config.watchlist_file)
        _check(
            checks,
            database_path.exists(),
            "database_exists",
            f"DBを確認しました: {database_path}",
            f"DBがありません: {database_path}",
        )
        _check(
            checks,
            watchlist_path.exists(),
            "watchlist_exists",
            f"watchlistを確認しました: {watchlist_path}",
            f"watchlistがありません: {watchlist_path}",
        )

    ready = all(check.status != "error" for check in checks)
    return PaperTradeReadiness(ready=ready, checks=tuple(checks))


def math_isclose(left: float, right: float) -> bool:
    """設定値比較用の小さなfloat helper。"""

    return abs(left - right) <= 1e-9


def execute_if_ready(
    config: Config,
    runner: Callable[[], dict[str, Any]],
    *,
    require_paths: bool = True,
) -> dict[str, Any]:
    """受入ゲート通過時だけ日次サイクルを実行する。"""

    readiness = evaluate_paper_trade_readiness(config, require_paths=require_paths)
    if not readiness.ready:
        failures = "; ".join(
            check.message for check in readiness.checks if check.status == "error"
        )
        raise RuntimeError(f"paper trade readiness failed: {failures}")
    return runner()
