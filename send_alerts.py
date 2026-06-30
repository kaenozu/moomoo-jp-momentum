"""
アラート送信スクリプト

ファイルパス: send_alerts.py
何をするか: アラートチェックを実行し、通知を送信する
なぜ存在するか: 重要なイベントをユーザーに通知するため
関連ファイル: src/alerts.py, src/config.py

使い方:
    python send_alerts.py
"""

import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.alerts import AlertManager


def main() -> int:
    """メイン関数"""
    print("=" * 60)
    print("Moomoo アラート送信")
    print("=" * 60)

    # 設定読み込み
    try:
        config = load_config("config.yaml")
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    # アラートマネージャー初期化
    alert_manager = AlertManager(config)

    # アラートチェック実行
    print("\nアラートチェックを実行中...")
    sent_alerts = alert_manager.run_all_checks()

    if sent_alerts:
        print(f"\n[OK] {len(sent_alerts)}件のアラートを送信しました")
        for alert in sent_alerts:
            print(f"  - {alert.alert_type}: {alert.code}")
    else:
        print("\n[OK] 新規アラートはありません")

    print("\n" + "=" * 60)
    print("完了")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
