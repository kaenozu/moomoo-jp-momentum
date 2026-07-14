"""Resolve signal-algorithm and virtual-portfolio identifiers from config."""

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
