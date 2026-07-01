"""
戦略比較CLI

ファイルパス: strategy_compare.py
何をするか: 複数戦略の成績を比較表示する
なぜ存在するか: どの戦略が2559/1306に対して優位かを比較するため
関連ファイル: src/strategy_runner.py, src/virtual_trade.py, src/virtual_report.py

使い方:
    python strategy_compare.py
    python strategy_compare.py --from 2026-07-01 --to 2026-07-31
    python strategy_compare.py --csv --html
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from src.config import load_config
from src.virtual_report import VirtualReportGenerator
from src.strategies import StrategyRegistry


def compare_strategies(
    config,
    from_date: str = "",
    to_date: str = "",
) -> list[dict]:
    """全戦略を比較"""
    gen = VirtualReportGenerator(config)
    results = []

    for name in StrategyRegistry.list_names():
        report = gen.generate(name, from_date if from_date else None, to_date if to_date else None)
        results.append({
            "strategy": name,
            "total_return_pct": report.total_return_pct,
            "excess_vs_2559": report.excess_vs_2559,
            "excess_vs_1306": report.excess_vs_1306,
            "max_drawdown_pct": report.max_drawdown_pct,
            "win_rate": report.win_rate,
            "profit_factor": report.profit_factor,
            "trade_count": report.closed_trade_count,
            "avg_holding_days": report.avg_holding_days,
            "realized_pl": report.realized_pl,
        })

    return results


def display_table(results: list[dict]):
    """結果を表形式で表示"""
    print()
    print("=" * 100)
    print("戦略比較")
    print("=" * 100)
    header = f"{'戦略':<18} {'リターン%':>10} {'2559超過%':>10} {'1306超過%':>10} {'MDD%':>8} {'勝率%':>7} {'PF':>7} {'トレード':>8} {'保有日数':>8}"
    print(header)
    print("-" * 100)
    for r in results:
        print(
            f"{r['strategy']:<18} {r['total_return_pct']:>9.2f}% "
            f"{r['excess_vs_2559'] if r['excess_vs_2559'] is not None else 'N/A':>10} "
            f"{r['excess_vs_1306'] if r['excess_vs_1306'] is not None else 'N/A':>10} "
            f"{r['max_drawdown_pct'] if r['max_drawdown_pct'] is not None else 0:>7.2f}% "
            f"{r['win_rate'] if r['win_rate'] is not None else 0:>6.1f}% "
            f"{r['profit_factor'] if r['profit_factor'] is not None else 0:>6.2f} "
            f"{r['trade_count']:>8} "
            f"{r['avg_holding_days'] if r['avg_holding_days'] is not None else 0:>7.1f}"
        )
    print("=" * 100)


def export_csv(results: list[dict], output_dir: str = "reports", date_str: str = ""):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(results)
    path = Path(output_dir) / f"strategy_compare_{date_str}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[OK] CSV: {path}")


def export_html(results: list[dict], output_dir: str = "reports", date_str: str = ""):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    path = Path(output_dir) / f"strategy_compare_{date_str}.html"
    lines = ["""
<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<title>戦略比較</title>
<style>
body{font-family:sans-serif;margin:20px}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #ddd;padding:8px;text-align:left}
th{background:#007bff;color:#fff}
.positive{color:#28a745}.negative{color:#dc3545}
</style></head><body>
<h1>戦略比較</h1>
<table>
<tr><th>戦略</th><th>リターン</th><th>2559超過</th><th>1306超過</th><th>MDD</th><th>勝率</th><th>PF</th><th>トレード</th></tr>
"""]
    for r in results:
        ret = f'<td class="{"positive" if r["total_return_pct"]>=0 else "negative"}">{r["total_return_pct"]:.2f}%</td>'
        exc2559 = f'<td class="{"positive" if (r["excess_vs_2559"] or 0)>=0 else "negative"}">{r["excess_vs_2559"] if r["excess_vs_2559"] is not None else "N/A"}</td>'
        exc1306 = f'<td class="{"positive" if (r["excess_vs_1306"] or 0)>=0 else "negative"}">{r["excess_vs_1306"] if r["excess_vs_1306"] is not None else "N/A"}</td>'
        lines.append(f'<tr><td>{r["strategy"]}</td>{ret}{exc2559}{exc1306}<td>{r["max_drawdown_pct"]:.2f}%</td><td>{r["win_rate"]:.1f}%</td><td>{r["profit_factor"]:.2f}</td><td>{r["trade_count"]}</td></tr>')
    lines.append('</table><p style="margin-top:20px;color:#666;font-size:0.9em">※ 検証用レポートです。投資助言ではありません。</p></body></html>')
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] HTML: {path}")


def main():
    parser = argparse.ArgumentParser(description="Moomoo 戦略比較")
    parser.add_argument("--from", dest="from_date", help="開始日")
    parser.add_argument("--to", dest="to_date", help="終了日")
    parser.add_argument("--csv", action="store_true")
    parser.add_argument("--html", action="store_true")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    results = compare_strategies(config, args.from_date, args.to_date)
    display_table(results)

    date_str = datetime.now().strftime("%Y%m%d")
    if args.csv:
        export_csv(results, date_str=date_str)
    if args.html:
        export_html(results, date_str=date_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
