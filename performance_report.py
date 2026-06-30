"""
パフォーマンスレポートスクリプト

ファイルパス: performance_report.py
何をするか: パフォーマンスレポートを出力する
なぜ存在するか: 戦術の有効性を検証するため
関連ファイル: src/performance.py, src/benchmark.py, src/config.py

使い方:
    python performance_report.py
    python performance_report.py --from 2026-06-01 --to 2026-06-30
    python performance_report.py --benchmark JP.2559
    python performance_report.py --csv --html
    python performance_report.py --backtest
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from src.config import load_config
from src.performance import PerformanceEvaluator


def display_summary(summary) -> None:
    """サマリーを表示する"""
    print("\n" + "=" * 60)
    print("ポートフォリオサマリー")
    print("=" * 60)

    print(f"  投資総額: {summary.total_invested:,.0f}円")
    if summary.current_value:
        print(f"  評価額: {summary.current_value:,.0f}円")
    print(f"  実現損益: {summary.realized_pnl:,.0f}円")
    print(f"  未実現損益: {summary.unrealized_pnl:,.0f}円")
    print(f"  総損益: {summary.total_pnl:,.0f}円")

    print(f"\n  売買回数: {summary.win_count + summary.loss_count}回")
    print(f"  勝ち: {summary.win_count}回")
    print(f"  負け: {summary.loss_count}回")
    if summary.win_rate is not None:
        print(f"  勝率: {summary.win_rate:.1f}%")
    if summary.avg_win is not None:
        print(f"  平均利益: {summary.avg_win:,.0f}円")
    if summary.avg_loss is not None:
        print(f"  平均損失: {summary.avg_loss:,.0f}円")
    if summary.max_loss is not None:
        print(f"  最大損失: {summary.max_loss:,.0f}円")

    if summary.benchmark_return is not None:
        print(f"\n  ベンチマークリターン: {summary.benchmark_return:.1f}%")
    if summary.excess_return is not None:
        print(f"  超過リターン: {summary.excess_return:.1f}%")

    print("=" * 60)


def display_positions(positions) -> None:
    """保有ポジションを表示する"""
    if not positions:
        print("\n保有ポジションはありません")
        return

    print("\n" + "=" * 80)
    print("保有ポジション一覧")
    print("=" * 80)
    print(
        f"{'コード':<12} {'銘柄名':<16} {'数量':>6} "
        f"{'平均取得価格':>12} {'現在値':>10} {'評価損益':>10} {'リターン%':>8}"
    )
    print("-" * 80)

    for p in positions:
        name = (p.name or "")[:8]
        current = f"{p.current_price:,.0f}" if p.current_price else "N/A"
        pnl = f"{p.unrealized_pnl:,.0f}" if p.unrealized_pnl else "N/A"
        ret = f"{p.unrealized_return:.1f}" if p.unrealized_return else "N/A"

        print(
            f"{p.code:<12} {name:<16} {p.quantity:>6} "
            f"{p.avg_price:>12,.0f} {current:>10} {pnl:>10} {ret:>8}"
        )

    print("=" * 80)


def display_history(history) -> None:
    """売買履歴を表示する"""
    if not history:
        print("\n売買履歴はありません")
        return

    print("\n" + "=" * 80)
    print("売買履歴")
    print("=" * 80)
    print(
        f"{'コード':<12} {'銘柄名':<16} {'数量':>6} "
        f"{'取得価格':>10} {'売却価格':>10} {'損益':>10} {'リターン%':>8} {'保有日数':>8}"
    )
    print("-" * 80)

    for h in history:
        name = (h.name or "")[:8]
        exit_p = f"{h.exit_price:,.0f}" if h.exit_price else "N/A"
        pnl = f"{h.pnl:,.0f}" if h.pnl else "N/A"
        ret = f"{h.return_pct:.1f}" if h.return_pct else "N/A"
        days = f"{h.holding_days}" if h.holding_days else "N/A"

        print(
            f"{h.code:<12} {name:<16} {h.quantity:>6} "
            f"{h.entry_price:>10,.0f} {exit_p:>10} {pnl:>10} {ret:>8} {days:>8}"
        )

    print("=" * 80)


def export_to_csv(
    positions,
    history,
    output_dir: str = "reports",
    date_str: str = "",
) -> str:
    """CSVに出力する"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filename = f"performance_{date_str}.csv"
    filepath = Path(output_dir) / filename

    records = []

    # ポジション
    for p in positions:
        records.append({
            "type": "POSITION",
            "code": p.code,
            "name": p.name,
            "quantity": p.quantity,
            "avg_price": p.avg_price,
            "current_price": p.current_price,
            "unrealized_pnl": p.unrealized_pnl,
            "unrealized_return": p.unrealized_return,
        })

    # 売買履歴
    for h in history:
        records.append({
            "type": "TRADE",
            "code": h.code,
            "name": h.name,
            "quantity": h.quantity,
            "entry_price": h.entry_price,
            "exit_price": h.exit_price,
            "pnl": h.pnl,
            "return_pct": h.return_pct,
            "holding_days": h.holding_days,
        })

    df = pd.DataFrame(records)
    df.to_csv(filepath, index=False, encoding="utf-8-sig")

    return str(filepath)


def export_to_html(
    summary,
    positions,
    history,
    output_dir: str = "reports",
    date_str: str = "",
) -> str:
    """HTMLに出力する"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filename = f"performance_{date_str}.html"
    filepath = Path(output_dir) / filename

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>パフォーマンスレポート - {date_str}</title>
    <style>
        body {{ font-family: sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #007bff; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .positive {{ color: #28a745; }}
        .negative {{ color: #dc3545; }}
        .summary {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; }}
    </style>
</head>
<body>
    <h1>パフォーマンスレポート - {date_str}</h1>

    <div class="summary">
        <h2>サマリー</h2>
        <p>投資総額: {summary.total_invested:,.0f}円</p>
        <p>総損益: <span class="{'positive' if summary.total_pnl >= 0 else 'negative'}">{summary.total_pnl:,.0f}円</span></p>
        <p>勝率: {summary.win_rate:.1f}% ({summary.win_count}勝 {summary.loss_count}敗)</p>
    </div>

    <h2>保有ポジション</h2>
    <table>
        <tr>
            <th>コード</th><th>銘柄名</th><th>数量</th>
            <th>平均取得価格</th><th>現在値</th><th>評価損益</th><th>リターン%</th>
        </tr>
"""

    for p in positions:
        name = p.name or ""
        current = f"{p.current_price:,.0f}" if p.current_price else "N/A"
        pnl_class = "positive" if p.unrealized_pnl and p.unrealized_pnl >= 0 else "negative"
        pnl = f"{p.unrealized_pnl:,.0f}" if p.unrealized_pnl else "N/A"
        ret = f"{p.unrealized_return:.1f}" if p.unrealized_return else "N/A"

        html += f"""
        <tr>
            <td>{p.code}</td><td>{name}</td><td>{p.quantity}</td>
            <td>{p.avg_price:,.0f}</td><td>{current}</td>
            <td class="{pnl_class}">{pnl}</td><td>{ret}</td>
        </tr>
"""

    html += """
    </table>

    <h2>売買履歴</h2>
    <table>
        <tr>
            <th>コード</th><th>銘柄名</th><th>数量</th>
            <th>取得価格</th><th>売却価格</th><th>損益</th><th>リターン%</th><th>保有日数</th>
        </tr>
"""

    for h in history:
        name = h.name or ""
        exit_p = f"{h.exit_price:,.0f}" if h.exit_price else "N/A"
        pnl_class = "positive" if h.pnl and h.pnl >= 0 else "negative"
        pnl = f"{h.pnl:,.0f}" if h.pnl else "N/A"
        ret = f"{h.return_pct:.1f}" if h.return_pct else "N/A"
        days = f"{h.holding_days}" if h.holding_days else "N/A"

        html += f"""
        <tr>
            <td>{h.code}</td><td>{name}</td><td>{h.quantity}</td>
            <td>{h.entry_price:,.0f}</td><td>{exit_p}</td>
            <td class="{pnl_class}">{pnl}</td><td>{ret}</td><td>{days}</td>
        </tr>
"""

    html += """
    </table>

    <p style="margin-top: 20px; color: #666;">
        ※ このレポートは投資助言ではありません。検証結果の参考情報です。
    </p>
</body>
</html>
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    return str(filepath)


def main() -> int:
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description="Moomoo パフォーマンスレポート"
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        help="開始日（YYYY-MM-DD）",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        help="終了日（YYYY-MM-DD）",
    )
    parser.add_argument(
        "--benchmark",
        default=None,
        help="比較ベンチマークコード",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="CSV出力",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="HTML出力",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="全シグナルの事後検証を実行",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="設定ファイルパス",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo パフォーマンスレポート")
    print("=" * 60)

    # 設定読み込み
    try:
        config = load_config(args.config)
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    evaluator = PerformanceEvaluator(config)

    # 事後検証
    if args.backtest:
        print("\n" + "-" * 60)
        print("シグナル事後検証")
        print("-" * 60)

        results = evaluator.backtest_all_signals()
        print(f"[OK] 検証完了: {len(results)}件")

        if results:
            df = pd.DataFrame(results)
            print(df.to_string(index=False))

            # CSV出力
            date_str = datetime.now().strftime("%Y%m%d")
            csv_path = f"reports/signal_backtest_{date_str}.csv"
            Path("reports").mkdir(parents=True, exist_ok=True)
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            print(f"\n[OK] CSV出力: {csv_path}")

        return 0

    # サマリー
    summary = evaluator.get_summary(
        benchmark_code=args.benchmark,
        start_date=args.from_date,
        end_date=args.to_date,
    )
    display_summary(summary)

    # 保有ポジション
    positions = evaluator.get_positions()
    display_positions(positions)

    # 売買履歴
    history = evaluator.get_trade_history()
    display_history(history)

    # CSV/HTML出力
    date_str = datetime.now().strftime("%Y%m%d")

    if args.csv:
        csv_path = export_to_csv(positions, history, date_str=date_str)
        print(f"\n[OK] CSV出力: {csv_path}")

    if args.html:
        html_path = export_to_html(summary, positions, history, date_str=date_str)
        print(f"\n[OK] HTML出力: {html_path}")

    print("\n" + "=" * 60)
    print("完了")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
