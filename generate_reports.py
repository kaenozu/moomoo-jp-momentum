"""
レポート生成スクリプト

ファイルパス: generate_reports.py
何をするか: 日次・週次レポートを一括生成する
なぜ存在するか: 複数のレポート出力をまとめて実行するため
関連ファイル: screen_candidates.py, performance_report.py, send_alerts.py

使い方:
    python generate_reports.py              # 日次レポート
    python generate_reports.py --weekly     # 週次レポート
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))


def run_command(cmd: list[str], name: str) -> bool:
    """
    コマンドを実行する

    Args:
        cmd: コマンドと引数
        name: ジョブ名

    Returns:
        bool: 成功ならTrue
    """
    print(f"\n{name}を実行中...")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            print(f"[OK] {name}完了")
            return True
        else:
            print(f"[ERROR] {name}失敗: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"[ERROR] {name}エラー: {e}")
        return False


def generate_daily_reports() -> dict:
    """日次レポートを生成する"""
    results = {}

    # 候補一覧
    results["candidates"] = run_command(
        [sys.executable, "screen_candidates.py", "--csv", "--html", "--save"],
        "候補一覧レポート",
    )

    # パフォーマンス
    results["performance"] = run_command(
        [sys.executable, "performance_report.py", "--csv", "--html"],
        "パフォーマンスレポート",
    )

    # アラート
    results["alerts"] = run_command(
        [sys.executable, "send_alerts.py"],
        "アラート送信",
    )

    return results


def generate_weekly_reports() -> dict:
    """週次レポートを生成する"""
    results = {}

    # 日次レポート（全含む）
    daily_results = generate_daily_reports()
    results.update(daily_results)

    # 事後検証
    results["backtest"] = run_command(
        [sys.executable, "performance_report.py", "--backtest", "--csv", "--html"],
        "シグナル事後検証",
    )

    return results


def main() -> int:
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description="Moomoo レポート生成"
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="週次レポートを生成",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo レポート生成")
    print("=" * 60)
    print(f"実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if args.weekly:
        print("\n週次レポートを生成します")
        results = generate_weekly_reports()
    else:
        print("\n日次レポートを生成します")
        results = generate_daily_reports()

    # 結果まとめ
    print("\n" + "=" * 60)
    print("レポート生成結果")
    print("=" * 60)

    all_success = True
    for name, success in results.items():
        status = "[OK]" if success else "[ERROR]"
        print(f"  {status} {name}")
        if not success:
            all_success = False

    if all_success:
        print("\n[SUCCESS] 全レポートが正常に生成されました")
    else:
        print("\n[WARNING] 一部のレポート生成が失敗しました")

    # 出力先ディレクトリ
    print(f"\n出力先: reports/")
    print(f"  - signals_YYYYMMDD.csv")
    print(f"  - signals_YYYYMMDD.html")
    print(f"  - performance_YYYYMMDD.csv")
    print(f"  - performance_YYYYMMDD.html")
    print(f"  - alerts_YYYYMMDD.txt")

    print("\n" + "=" * 60)
    print("完了")
    print("=" * 60)

    return 0 if all_success else 1


if __name__ == "__main__":
    sys.exit(main())
