"""
手動売買記録スクリプト

ファイルパス: record_trade.py
何をするか: CLIで手動売買を記録する
なぜ存在するか: 売買記録を手動で入力するため
関連ファイル: src/trade_log.py, src/config.py

使い方:
    python record_trade.py --code JP.4502 --side BUY --quantity 1 --price 4823 --reason "買い候補94点"
    python record_trade.py --code JP.4502 --side SELL --quantity 1 --price 5000 --reason "利確"
    python record_trade.py --list
"""

import argparse
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.trade_log import TradeLog


def list_trades(trade_log: TradeLog) -> None:
    """売買記録を一覧表示する"""
    trades = trade_log.get_all_trades()

    if not trades:
        print("売買記録がありません")
        return

    print("\n" + "=" * 80)
    print("売買記録一覧")
    print("=" * 80)
    print(
        f"{'ID':>5} {'コード':<12} {'方向':<5} {'数量':>6} "
        f"{'価格':>10} {'日時':<20} {'理由'}"
    )
    print("-" * 80)

    for t in trades:
        print(
            f"{t.id:>5} {t.code:<12} {t.side:<5} {t.quantity:>6} "
            f"{t.price:>10,.0f} {t.executed_at:<20} {t.reason[:30]}"
        )

    print("=" * 80)
    print(f"全{len(trades)}件")


def main() -> int:
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description="Moomoo 手動売買記録"
    )
    parser.add_argument(
        "--code",
        help="銘柄コード（例: JP.4502）",
    )
    parser.add_argument(
        "--side",
        choices=["BUY", "SELL"],
        help="売買方向（BUY or SELL）",
    )
    parser.add_argument(
        "--quantity",
        type=int,
        help="数量",
    )
    parser.add_argument(
        "--price",
        type=float,
        help="価格",
    )
    parser.add_argument(
        "--reason",
        default="",
        help="理由",
    )
    parser.add_argument(
        "--exit-rule",
        default="",
        help="売りルール",
    )
    parser.add_argument(
        "--memo",
        default="",
        help="メモ",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="売買記録を一覧表示",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="設定ファイルパス",
    )
    args = parser.parse_args()

    # 設定読み込み
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    trade_log = TradeLog(config)

    # 一覧表示
    if args.list:
        list_trades(trade_log)
        return 0

    # 売買記録
    if not args.code or not args.side or args.quantity is None or args.price is None:
        print("[ERROR] 売買記録には --code, --side, --quantity, --price が必要です")
        parser.print_help()
        return 1

    trade_id = trade_log.record_trade(
        code=args.code,
        side=args.side,
        quantity=args.quantity,
        price=args.price,
        reason=args.reason,
        exit_rule=args.exit_rule,
        memo=args.memo,
    )

    print(f"[OK] 売買記録を作成しました (ID: {trade_id})")
    print(f"  コード: {args.code}")
    print(f"  方向: {args.side}")
    print(f"  数量: {args.quantity}")
    print(f"  価格: {args.price:,.0f}円")
    print(f"  理由: {args.reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
