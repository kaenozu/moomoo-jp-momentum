"""
候補一覧表示スクリプト

ファイルパス: screen_candidates.py
何をするか: 売買候補の一覧を表示し、CSV/HTMLに出力する
なぜ存在するか: ユーザーに売買候補を提示するため
関連ファイル: src/screener.py, src/scoring.py, src/signals.py

使い方:
    python screen_candidates.py                    # 最新データで実行
    python screen_candidates.py --date 2026-06-30  # 指定日で実行
    python screen_candidates.py --csv              # CSV出力
    python screen_candidates.py --html             # HTML出力
    python screen_candidates.py --top 10           # 上位10件のみ表示
    python screen_candidates.py --save             # signalsテーブルに保存
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from src.config import load_config
from src.screener import Screener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def display_console(
    candidates: list,
    top: int | None = None,
) -> None:
    """
    コンソールに候補一覧を表示する

    Args:
        candidates: 候補リスト
        top: 表示件数（Noneなら全件）
    """
    if not candidates:
        print("候補がありません")
        return

    display_list = candidates[:top] if top else candidates

    print("\n" + "=" * 120)
    print("売買候補一覧")
    print("=" * 120)

    # ヘッダー
    header = (
        f"{'コード':<12} {'銘柄名':<16} {'終値':>10} {'日比%':>7} "
        f"{'MA5':>10} {'MA25':>10} {'出来高比':>8} {'5日リターン%':>10} "
        f"{'スコア':>6} {'シグナル':<12}"
    )
    print(header)
    print("-" * 120)

    # データ行
    for c in display_list:
        name = (c.name or "")[:8]  # 銘柄名を8文字に制限
        close_str = f"{c.close:,.0f}" if c.close else "N/A"
        daily_ret_str = f"{c.daily_return:.1f}" if c.daily_return is not None else "N/A"
        ma5_str = f"{c.ma5:,.0f}" if c.ma5 else "N/A"
        ma25_str = f"{c.ma25:,.0f}" if c.ma25 else "N/A"
        vol_ratio_str = f"{c.volume_ratio:.1f}" if c.volume_ratio else "N/A"
        ret5d_str = f"{c.return_5d:.1f}" if c.return_5d is not None else "N/A"
        score_str = f"{c.score:.0f}"

        # シグナル表示を変換
        signal_display = {
            "BUY_CANDIDATE": "買い候補",
            "WATCH": "監視",
            "EXCLUDE": "除外",
            "RISK_WARNING": "リスク警告",
        }.get(c.signal_type, c.signal_type)

        row = (
            f"{c.code:<12} {name:<16} {close_str:>10} {daily_ret_str:>7} "
            f"{ma5_str:>10} {ma25_str:>10} {vol_ratio_str:>8} {ret5d_str:>10} "
            f"{score_str:>6} {signal_display:<12}"
        )
        print(row)

        # リスク警告がある場合は表示
        if c.risk_warnings:
            print(f"  [!] {c.risk_warnings}")

    print("=" * 120)

    # サマリー
    buy_count = sum(1 for c in candidates if c.signal_type == "BUY_CANDIDATE")
    watch_count = sum(1 for c in candidates if c.signal_type == "WATCH")
    exclude_count = sum(1 for c in candidates if c.signal_type == "EXCLUDE")

    print(f"\nサマリー: 候補{buy_count}件 / 監視{watch_count}件 / 除外{exclude_count}件")

    if top and len(candidates) > top:
        print(f"  (全{len(candidates)}件中、上位{top}件を表示)")


def export_to_csv(
    candidates: list,
    output_dir: str = "reports",
    date: str | None = None,
) -> str:
    """
    候補一覧をCSVに出力する

    Args:
        candidates: 候補リスト
        output_dir: 出力ディレクトリ
        date: 基準日

    Returns:
        str: 出力ファイルパス
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    date_str = date or datetime.now().strftime("%Y-%m-%d")
    filename = f"signals_{date_str.replace('-', '')}.csv"
    filepath = Path(output_dir) / filename

    # DataFrameに変換
    records = []
    for c in candidates:
        records.append({
            "code": c.code,
            "name": c.name,
            "date": c.date,
            "close": c.close,
            "daily_return": c.daily_return,
            "ma5": c.ma5,
            "ma25": c.ma25,
            "high_20d": c.high_20d,
            "distance_from_high_20d": c.distance_from_high_20d,
            "volume_ratio": c.volume_ratio,
            "return_5d": c.return_5d,
            "turnover": c.turnover,
            "score": c.score,
            "signal_type": c.signal_type,
            "reason": c.reason,
            "risk_warnings": c.risk_warnings,
            "updated_at": c.updated_at,
        })

    df = pd.DataFrame(records)
    df.to_csv(filepath, index=False, encoding="utf-8-sig")

    logger.info(f"CSV出力完了: {filepath}")
    return str(filepath)


def export_to_html(
    candidates: list,
    output_dir: str = "reports",
    date: str | None = None,
) -> str:
    """
    候補一覧をHTMLに出力する

    Args:
        candidates: 候補リスト
        output_dir: 出力ディレクトリ
        date: 基準日

    Returns:
        str: 出力ファイルパス
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    date_str = date or datetime.now().strftime("%Y-%m-%d")
    filename = f"signals_{date_str.replace('-', '')}.html"
    filepath = Path(output_dir) / filename

    # HTML生成
    html_content = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>売買候補一覧 - {date_str}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #007bff;
            padding-bottom: 10px;
        }}
        .summary {{
            background-color: #fff;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background-color: #fff;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background-color: #007bff;
            color: white;
            position: sticky;
            top: 0;
        }}
        tr:hover {{
            background-color: #f5f5f5;
        }}
        .buy-candidate {{
            background-color: #d4edda;
        }}
        .watch {{
            background-color: #fff3cd;
        }}
        .exclude {{
            background-color: #f8d7da;
        }}
        .risk-warning {{
            color: #dc3545;
            font-weight: bold;
        }}
        .score {{
            font-weight: bold;
            font-size: 1.1em;
        }}
        .reason {{
            font-size: 0.9em;
            color: #666;
        }}
    </style>
</head>
<body>
    <h1>売買候補一覧 - {date_str}</h1>

    <div class="summary">
        <strong>サマリー:</strong>
        候補 {sum(1 for c in candidates if c.signal_type == 'BUY_CANDIDATE')}件 /
        監視 {sum(1 for c in candidates if c.signal_type == 'WATCH')}件 /
        除外 {sum(1 for c in candidates if c.signal_type == 'EXCLUDE')}件
    </div>

    <table>
        <thead>
            <tr>
                <th>コード</th>
                <th>銘柄名</th>
                <th>終値</th>
                <th>日比%</th>
                <th>MA5</th>
                <th>MA25</th>
                <th>出来高比</th>
                <th>5日リターン%</th>
                <th>スコア</th>
                <th>シグナル</th>
                <th>判定理由</th>
            </tr>
        </thead>
        <tbody>
"""

    for c in candidates:
        row_class = {
            "BUY_CANDIDATE": "buy-candidate",
            "WATCH": "watch",
            "EXCLUDE": "exclude",
        }.get(c.signal_type, "")

        signal_display = {
            "BUY_CANDIDATE": "買い候補",
            "WATCH": "監視",
            "EXCLUDE": "除外",
            "RISK_WARNING": "リスク警告",
        }.get(c.signal_type, c.signal_type)

        close_str = f"{c.close:,.0f}" if c.close else "N/A"
        daily_ret_str = f"{c.daily_return:.1f}" if c.daily_return is not None else "N/A"
        ma5_str = f"{c.ma5:,.0f}" if c.ma5 else "N/A"
        ma25_str = f"{c.ma25:,.0f}" if c.ma25 else "N/A"
        vol_ratio_str = f"{c.volume_ratio:.1f}" if c.volume_ratio else "N/A"
        ret5d_str = f"{c.return_5d:.1f}" if c.return_5d is not None else "N/A"

        risk_html = ""
        if c.risk_warnings:
            risk_html = f'<span class="risk-warning">⚠ {c.risk_warnings}</span>'

        html_content += f"""
            <tr class="{row_class}">
                <td>{c.code}</td>
                <td>{c.name or ''}</td>
                <td>{close_str}</td>
                <td>{daily_ret_str}</td>
                <td>{ma5_str}</td>
                <td>{ma25_str}</td>
                <td>{vol_ratio_str}</td>
                <td>{ret5d_str}</td>
                <td class="score">{c.score:.0f}</td>
                <td>{signal_display}</td>
                <td class="reason">{c.reason} {risk_html}</td>
            </tr>
"""

    html_content += """
        </tbody>
    </table>

    <p style="margin-top: 20px; color: #666; font-size: 0.9em;">
        ※ この一覧は投資助言ではありません。売買候補の参考情報としてご活用ください。
        最終的な投資判断はご自身の責任でお願いします。
    </p>
</body>
</html>
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    logger.info(f"HTML出力完了: {filepath}")
    return str(filepath)


def main() -> int:
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description="Moomoo 売買候補一覧表示"
    )
    parser.add_argument(
        "--date",
        default=None,
        help="基準日（YYYY-MM-DD形式）。未指定なら最新",
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
        "--top",
        type=int,
        default=None,
        help="表示件数（上位N件）",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="signalsテーブルに保存",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="設定ファイルパス",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="古いデータでも出力する",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo 売買候補一覧")
    print("=" * 60)

    # 設定読み込み
    try:
        config = load_config(args.config)
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    # データ鮮度チェック
    from src.data_freshness import DataFreshnessGuard
    guard = DataFreshnessGuard(config)

    try:
        guard.assert_fresh_data_or_stop(
            allow_stale=args.allow_stale,
            table_name="daily_bars",
        )
    except SystemError as e:
        print(f"[ERROR] {e}")
        return 1

    # スクリーナー初期化
    screener = Screener(config)

    # 候補スクリーニング
    print(f"\n基準日: {args.date or '最新'}")
    candidates = screener.screen_candidates(date=args.date)

    if not candidates:
        print("[WARNING] 候補がありません")
        print("  まず daily_update.py を実行してデータを取得してください")
        return 0

    print(f"[OK] スクリーニング完了: {len(candidates)}銘柄")

    # コンソール表示
    display_console(candidates, top=args.top)

    # CSV出力
    if args.csv:
        csv_path = export_to_csv(candidates, date=args.date)
        print(f"\n[OK] CSV出力: {csv_path}")

    # HTML出力
    if args.html:
        html_path = export_to_html(candidates, date=args.date)
        print(f"\n[OK] HTML出力: {html_path}")

    # signalsテーブルに保存
    if args.save:
        save_count = screener.save_signals_to_db(candidates)
        print(f"\n[OK] signalsテーブルに保存: {save_count}件")

    print("\n" + "=" * 60)
    print("完了")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
