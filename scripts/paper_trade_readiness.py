#!/usr/bin/env python3
"""mp20仮想ペーパートレードの受入確認と明示実行。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_daily_cycle import Provider, run_cycle  # noqa: E402
from src.config import load_config  # noqa: E402
from src.paper_trade_readiness import (  # noqa: E402
    evaluate_paper_trade_readiness,
    execute_if_ready,
)


def _print_report(report_json: str) -> None:
    payload = json.loads(report_json)
    print(f"ready={str(payload['ready']).lower()}")
    for check in payload["checks"]:
        print(f"[{check['status'].upper()}] {check['name']}: {check['message']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.paper-mp20.yaml")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument(
        "--provider",
        choices=["auto", "moomoo", "yfinance"],
        default="auto",
    )
    parser.add_argument("--allow-stale", action="store_true")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="受入ゲート通過後にSQLite仮想日次サイクルを実行",
    )
    parser.add_argument("--json", action="store_true", help="受入結果をJSON表示")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    report = evaluate_paper_trade_readiness(config)
    if args.json:
        print(report.to_json())
    else:
        _print_report(report.to_json())

    if not args.execute:
        return 0 if report.ready else 1
    if not report.ready:
        print("[ERROR] readiness gate failed; daily cycle was not executed", file=sys.stderr)
        return 1

    provider = cast(Provider, args.provider)
    try:
        result = execute_if_ready(
            config,
            lambda: run_cycle(
                args.date,
                dry_run=False,
                config_path=args.config,
                allow_stale=args.allow_stale,
                skip_fetch=False,
                provider=provider,
            ),
        )
    except (RuntimeError, SystemError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
