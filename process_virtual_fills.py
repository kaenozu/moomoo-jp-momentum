"""
仮想約定処理スクリプト

ファイルパス: process_virtual_fills.py
何をするか: 未約定の仮想注文を日足データに基づいて約定判定する
なぜ存在するか: moomoo APIを使わずにアプリ内で約定をシミュレートするため
関連ファイル: src/virtual_trade.py, src/config.py

注意:
    - 約定判定は日足データに基づく簡易シミュレーションです
    - 実際の約定価格・板・スプレッド・約定順序とは異なる可能性があります
    - moomoo APIの注文系APIは使いません

使い方:
    python process_virtual_fills.py --date 2026-07-01
    python process_virtual_fills.py --from 2026-07-01 --to 2026-07-31
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.virtual_trade import VirtualTradeManager


def main():
    parser = argparse.ArgumentParser(description="Moomoo 仮想約定処理")
    parser.add_argument("--date", help="約定判定日（YYYY-MM-DD）。未指定なら今日")
    parser.add_argument("--from", dest="from_date", help="開始日")
    parser.add_argument("--to", dest="to_date", help="終了日")
    parser.add_argument("--strategy", default="default", help="戦術名")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    manager = VirtualTradeManager(config)
    total_fills = 0

    if args.from_date and args.to_date:
        # 期間指定
        from_dt = datetime.strptime(args.from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(args.to_date, "%Y-%m-%d")
        current = from_dt
        while current <= to_dt:
            date_str = current.strftime("%Y-%m-%d")
            fills = manager.process_fills(args.strategy, date_str)
            if fills:
                for f in fills:
                    print(f"  約定: {f.code} {f.side} {f.quantity}株 @{f.price:.0f} ({f.filled_at})")
                total_fills += len(fills)
            current += timedelta(days=1)
    else:
        # 単日指定
        target_date = args.date or datetime.now().strftime("%Y-%m-%d")
        fills = manager.process_fills(args.strategy, target_date)
        for f in fills:
            print(f"  約定: {f.code} {f.side} {f.quantity}株 @{f.price:.0f} ({f.filled_at})")
        total_fills += len(fills)

    print(f"\n合計 {total_fills} 件約定しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
