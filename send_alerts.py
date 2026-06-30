"""
アラート送信スクリプト

ファイルパス: send_alerts.py
何をするか: アラートチェックを実行し、通知を送信する
なぜ存在するか: 重要なイベントをユーザーに通知するため
関連ファイル: src/alerts.py, src/config.py
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.alerts import AlertManager
from src.config import load_config


def main() -> int:
    """メイン関数"""
    parser = argparse.ArgumentParser(description="Moomoo アラート送信")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo アラート送信")
    print("=" * 60)

    try:
        config = load_config(args.config)
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    alert_manager = AlertManager(config)

    if not alert_manager.enabled:
        print("[INFO] alerts.enabled が false です。アラート送信をスキップします。")
        return 0

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
