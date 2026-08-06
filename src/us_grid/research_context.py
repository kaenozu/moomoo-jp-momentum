"""Per-process canonical data context for US-grid research scripts."""

from __future__ import annotations

from pathlib import Path

from .data import UsDataBundle
from .data_v2 import (
    attach_corporate_actions as _attach_corporate_actions,
    load_or_fetch as _load_or_fetch,
)

_CURRENT_ACTIONS: dict[str, list[dict]] = {}


def _register(bundle: UsDataBundle) -> dict[str, list[dict]]:
    global _CURRENT_ACTIONS
    actions = _attach_corporate_actions(bundle)
    _CURRENT_ACTIONS = {
        code: [dict(action) for action in values]
        for code, values in actions.items()
    }
    return current_corporate_actions()


def current_corporate_actions() -> dict[str, list[dict]]:
    return {
        code: [dict(action) for action in values]
        for code, values in _CURRENT_ACTIONS.items()
    }


def load_or_fetch(
    symbols: list[str],
    start_date: str,
    end_date: str,
    data_dir: str | Path,
    *,
    fetch: bool = True,
    fx_start_date: str | None = None,
) -> UsDataBundle:
    bundle = _load_or_fetch(
        symbols,
        start_date,
        end_date,
        data_dir,
        fetch=fetch,
        fx_start_date=fx_start_date,
    )
    _register(bundle)
    return bundle


def attach_corporate_actions(bundle: UsDataBundle) -> dict[str, list[dict]]:
    return _register(bundle)
