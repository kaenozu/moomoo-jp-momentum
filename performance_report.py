"""
パフォーマンスレポートスクリプト

ファイルパス: performance_report.py
何をするか: パフォーマンスレポートを出力する
なぜ存在するか: 戦術の有効性を検証するため
関連ファイル: src/performance.py, src.benchmark.py, src.config.py
"""

import argparse
import html
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from src.config import load_config
from src.performance import PerformanceEvaluator


def _fmt(value, pattern="{:,.0f}", na="N/A") -> str:
    return pattern.format(value) if value is not None else na


def display_summary(summary) -> None:
    """サマリーを表示する"""
    print("\n" + "=" * 60)
    print("ポートフォリオサマリー")
    print("=" * 60)
    print(f"  投資総額: {summary.total_invested:,.0f}円")
    print(f"  評価額: {summary.current_value:,.0f}円" if summary.current_value is not None else "  評価額: N/A")
    print(f"  実現損益: {summary.realized_pnl:,.0f}円")
    print(f"  未実現損益: {summary.unrealized_pnl:,.0f}円")
    print(f"  総損益: {summary.total_pnl:,.0f}円")

    print(f"\n  売買回数: {summary.win_count + summary.loss_count}回")
    print(f"  勝ち: {summary.win_count}回")
    print(f"  負け: {summary.loss_count}回")
    print(f"  勝率: {_fmt(summary.win_rate, '{:.1f}%')}")
    print(f"  平均利益: {_fmt(summary.avg_win)}円")
    print(f"  平均損失: {_fmt(summary.avg_loss)}円")
    print(f"  最大損失: {_fmt(summary.max_loss)}円")

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
        current = _fmt(p.current_price)
        pnl = _fmt(p.unrealized_pnl)
        ret = _fmt(p.unrealized_return, "{:.1f}")

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
        exit_p = _fmt(h.exit_price)
        pnl = _fmt(h.pnl)
        ret = _fmt(h.return_pct, "{:.1f}")
        days = str(h.holding_days) if h.holding_days is not None else "N/A"

        print(
            f"{h.code:<12} {name:<16} {h.quantity:>6} "
            f"{h.entry_price:>10,.0f} {exit_p:>10} {pnl:>10} {ret:>8} {days:>8}"
        )

    print("=" * 80)


def export_to_csv(positions, history, output_dir: str = "reports", date_str: str = "") -> str:
    """CSVに出力する"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filepath = Path(output_dir) / f"performance_{date_str}.csv"

    records = []

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

    pd.DataFrame(records).to_csv(filepath, index=False, encoding="utf-8-sig")
    return str(filepath)


def export_to_html(summary, positions, history, output_dir: str = "reports", date_str: str = "") -> str:
    """HTMLに出力する"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filepath = Path(output_dir) / f"performance_{date_str}.html"

    position_rows = []
    for p in positions:
        pnl_class = "positive" if p.unrealized_pnl is not None and p.unrealized_pnl >= 0 else "negative"
        position_rows.append(f"""
        <tr>
            <td>{html.escape(str(p.code))}</td><td>{html.escape(str(p.name or ""))}</td><td>{p.quantity}</td>
            <td>{p.avg_price:,.0f}</td><td>{html.escape(_fmt(p.current_price))}</td>
            <td class="{pnl_class}">{html.escape(_fmt(p.unrealized_pnl))}</td>
            <td>{html.escape(_fmt(p.unrealized_return, "{:.1f}"))}</td>
        </tr>
        """)

    history_rows = []
    for h in history:
        pnl_class = "positive" if h.pnl is not None and h.pnl >= 0 else "negative"
        history_rows.append(f"""
        <tr>
            <td>{html.escape(str(h.code))}</td><td>{html.escape(str(h.name or ""))}</td><td>{h.quantity}</td>
            <td>{h.entry_price:,.0f}</td><td>{html.escape(_fmt(h.exit_price))}</td>
            <td class="{pnl_class}">{html.escape(_fmt(h.pnl))}</td>
            <td>{html.escape(_fmt(h.return_pct, "{:.1f}"))}</td>
            <td>{html.escape(str(h.holding_days if h.holding_days is not None else "N/A"))}</td>
        </tr>
        """)

    win_rate_text = _fmt(summary.win_rate, "{:.1f}%")

    content = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>パフォーマンスレポート - {html.escape(date_str)}</title>
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
    <h1>パフォーマンスレポート - {html.escape(date_str)}</h1>

    <div class="summary">
        <h2>サマリー</h2>
        <p>投資総額: {summary.total_invested:,.0f}円</p>
        <p>総損益: <span class="{'positive' if summary.total_pnl >= 0 else 'negative'}">{summary.total_pnl:,.0f}円</span></p>
        <p>勝率: {html.escape(win_rate_text)} ({summary.win_count}勝 {summary.loss_count}敗)</p>
    </div>

    <h2>保有ポジション</h2>
    <table>
        <tr>
            <th>コード</th><th>銘柄名</th><th>数量</th>
            <th>平均取得価格</th><th>現在値</th><th>評価損益</th><th>リターン%</th>
        </tr>
        {''.join(position_rows)}
    </table>

    <h2>売買履歴</h2>
    <table>
        <tr>
            <th>コード</th><th>銘柄名</th><th>数量</th>
            <th>取得価格</th><th>売却価格</th><th>損益</th><th>リターン%</th><th>保有日数</th>
        </tr>
        {''.join(history_rows)}
    </table>

    <p style="margin-top: 20px; color: #666;">
        ※ このレポートは投資助言ではありません。検証結果の参考情報です。
    </p>
</body>
</html>
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return str(filepath)


def main() -> int:
    """メイン関数"""
    parser = argparse.ArgumentParser(description="Moomoo パフォーマンスレポート")
    parser.add_argument("--from", dest="from_date", help="開始日（YYYY-MM-DD）")
    parser.add_argument("--to", dest="to_date", help="終了日（YYYY-MM-DD）")
    parser.add_argument("--benchmark", default=None, help="比較ベンチマークコード")
    parser.add_argument("--csv", action="store_true", help="CSV出力")
    parser.add_argument("--html", action="store_true", help="HTML出力")
    parser.add_argument("--backtest", action="store_true", help="全シグナルの事後検証を実行")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo パフォーマンスレポート")
    print("=" * 60)

    try:
        config = load_config(args.config)
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    evaluator = PerformanceEvaluator(config)

    if args.backtest:
        print("\n" + "-" * 60)
        print("シグナル事後検証")
        print("-" * 60)

        results = evaluator.backtest_all_signals()
        print(f"[OK] 検証完了: {len(results)}件")

        if results:
            df = pd.DataFrame(results)
            print(df.to_string(index=False))

            date_str = datetime.now().strftime("%Y%m%d")
            output_dir = config.get("report.output_dir", "reports")
            csv_path = Path(output_dir) / f"signal_backtest_{date_str}.csv"
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            print(f"\n[OK] CSV出力: {csv_path}")

        return 0

    summary = evaluator.get_summary(
        benchmark_code=args.benchmark,
        start_date=args.from_date,
        end_date=args.to_date,
    )
    display_summary(summary)

    positions = evaluator.get_positions()
    display_positions(positions)

    history = evaluator.get_trade_history()
    display_history(history)

    date_str = datetime.now().strftime("%Y%m%d")
    output_dir = config.get("report.output_dir", "reports")

    if args.csv:
        csv_path = export_to_csv(positions, history, output_dir=output_dir, date_str=date_str)
        print(f"\n[OK] CSV出力: {csv_path}")

    if args.html:
        html_path = export_to_html(summary, positions, history, output_dir=output_dir, date_str=date_str)
        print(f"\n[OK] HTML出力: {html_path}")

    print("\n" + "=" * 60)
    print("完了")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
