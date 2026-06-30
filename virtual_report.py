"""
仮想トレードレポートCLI

ファイルパス: virtual_report.py
何をするか: 仮想トレードの成績レポートを出力する
なぜ存在するか: 戦略の有効性を検証するため
関連ファイル: src/virtual_report.py, src/config.py

使い方:
    python virtual_report.py
    python virtual_report.py --strategy default
    python virtual_report.py --csv --html
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.virtual_report import VirtualReportGenerator


def display_report(report):
    print("\n" + "=" * 60)
    print("仮想トレードレポート")
    print("=" * 60)
    print(f"初期資金: {report.initial_cash:,.0f}円")
    print(f"最終資産: {report.final_total_equity:,.0f}円")
    print(f"総リターン: {report.total_return_pct:.2f}%")
    print(f"実現損益: {report.realized_pl:,.0f}円")
    print(f"未実現損益: {report.unrealized_pl:,.0f}円")
    print()
    if report.benchmark_2559_return is not None:
        print(f"2559リターン: {report.benchmark_2559_return:.2f}%")
        print(f"2559超過リターン: {report.excess_vs_2559:.2f}%")
    if report.benchmark_1306_return is not None:
        print(f"1306リターン: {report.benchmark_1306_return:.2f}%")
        print(f"1306超過リターン: {report.excess_vs_1306:.2f}%")
    print()
    print(f"クローズドトレード: {report.closed_trade_count}件")
    print(f"勝率: {report.win_rate:.1f}%" if report.win_rate is not None else "勝率: N/A")
    print(f"平均利益: {report.avg_win:,.0f}円" if report.avg_win is not None else "平均利益: N/A")
    print(f"平均損失: {report.avg_loss:,.0f}円" if report.avg_loss is not None else "平均損失: N/A")
    print(f"プロフィットファクター: {report.profit_factor:.2f}" if report.profit_factor is not None else "PF: N/A")
    print(f"最大ドローダウン: {report.max_drawdown_pct:.2f}%" if report.max_drawdown_pct is not None else "MDD: N/A")
    print(f"平均保有日数: {report.avg_holding_days:.1f}日" if report.avg_holding_days is not None else "平均保有日数: N/A")

    if report.exit_reason_stats:
        print("\n--- exit_reason別 ---")
        for s in report.exit_reason_stats:
            print(f"  {s.exit_reason}: {s.count}件 (勝率{s.win_rate:.0f}%, PL{s.realized_pl:+,.0f}円)")


def export_csv(report, output_dir="reports", date_str=""):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    gen = VirtualReportGenerator(load_config("config.yaml"))
    df = gen.to_dataframe(report)
    path = Path(output_dir) / f"virtual_report_{date_str}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[OK] CSV: {path}")

    if report.exit_reason_stats:
        df2 = gen.exit_reason_to_dataframe(report)
        path2 = Path(output_dir) / f"virtual_exit_reason_{date_str}.csv"
        df2.to_csv(path2, index=False, encoding="utf-8-sig")
        print(f"[OK] CSV: {path2}")

    if report.closed_trades:
        df3 = gen.closed_trades_to_dataframe(report)
        path3 = Path(output_dir) / f"virtual_closed_trades_{date_str}.csv"
        df3.to_csv(path3, index=False, encoding="utf-8-sig")
        print(f"[OK] CSV: {path3}")


def export_html(report, output_dir="reports", date_str=""):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    path = Path(output_dir) / f"virtual_report_{date_str}.html"
    lines = [f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<title>仮想トレードレポート {date_str}</title>
<style>
body{{font-family:sans-serif;margin:20px;background:#f5f5f5}}
h1{{color:#333;border-bottom:2px solid #007bff;padding-bottom:10px}}
table{{border-collapse:collapse;width:100%;background:#fff;margin:10px 0}}
th,td{{border:1px solid #ddd;padding:8px;text-align:left}}
th{{background:#007bff;color:#fff}}
.positive{{color:#28a745}}
.negative{{color:#dc3545}}
.card{{background:#fff;padding:15px;border-radius:5px;margin:10px 0}}
</style></head><body>
<h1>仮想トレードレポート</h1>
<p>作成日: {date_str}</p>
<div class="card">
<h2>サマリー</h2>
<table>
<tr><th>項目</th><th>値</th></tr>
<tr><td>初期資金</td><td>{report.initial_cash:,.0f}円</td></tr>
<tr><td>最終資産</td><td>{report.final_total_equity:,.0f}円</td></tr>
<tr><td>総リターン</td><td class="{'positive' if report.total_return_pct>=0 else 'negative'}">{report.total_return_pct:.2f}%</td></tr>
<tr><td>実現損益</td><td class="{'positive' if report.realized_pl>=0 else 'negative'}">{report.realized_pl:,.0f}円</td></tr>
<tr><td>勝率</td><td>{report.win_rate:.1f}%</td></tr>
<tr><td>PF</td><td>{report.profit_factor:.2f}</td></tr>
<tr><td>MDD</td><td class="negative">{report.max_drawdown_pct:.2f}%</td></tr>
"""]
    if report.excess_vs_2559 is not None:
        lines.append(f'<tr><td>2559超過リターン</td><td class="{"positive" if report.excess_vs_2559>=0 else "negative"}">{report.excess_vs_2559:+.2f}%</td></tr>')
    if report.excess_vs_1306 is not None:
        lines.append(f'<tr><td>1306超過リターン</td><td class="{"positive" if report.excess_vs_1306>=0 else "negative"}">{report.excess_vs_1306:+.2f}%</td></tr>')
    lines.append('</table></div>')

    if report.exit_reason_stats:
        lines.append('<h2>exit_reason別</h2><table><tr><th>理由</th><th>件数</th><th>勝率</th><th>損益</th></tr>')
        for s in report.exit_reason_stats:
            lines.append(f'<tr><td>{s.exit_reason}</td><td>{s.count}</td><td>{s.win_rate:.0f}%</td><td class="{"positive" if s.realized_pl>=0 else "negative"}">{s.realized_pl:+,.0f}円</td></tr>')
        lines.append('</table>')

    lines.append('<p style="margin-top:20px;color:#666;font-size:0.9em">※ これは検証用レポートです。投資助言ではありません。</p>')
    lines.append('</body></html>')
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] HTML: {path}")


def main():
    parser = argparse.ArgumentParser(description="Moomoo 仮想トレードレポート")
    parser.add_argument("--strategy", default="default")
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--csv", action="store_true")
    parser.add_argument("--html", action="store_true")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    gen = VirtualReportGenerator(config)
    report = gen.generate(args.strategy, args.from_date, args.to_date)
    display_report(report)

    date_str = datetime.now().strftime("%Y%m%d")
    if args.csv:
        export_csv(report, date_str=date_str)
    if args.html:
        export_html(report, date_str=date_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
