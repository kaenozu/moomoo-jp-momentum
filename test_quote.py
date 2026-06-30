"""
相場データ取得テストスクリプト

ファイルパス: test_quote.py
何をするか: moomoo OpenDから相場データを取得し、SQLiteに保存する
なぜ存在するか: データ取得パイプラインの動作確認用
関連ファイル: src/quote_service.py, src/data_store.py, src/config.py

使い方:
    python test_quote.py
"""

import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from src.config import load_config
from src.connection import OpenDConnection
from src.data_store import DataStore
from src.models import DailyBar, Quote
from src.quote_service import QuoteService


def test_single_stock_snapshot(
    quote_service: QuoteService,
    data_store: DataStore,
) -> bool:
    """1銘柄のスナップショット取得テスト"""
    print("\n[TEST 1] 1銘柄のスナップショット取得")
    print("-" * 60)

    code = "JP.7203"  # トヨタ自動車
    print(f"銘柄: {code}")

    df = quote_service.get_stock_snapshot([code])

    if df.empty:
        print("[FAIL] データが取得できませんでした")
        return False

    print("[OK] スナップショット取得成功")
    print(f"  銘柄名: {df['name'].iloc[0]}")
    print(f"  現在値: {df['last_price'].iloc[0]}")
    print(f"  前日比: {df.get('price_spread', pd.Series([0])).iloc[0]}")
    print(f"  出来高: {df['volume'].iloc[0]}")
    print(f"  売買代金: {df['turnover'].iloc[0]}")

    # SQLiteに保存
    quote = quote_service.parse_snapshot_to_quote(df.iloc[0])
    data_store.save_quote(quote)
    print("[OK] SQLiteに保存しました")

    return True


def test_multiple_stock_snapshot(
    quote_service: QuoteService,
    data_store: DataStore,
) -> bool:
    """複数銘柄のスナップショット取得テスト"""
    print("\n[TEST 2] 複数銘柄のスナップショット取得")
    print("-" * 60)

    codes = [
        "JP.7203",   # トヨタ自動車
        "JP.6758",   # ソニーグループ
        "JP.6861",   # キーエンス
        "JP.8306",   # 三菱UFJフィナンシャル・グループ
        "JP.9984",   # ソフトバンクグループ
    ]
    print(f"銘柄数: {len(codes)}")

    df = quote_service.get_stock_snapshot(codes)

    if df.empty:
        print("[FAIL] データが取得できませんでした")
        return False

    print(f"[OK] スナップショット取得成功 ({len(df)}件)")
    for _, row in df.iterrows():
        print(
            f"  {row['code']}: {row['name']} "
            f"- {row['last_price']} "
            f"(出来高: {row['volume']})"
        )

    # SQLiteに保存
    for _, row in df.iterrows():
        quote = quote_service.parse_snapshot_to_quote(row)
        data_store.save_quote(quote)
    print("[OK] SQLiteに保存しました")

    return True


def test_daily_klines(
    quote_service: QuoteService,
    data_store: DataStore,
) -> bool:
    """日足データ取得テスト"""
    print("\n[TEST 3] 日足データ取得")
    print("-" * 60)

    code = "JP.7203"  # トヨタ自動車
    num = 30  # 直近30日
    print(f"銘柄: {code}, 取得本数: {num}")

    df = quote_service.get_daily_klines(code, num=num)

    if df.empty:
        print("[FAIL] データが取得できませんでした")
        return False

    print(f"[OK] 日足取得成功 ({len(df)}件)")
    print(f"  最新日付: {df['time_key'].iloc[0]}")
    print(f"  最新終値: {df['close'].iloc[0]}")
    print(f"  最古日付: {df['time_key'].iloc[-1]}")
    print(f"  最古終値: {df['close'].iloc[-1]}")

    # SQLiteに保存
    count = data_store.save_dataframe_to_daily_bars(df, code)
    print(f"[OK] SQLiteに保存しました ({count}件)")

    return True


def test_saved_data_verification(
    data_store: DataStore,
) -> bool:
    """SQLiteに保存したデータを検証するテスト"""
    print("\n[TEST 4] 保存データの検証")
    print("-" * 60)

    # quotesテーブルの確認
    import sqlite3
    with sqlite3.connect(data_store.db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM quotes")
        quote_count = cursor.fetchone()[0]

    print(f"  quotesテーブル: {quote_count}件")

    if quote_count == 0:
        print("[FAIL] quotesテーブルにデータがありません")
        return False

    # 最新のレコードを表示
    with sqlite3.connect(data_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM quotes ORDER BY timestamp DESC LIMIT 1"
        )
        row = cursor.fetchone()

    if row:
        print(f"  最新レコード:")
        print(f"    銘柄コード: {row['code']}")
        print(f"    タイムスタンプ: {row['timestamp']}")
        print(f"    現在値: {row['price']}")

    # daily_barsテーブルの確認
    with sqlite3.connect(data_store.db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM daily_bars")
        daily_count = cursor.fetchone()[0]

    print(f"  daily_barsテーブル: {daily_count}件")

    if daily_count > 0:
        with sqlite3.connect(data_store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM daily_bars ORDER BY date DESC LIMIT 1"
            )
            row = cursor.fetchone()

        if row:
            print(f"  最新日足:")
            print(f"    銘柄コード: {row['code']}")
            print(f"    日付: {row['date']}")
            print(f"    終値: {row['close']}")

    print("[OK] データの保存を確認しました")
    return True


def main() -> int:
    """メイン関数"""
    print("=" * 60)
    print("Moomoo 相場データ取得テスト")
    print("=" * 60)

    # 設定読み込み
    try:
        config = load_config("config.yaml")
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        print("\n対処法: config.example.yaml を config.yaml としてコピーしてください")
        return 1

    # データストア初期化
    data_store = DataStore(config)
    print(f"[OK] データベース初期化: {config.database_path}")

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

        # テスト実行
        quote_service = QuoteService(config, quote_ctx)

        results = []
        results.append(("1銘柄スナップショット", test_single_stock_snapshot(quote_service, data_store)))
        results.append(("複数銘柄スナップショット", test_multiple_stock_snapshot(quote_service, data_store)))
        results.append(("日足データ取得", test_daily_klines(quote_service, data_store)))
        results.append(("保存データ検証", test_saved_data_verification(data_store)))

        # 結果集計
        print("\n" + "=" * 60)
        print("テスト結果サマリー")
        print("=" * 60)

        all_passed = True
        for name, passed in results:
            status = "[PASS]" if passed else "[FAIL]"
            print(f"  {status} {name}")
            if not passed:
                all_passed = False

        if all_passed:
            print("\n[SUCCESS] すべてのテストが成功しました")
            return 0
        else:
            print("\n[WARNING] 一部のテストが失敗しました")
            return 1


if __name__ == "__main__":
    sys.exit(main())
