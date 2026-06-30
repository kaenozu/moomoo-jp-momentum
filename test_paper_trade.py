"""
ペーパートレード実機テストスクリプト

ファイルパス: test_paper_trade.py
何をするか: moomoo OpenAPIのSIMULATE環境でペーパートレードをテストする
なぜ存在するか: APIのペーパートレード機能を確認するため
関連ファイル: src/paper_trade.py, src/config.py

注意:
    - これはSIMULATE環境でのテストです
    - 実資金は使用しません
    - TrdEnv.REAL は使用しません
    - 成行注文は行いません
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.paper_trade import PaperTradeManager


def print_warning():
    """警告メッセージを表示する"""
    print()
    print("  これはmoomoo OpenAPIのSIMULATE環境を使ったペーパートレード検証です。")
    print("  実資金を使う注文ではありません。")
    print("  TrdEnv.REAL は使用しません。")
    print("  成行注文は行いません。")
    print()


def test_connection(manager: PaperTradeManager) -> bool:
    """接続確認テスト"""
    print("\n" + "=" * 60)
    print("[TEST 1] 接続確認")
    print("=" * 60)

    # 口座一覧取得
    accounts = manager.get_accounts()
    if not accounts:
        print("[FAIL] 口座が取得できませんでした")
        print("  OpenDが起動しているか確認してください")
        return False

    print(f"[OK] 口座取得成功: {len(accounts)}件")
    for acc in accounts:
        print(f"  アカウントID: {acc.get('acc_id')}")
        print(f"  環境: SIMULATE")

    return True


def test_order_possibility(manager: PaperTradeManager) -> dict:
    """注文可能性チェック"""
    print("\n" + "=" * 60)
    print("[TEST 2] 注文可能性チェック")
    print("=" * 60)

    # 現在値を取得
    from futu import OpenQuoteContext, RET_OK

    quote_ctx = OpenQuoteContext(
        host=manager.host,
        port=manager.port,
    )

    try:
        ret, data = quote_ctx.get_market_snapshot(["JP.7203"])
        if ret == RET_OK:
            current_price = data["last_price"].iloc[0]
            print(f"JP.7203 現在値: {current_price}")
        else:
            print(f"[WARN] 現在値取得失敗: {data}")
            current_price = 2700  # フォールバック
    finally:
        quote_ctx.close()

    # テスト用価格を計算（現在値の70%程度）
    test_price = int(current_price * 0.7)
    print(f"テスト用買い指値: {test_price}円（現在値の約70%）")

    params = {
        "market": "JP",
        "trd_env": "SIMULATE",
        "acc_id": accounts[0].get("acc_id") if accounts else "N/A",
        "code": "JP.7203",
        "quantity": 1,
        "price": test_price,
        "order_type": "LIMIT",
    }

    print("\n注文パラメータ:")
    for k, v in params.items():
        print(f"  {k}: {v}")

    return params


def test_limit_order(manager: PaperTradeManager, price: int) -> Optional[str]:
    """指値注文テスト"""
    print("\n" + "=" * 60)
    print("[TEST 3] 約定しにくい指値注文テスト")
    print("=" * 60)

    order = manager.place_order(
        code="JP.7203",
        side="BUY",
        quantity=1,
        price=price,
        order_type="LIMIT",
    )

    if order:
        print(f"[OK] 注文送信成功")
        print(f"  注文ID: {order.order_id}")
        print(f"  銘柄: {order.code}")
        print(f"  方向: {order.side}")
        print(f"  数量: {order.quantity}")
        print(f"  価格: {order.price}")
        print(f"  ステータス: {order.status}")
        return order.order_id
    else:
        print("[FAIL] 注文送信失敗")
        return None


def test_order_list(manager: PaperTradeManager) -> None:
    """注文一覧確認"""
    print("\n" + "=" * 60)
    print("[TEST 4] 注文一覧確認")
    print("=" * 60)

    orders = manager.list_orders()
    print(f"注文一覧: {len(orders)}件")

    for o in orders:
        print(f"  注文ID: {o.order_id}")
        print(f"  銘柄: {o.code}")
        print(f"  数量: {o.quantity}")
        print(f"  価格: {o.price}")
        print(f"  ステータス: {o.status}")


def test_cancel(manager: PaperTradeManager, order_id: str) -> bool:
    """キャンセル確認"""
    print("\n" + "=" * 60)
    print("[TEST 5] キャンセル確認")
    print("=" * 60)

    cancelled = manager.cancel_order(order_id)
    if cancelled:
        print(f"[OK] キャンセル成功: {order_id}")
    else:
        print(f"[FAIL] キャンセル失敗: {order_id}")

    return cancelled


def test_positions(manager: PaperTradeManager) -> None:
    """ポジション確認"""
    print("\n" + "=" * 60)
    print("[TEST 6] ポジション確認")
    print("=" * 60)

    positions = manager.list_positions()
    print(f"ポジション: {len(positions)}件")

    for p in positions:
        print(f"  銘柄: {p.code}")
        print(f"  数量: {p.quantity}")
        print(f"  取得単価: {p.cost_price}")


def test_guards(manager: PaperTradeManager) -> None:
    """ガードテスト"""
    print("\n" + "=" * 60)
    print("[TEST 7] ガードテスト")
    print("=" * 60)

    # 1. quantity上限超過
    print("\n--- quantity上限超過テスト ---")
    order = manager.place_order(
        code="JP.7203",
        side="BUY",
        quantity=100,  # max_order_quantity=10を超える
        price=2700,
        order_type="LIMIT",
    )
    if order is None:
        print("[OK] quantity上限で拒否されました")
    else:
        print("[FAIL] quantity上限が無視されました")

    # 2. 金額上限超過
    print("\n--- 金額上限超過テスト ---")
    order = manager.place_order(
        code="JP.7203",
        side="BUY",
        quantity=10,
        price=10000,  # 10*10000=100000 > max_order_amount=50000
        order_type="LIMIT",
    )
    if order is None:
        print("[OK] 金額上限で拒否されました")
    else:
        print("[FAIL] 金額上限が無視されました")

    # 3. 成行注文（無効な場合）
    print("\n--- 成行注文テスト（allow_market_order=false）---")
    order = manager.place_order(
        code="JP.7203",
        side="BUY",
        quantity=1,
        price=2700,
        order_type="MARKET",
    )
    if order is None:
        print("[OK] 成行注文が拒否されました")
    else:
        print("[FAIL] 成行注文が許可されました")


def main() -> int:
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description="Moomoo ペーパートレード実機テスト"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="接続確認のみ実行",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="設定ファイルパス",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("moomoo ペーパートレード実機テスト")
    print("=" * 60)
    print_warning()

    # 設定読み込み
    try:
        config = load_config(args.config)
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    # ペーパートレード設定確認
    pt_config = config.get("paper_trade", {})
    print(f"\nペーパートレード設定:")
    print(f"  enabled: {pt_config.get('enabled', False)}")
    print(f"  environment: {pt_config.get('environment', 'SIMULATE')}")
    print(f"  allow_market_order: {pt_config.get('allow_market_order', False)}")
    print(f"  max_order_quantity: {pt_config.get('max_order_quantity', 10)}")
    print(f"  max_order_amount: {pt_config.get('max_order_amount', 50000)}")

    if not pt_config.get("enabled", False):
        print("\n[ERROR] ペーパートレードが無効です")
        print("  config.yaml で paper_trade.enabled を true にしてください")
        return 1

    manager = PaperTradeManager(config)

    # 接続確認
    if not test_connection(manager):
        return 1

    if args.check_only:
        print("\n[OK] 接続確認のみ完了")
        return 0

    # 注文可能性チェック
    params = test_order_possibility(manager)

    # 指値注文テスト
    order_id = test_limit_order(manager, params["price"])

    if order_id:
        # 注文一覧確認
        test_order_list(manager)

        # キャンセル確認
        test_cancel(manager, order_id)

    # ポジション確認
    test_positions(manager)

    # ガードテスト
    test_guards(manager)

    print("\n" + "=" * 60)
    print("テスト完了")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
