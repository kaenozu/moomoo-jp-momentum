#!/usr/bin/env python3
"""既存設定からmp20仮想ペーパートレード専用設定を生成する。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.paper_trade_readiness import write_mp20_paper_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="config.yaml", help="元の設定ファイル")
    parser.add_argument(
        "--output",
        default="config.paper-mp20.yaml",
        help="生成する専用設定ファイル",
    )
    parser.add_argument("--force", action="store_true", help="既存出力を上書き")
    args = parser.parse_args()

    try:
        output = write_mp20_paper_config(
            args.base,
            args.output,
            overwrite=args.force,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] generated: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
