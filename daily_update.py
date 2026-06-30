"""
日次更新スクリプト

ファイルパス: daily_update.py
何をするか: 全銘柄の日足データを取得し、指標を計算し、SQLiteとCSVに出力する
なぜ存在するか: 日次のデータ更新と指標計算を一括実行するため
関連ファイル: src/quote_service.py, src/indicators.py, src/data_store.py

使い方:
    python daily_update.py              # 全銘柄を更新
    python daily_update.py --force      # 強制再取得（スキップしない）
    python daily_update.py --dry-run    # テスト実行（API呼び出しなし）

注意:
    - 日足更新は大引け後に実行するのが自然です
    - 取引時間外でも実行可能です
    - 最新営業日のデータが取得済みならスキップします
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
from src.connection import OpenDConnection
from src.data_store import DataStore
from src.indicators import calculate_indicators_batch, indicators_to_dataframe
from src.quote_service import QuoteService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_latest_bar_date(data_store: DataStore, code: str) -> str | None:
    """
    銘柄の最新日足の日付を取得する

    Args:
        data_store: データストア
        code: 銘柄コード

    Returns:
        str | None: 最新日付（YYYY-MM-DD）。データがない場合はNone
    """
    import sqlite3

    with sqlite3.connect(data_store.db_path) as conn:
        cursor = conn.execute(
            "SELECT MAX(date) FROM daily_bars WHERE code = ?",
            (code,),
        )
        result = cursor.fetchone()
        return result[0] if result and result[0] else None


def should_skip_fetch(
    data_store: DataStore,
    code: str,
    today: str,
) -> bool:
    """
    スキップすべきかを判定する

    判定基準:
    - 最新の日足日付が今日（または直近営業日）ならスキップ
    - 取引時間内なら、まだ今日のデータが確定していない可能性があるので取得する

    Args:
        data_store: データストア
        code: 銘柄コード
        today: 今日の日付（YYYY-MM-DD）

    Returns:
        bool: スキップすべきならTrue
    """
    latest_date = get_latest_bar_date(data_store, code)

    if latest_date is None:
        # データがないので取得する
        return False

    # 最新日付が今日ならスキップ
    if latest_date >= today:
        return True

    # 最新日付が直近営業日（今日が土日の場合）ならスキップ
    # 簡易判定: 今日が土日なら最新日付が金曜日ならスキップ
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    weekday = today_dt.weekday()

    if weekday == 5:  # 土曜日
        # 最新日付が金曜日ならスキップ
        if latest_date >= (today_dt - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d"):
            return True
    elif weekday == 6:  # 日曜日
        # 最新日付が金曜日ならスキップ
        if latest_date >= (today_dt - __import__("datetime").timedelta(days=2)).strftime("%Y-%m-%d"):
            return True

    return False


def fetch_and_save_daily_klines(
    quote_service: QuoteService,
    data_store: DataStore,
    codes: list[str],
    num_days: int = 120,
    force: bool = False,
) -> dict[str, pd.DataFrame]:
    """
    複数銘柄の日足データを取得し、SQLiteに保存する

    優先順位:
    1. get_cur_kline（直近30日）
    2. request_history_kline（start指定付き）

    Args:
        quote_service: 相場サービス
        data_store: データストア
        codes: 銘柄コードのリスト
        num_days: 取得日数（デフォルト120: 約6営業月）
        force: 強制再取得

    Returns:
        dict[str, pd.DataFrame]: {銘柄コード: 日足DataFrame}
    """
    today = datetime.now().strftime("%Y-%m-%d")
    data_dict = {}

    for i, code in enumerate(codes, 1):
        logger.info(f"[{i}/{len(codes)}] {code} の日足を取得中...")

        # スキップ判定
        if not force and should_skip_fetch(data_store, code, today):
            logger.info(f"  スキップ: 最新データは取得済み")
            # DBから読み込み
            df = data_store.get_daily_bars(code, limit=num_days)
            if not df.empty:
                data_dict[code] = df
            continue

        # フォールバック付きで取得（get_cur_kline優先）
        df = quote_service.get_daily_klines_with_fallback(
            code, num=num_days, start="2025-01-01"
        )

        if df.empty:
            logger.warning(f"  データ取得失敗: {code}")
            continue

        # SQLiteに保存
        count = data_store.save_dataframe_to_daily_bars(df, code)
        logger.info(f"  保存完了: {count}件")

        data_dict[code] = df

    return data_dict


def save_indicators_to_db(
    data_store: DataStore,
    indicators_df: pd.DataFrame,
) -> int:
    """
    指標結果をSQLiteに保存する

    Args:
        data_store: データストア
        indicators_df: 指標DataFrame

    Returns:
        int: 保存件数
    """
    import sqlite3

    count = 0
    with sqlite3.connect(data_store.db_path) as conn:
        for _, row in indicators_df.iterrows():
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO indicators
                    (code, date, close, volume, turnover, daily_return,
                     ma5, ma25, high_20d, distance_from_high_20d,
                     volume_ma20, volume_ratio, return_5d, history_days, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("code"),
                        row.get("date"),
                        row.get("close"),
                        row.get("volume"),
                        row.get("turnover"),
                        row.get("daily_return"),
                        row.get("ma5"),
                        row.get("ma25"),
                        row.get("high_20d"),
                        row.get("high_20d_distance"),
                        row.get("volume_ma20"),
                        row.get("volume_ratio"),
                        row.get("return_5d"),
                        row.get("history_days"),
                        datetime.now().isoformat(),
                    ),
                )
                count += 1
            except sqlite3.Error as e:
                logger.error(
                    f"指標保存エラー: {row.get('code')} - {e}"
                )

    return count


def export_to_csv(
    indicators_df: pd.DataFrame,
    output_dir: str = "reports",
) -> str:
    """
    指標結果をCSVに出力する

    Args:
        indicators_df: 指標DataFrame
        output_dir: 出力ディレクトリ

    Returns:
        str: 出力ファイルパス
    """
    # 出力ディレクトリ作成
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # ファイル名生成
    today = datetime.now().strftime("%Y%m%d")
    filename = f"indicators_{today}.csv"
    filepath = Path(output_dir) / filename

    # CSV出力
    indicators_df.to_csv(filepath, index=False, encoding="utf-8-sig")
    logger.info(f"CSV出力完了: {filepath}")

    return str(filepath)


def main() -> int:
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description="Moomoo 日次更新スクリプト"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="強制再取得（スキップしない）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="テスト実行（API呼び出しなし）",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="設定ファイルパス（デフォルト: config.yaml）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Moomoo 日次更新")
    print("=" * 60)

    # 設定読み込み
    try:
        config = load_config(args.config)
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    # データストア初期化
    data_store = DataStore(config)
    print(f"[OK] データベース: {config.database_path}")

    # 銘柄リスト読み込み
    symbols = data_store.get_enabled_symbols()
    codes = [s.code for s in symbols]
    symbols_info = {s.code: s.name for s in symbols}
    print(f"[OK] 銘柄数: {len(codes)}")

    if not codes:
        print("[ERROR] 銘柄リストが空です")
        print("  data/symbols.json を確認してください")
        return 1

    if args.dry_run:
        print("\n[DRY-RUN] テスト実行（API呼び出しなし）")
        print(f"  対象銘柄: {codes[:5]}...")
        return 0

    # OpenD接続
    print(f"\nOpenD接続先: {config.opend_host}:{config.opend_port}")

    with OpenDConnection(config) as conn:
        status = conn.connect()

        if not status.connected:
            print(f"[ERROR] 接続失敗: {status.message}")
            if status.hint:
                print(f"\n{status.hint}")
            return 1

        print("[OK] OpenD接続成功")

        quote_ctx = conn.get_quote_context()
        if not quote_ctx:
            print("[ERROR] 行情コンテキストが取得できません")
            return 1

        quote_service = QuoteService(config, quote_ctx)

        # 日足データ取得・保存
        print("\n" + "-" * 60)
        print("日足データ取得・保存")
        print("-" * 60)

        data_dict = fetch_and_save_daily_klines(
            quote_service,
            data_store,
            codes,
            num_days=120,
            force=args.force,
        )

        if not data_dict:
            print("[ERROR] データが取得できませんでした")
            return 1

        print(f"\n[OK] 取得完了: {len(data_dict)}/{len(codes)}銘柄")

        # 指標計算
        print("\n" + "-" * 60)
        print("指標計算")
        print("-" * 60)

        indicators = calculate_indicators_batch(data_dict, symbols_info)
        indicators_df = indicators_to_dataframe(indicators)

        if indicators_df.empty:
            print("[ERROR] 指標計算結果が空です")
            return 1

        print(f"[OK] 指標計算完了: {len(indicators_df)}銘柄")

        # SQLiteに指標を保存
        print("\n" + "-" * 60)
        print("指標をSQLiteに保存")
        print("-" * 60)

        ind_count = save_indicators_to_db(data_store, indicators_df)
        print(f"[OK] 指標保存完了: {ind_count}件")

        # CSV出力
        print("\n" + "-" * 60)
        print("CSV出力")
        print("-" * 60)

        output_dir = config.get("report.output_dir", "reports")
        csv_path = export_to_csv(indicators_df, output_dir)
        print(f"[OK] CSV出力: {csv_path}")

        # サンプル出力
        print("\n" + "-" * 60)
        print("サンプル出力（先頭5銘柄）")
        print("-" * 60)

        display_cols = [
            "code", "name", "close", "daily_return",
            "ma5", "ma25", "volume_ratio", "return_5d",
        ]
        available_cols = [c for c in display_cols if c in indicators_df.columns]
        print(indicators_df[available_cols].head().to_string(index=False))

        print("\n" + "=" * 60)
        print("日次更新完了")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
