"""
OpenD接続テストスクリプト

ファイルパス: test_connection.py
何をするか: moomoo OpenDへの接続をテストする
なぜ存在するか: 開発環境の動作確認用
関連ファイル: src/connection.py, src/config.py

使い方:
    python test_connection.py
"""

import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.connection import OpenDConnection


def main() -> int:
    """メイン関数"""
    print("=" * 60)
    print("Moomoo OpenD 接続テスト")
    print("=" * 60)

    # 設定読み込み
    try:
        config = load_config("config.yaml")
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        print("\n対処法: config.example.yaml を config.yaml としてコピーしてください")
        return 1

    # OpenD接続テスト
    print(f"\nOpenD接続先: {config.opend_host}:{config.opend_port}")
    print("-" * 60)

    with OpenDConnection(config) as conn:
        status = conn.connect()

        if status.connected:
            print(f"[OK] 接続成功: {status.message}")

            # 追加テスト: トヨタ自動車のスナップショット取得
            print("\n追加テスト: JP.7203（トヨタ自動車）のスナップショット取得")
            quote_ctx = conn.get_quote_context()
            if quote_ctx:
                from futu import RET_OK

                ret, data = quote_ctx.get_market_snapshot(["JP.7203"])
                if ret == RET_OK:
                    print("[OK] スナップショット取得成功")
                    print(f"  銘柄コード: {data['code'].iloc[0]}")
                    print(f"  銘柄名: {data['name'].iloc[0]}")
                    print(f"  現在値: {data['last_price'].iloc[0]}")
                    print(f"  出来高: {data['volume'].iloc[0]}")
                    print(f"  売買代金: {data['turnover'].iloc[0]}")
                else:
                    print(f"[WARN] スナップショット取得失敗: {data}")

            print("\n" + "=" * 60)
            print("接続テスト完了")
            print("=" * 60)
            return 0
        else:
            print(f"[ERROR] 接続失敗: {status.message}")
            if status.hint:
                print(f"\n{status.hint}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
