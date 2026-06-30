"""
ペーパートレードCLIスクリプト（experimental）

ファイルパス: paper_order.py
何をするか: CLIからUS市場向けのSIMULATEペーパートレード注文を出力する
なぜ存在するか: 実資金を使わずに取引ロジックを検証するため
関連ファイル: src/paper_trade.py, src/config.py

注意:
    - これはSIMULATE環境でのみ動作します
    - 実資金は使用しません
    - TrdEnv.REAL は使用しません
    - JP市場では常に停止します
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def print_warning() -> None:
    """警告メッセージを表示する"""
    print()
    print("=" * 60)
    print("  このプロジェクトではAPI注文機能を使用しません。")
    print("  moomoo JPの日本株SIMULATE注文はOpenAPI経由では")
    print("  利用できない可能性が高いため、注文はmoomooアプリで")
    print("  手動実行してください。")
    print("  売買記録は record_trade.py またはStreamlitの")
    print("  手動売買ログから登録してください。")
    print("=" * 60)
    print()


def print_unsupported() -> None:
    """JP市場非対応メッセージを表示する"""
    print()
    print("=" * 60)
    print("  moomoo JP / FUTUJP では、OpenAPI経由の")
    print("  日本株SIMULATE注文が利用できないため、")
    print("  この機能は無効です。")
    print()
    print("  アプリ内デモ取引とAPI SIMULATEは別物です。")
    print("  取引はmoomooアプリで手動実行してください。")
    print("=" * 60)
    print()


def main() -> int:
    """メイン関数"""
    parser = argparse.ArgumentParser(description="Moomoo ペーパートレードCLI（experimental）")
    parser.add_argument("--code", help="銘柄コード（例: US.AAPL）")
    parser.add_argument("--side", choices=["BUY", "SELL"], help="売買方向（BUY or SELL）")
    parser.add_argument("--quantity", type=int, help="数量")
    parser.add_argument("--price", type=float, help="価格")
    parser.add_argument("--order-type", choices=["LIMIT", "MARKET"], default="LIMIT", help="注文タイプ")
    parser.add_argument("--market", choices=["JP", "US"], default="JP", help="市場（デフォルト: JP）")
    parser.add_argument("--experimental", action="store_true", help="US市場のexperimental機能を有効化")
    parser.add_argument("--list-orders", action="store_true", help="注文一覧を表示")
    parser.add_argument("--positions", action="store_true", help="ポジション一覧を表示")
    parser.add_argument("--cancel", action="store_true", help="注文をキャンセル")
    parser.add_argument("--order-id", help="キャンセルする注文ID")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo ペーパートレード（experimental）")
    print("=" * 60)
    print_warning()

    if args.market == "JP":
        print_unsupported()
        return 1

    if args.market == "US" and not args.experimental:
        print("[ERROR] US市場のペーパートレードには --experimental フラグが必要です")
        print("  例: python paper_order.py --market US --experimental --code US.AAPL --side BUY --quantity 1 --price 200")
        return 1

    print("[INFO] US市場のペーパートレード（experimental）")
    print("  注意: これはSIMULATE環境でのみ動作します。")
    print("  実資金は使用しません。")
    print()

    try:
        from src.config import load_config
        from src.paper_trade import PaperTradeManager

        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    pt_config = config.get("paper_trade", {})
    if not pt_config.get("enabled", False):
        print("[ERROR] ペーパートレードが無効です")
        print("  config.yaml で paper_trade.enabled を true にしてください")
        return 1

    manager = PaperTradeManager(config)

    if args.list_orders:
        orders = manager.list_orders(market="US")
        if orders:
            print("注文一覧:")
            for o in orders:
                print(f"  {o.order_id}: {o.code} {o.side} {o.quantity}株 @{o.price} ({o.status})")
        else:
            print("注文はありません")
        return 0

    if args.positions:
        print("ポジション一覧: 未実装")
        return 0

    if args.cancel:
        if not args.order_id:
            print("[ERROR] --order-id を指定してください")
            return 1
        cancelled = manager.cancel_order(args.order_id, market="US")
        if cancelled:
            print(f"[OK] キャンセル成功: {args.order_id}")
        else:
            print(f"[ERROR] キャンセル失敗: {args.order_id}")
        return 0

    if not args.code or not args.side or args.quantity is None or args.price is None:
        print("[ERROR] 注文には --code, --side, --quantity, --price が必要です")
        parser.print_help()
        return 1

    print("注文内容:")
    print(f"  銘柄: {args.code}")
    print(f"  方向: {args.side}")
    print(f"  数量: {args.quantity}")
    print(f"  価格: {args.price:,.2f}")
    print(f"  タイプ: {args.order_type}")
    print()

    confirm = input("この内容でペーパートレードを出しますか？ (y/N): ")
    if confirm.lower() != "y":
        print("キャンセルしました")
        return 0

    order = manager.place_order(
        code=args.code,
        side=args.side,
        quantity=args.quantity,
        price=args.price,
        order_type=args.order_type,
        market="US",
    )

    if order:
        print("\n[OK] 注文送信成功")
        print(f"  注文ID: {order.order_id}")
        print(f"  ステータス: {order.status}")
        return 0

    print("\n[ERROR] 注文送信失敗")
    return 1


if __name__ == "__main__":
    sys.exit(main())
