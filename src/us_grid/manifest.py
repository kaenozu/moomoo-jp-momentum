"""
Run manifest generation.

The manifest captures everything needed to reproduce a backtest run:
git SHA, config hash, data hash, parameter selection method, and warnings.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def git_sha(repo_root: str | Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root) if repo_root else None,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def git_branch(repo_root: str | Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(repo_root) if repo_root else None,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def config_sha256(config_text: str) -> str:
    return hashlib.sha256(config_text.encode("utf-8")).hexdigest()


def build_manifest(
    *,
    run_id: str,
    config_path: str,
    config_text: str,
    grid_config: Any,
    data_hash: str,
    data_sources: list[str],
    symbols: list[str],
    start_date: str,
    end_date: str,
    capital_jpy: float,
    currency: str,
    cost_model: str,
    fill_model: str,
    parameter_selection_method: str,
    random_seed: int,
    warnings: list[str],
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build the reproducible run manifest dict."""
    return {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(repo_root),
        "branch": git_branch(repo_root),
        "config_path": config_path,
        "config_sha256": config_sha256(config_text),
        "data_sha256": data_hash,
        "data_sources": data_sources,
        "symbols": symbols,
        "date_range": {"start": start_date, "end": end_date},
        "capital_jpy": capital_jpy,
        "currency": currency,
        "cost_model": cost_model,
        "fill_model": fill_model,
        "parameter_selection_method": parameter_selection_method,
        "random_seed": random_seed,
        "strategy_name": grid_config.strategy_name,
        "grid": {
            "spacing_mode": grid_config.spacing_mode,
            "spacing_pct": grid_config.spacing_pct,
            "atr_period": grid_config.atr_period,
            "atr_multiplier": grid_config.atr_multiplier,
            "buy_levels": grid_config.buy_levels,
            "sell_levels": grid_config.sell_levels,
            "quantity_per_level": grid_config.quantity_per_level,
            "core_allocation_pct": grid_config.core_allocation_pct,
            "regime_filter_enabled": grid_config.regime_filter_enabled,
        },
        "warnings": warnings,
    }


def save_manifest(manifest: dict[str, Any], report_dir: str | Path) -> Path:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "manifest.json"
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path
