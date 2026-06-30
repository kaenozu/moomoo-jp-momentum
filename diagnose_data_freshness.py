"""
データ鮮度診断スクリプト

ファイルパス: diagnose_data_freshness.py
何をするか: moomoo APIから取得できるデータの鮮度を診断する
なぜ存在するか: 日足データが古い問題の原因を特定するため
関連ファイル: src/quote_service.py, src/connection.py, src/config.py

使い方:
    python diagnose_data_freshness.py
"""

import sys
from datetime import datetime
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from src.config import load_config
from src.connection import OpenDConnection
from src.quote_service import QuoteService


def diagnose_snapshot(
    quote_service: QuoteService,
    codes: list[str],
) -> pd.DataFrame:
    """スナップショットの鮮度を診断する"""
    print("\n" + "=" * 80)
    print("[TEST 1] get_market_snapshot の鮮度確認")
    print("=" * 80)

    df = quote_service.get_stock_snapshot(codes)

    if df.empty:
        print("[FAIL] スナップショット取得失敗")
        return df

    print(f"[OK] 取得成功: {len(df)}件")
    print("\nカラム一覧:", list(df.columns))
    print("\n全データ:")
    print(df.to_string())

    # CSV保存
    output_path = Path("reports") / "diagnostic_snapshot.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n[OK] CSV保存: {output_path}")

    return df


def diagnose_history_kline(
    quote_service: QuoteService,
    code: str,
) -> pd.DataFrame:
    """request_history_klineの鮮度を診断する"""
    print("\n" + "=" * 80)
    print(f"[TEST 2] request_history_kline の鮮度確認 ({code})")
    print("=" * 80)

    # テスト条件
    test_cases = [
        {"name": "start/end指定なし", "start": None, "end": None, "autype": "qfq"},
        {"name": "start=2026-01-01", "start": "2026-01-01", "end": None, "autype": "qfq"},
        {"name": "end=2026-06-30", "start": None, "end": "2026-06-30", "autype": "qfq"},
        {"name": "autype=none", "start": None, "end": None, "autype": "none"},
    ]

    all_results = []

    for test in test_cases:
        print(f"\n--- {test['name']} ---")
        ret, data, _ = quote_service.ctx.request_history_kline(
            code,
            ktype="K_DAY",
            start=test["start"],
            end=test["end"],
            autype=test["autype"],
            max_count=1000,
        )

        if ret != 0:
            print(f"  [FAIL] {data}")
            continue

        if data.empty:
            print("  [WARN] データなし")
            continue

        latest_date = data["time_key"].iloc[0][:10]
        oldest_date = data["time_key"].iloc[-1][:10]
        count = len(data)

        print(f"  最新日付: {latest_date}")
        print(f"  最古日付: {oldest_date}")
        print(f"  件数: {count}")
        print(f"  先頭3件:")
        print(data[["time_key", "open", "high", "low", "close", "volume"]].head(3).to_string())
        print(f"  末尾3件:")
        print(data[["time_key", "open", "high", "low", "close", "volume"]].tail(3).to_string())

        all_results.append({
            "test": test["name"],
            "latest_date": latest_date,
            "oldest_date": oldest_date,
            "count": count,
        })

    # 結果まとめ
    print("\n--- 結果まとめ ---")
    for r in all_results:
        print(f"  {r['test']}: 最新={r['latest_date']}, 件数={r['count']}")

    return data


def diagnose_cur_kline(
    quote_service: QuoteService,
    code: str,
) -> pd.DataFrame:
    """get_cur_klineの鮮度を診断する"""
    print("\n" + "=" * 80)
    print(f"[TEST 3] get_cur_kline の鮮度確認 ({code})")
    print("=" * 80)

    # 事前にサブスクライブ
    from futu import SubType, RET_OK
    ret, _ = quote_service.ctx.subscribe(
        code,
        [SubType.K_DAY, SubType.K_1M, SubType.K_5M],
        subscribe_push=False,
    )
    if ret != RET_OK:
        print(f"  [WARN] サブスクライブ失敗: {ret}")

    # get_cur_kline実行
    ret, data = quote_service.ctx.get_cur_kline(
        code,
        num=30,
        ktype="K_DAY",
    )

    if ret != RET_OK:
        print(f"  [FAIL] {data}")
        return pd.DataFrame()

    if data.empty:
        print("  [WARN] データなし")
        return data

    latest_date = data["time_key"].iloc[0][:10]
    oldest_date = data["time_key"].iloc[-1][:10]
    count = len(data)

    print(f"  最新日付: {latest_date}")
    print(f"  最古日付: {oldest_date}")
    print(f"  件数: {count}")
    print(f"  先頭3件:")
    print(data[["time_key", "open", "high", "low", "close", "volume"]].head(3).to_string())
    print(f"  末尾3件:")
    print(data[["time_key", "open", "high", "low", "close", "volume"]].tail(3).to_string())

    # CSV保存
    output_path = Path("reports") / f"diagnostic_cur_kline_{code.replace('.', '_')}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n  [OK] CSV保存: {output_path}")

    return data


def diagnose_db_freshness() -> None:
    """保存済みDBの鮮度を診断する"""
    print("\n" + "=" * 80)
    print("[TEST 4] 保存済みDBの鮮度確認")
    print("=" * 80)

    import sqlite3

    db_path = Path("data/moomoo.db")
    if not db_path.exists():
        print("  [FAIL] DBが見つかりません")
        return

    with sqlite3.connect(db_path) as conn:
        # daily_barsの最新日付
        cursor = conn.execute("SELECT MAX(date), MIN(date), COUNT(*) FROM daily_bars")
        max_date, min_date, count = cursor.fetchone()
        print(f"  daily_bars: 最新={max_date}, 最古={min_date}, 件数={count}")

        # 銘柄ごとの最新日付
        cursor = conn.execute("""
            SELECT code, MAX(date) as max_date, COUNT(*) as cnt
            FROM daily_bars
            GROUP BY code
            ORDER BY max_date DESC
            LIMIT 10
        """)
        print("\n  銘柄ごとの最新日付（上位10件）:")
        for row in cursor.fetchall():
            print(f"    {row[0]}: {row[1]} ({row[2]}件)")

        # indicatorsの最新日付
        cursor = conn.execute("SELECT MAX(date) FROM indicators")
        max_ind_date = cursor.fetchone()[0]
        print(f"\n  indicators: 最新={max_ind_date}")

        # signalsの最新日付
        cursor = conn.execute("SELECT MAX(date) FROM signals")
        max_sig_date = cursor.fetchone()[0]
        print(f"  signals: 最新={max_sig_date}")

        # 今日からの差分
        today = datetime.now().strftime("%Y-%m-%d")
        if max_date:
            from datetime import datetime as dt
            latest = dt.strptime(max_date, "%Y-%m-%d")
            now = dt.strptime(today, "%Y-%m-%d")
            days_diff = (now - latest).days
            print(f"\n  最新日付が今日から {days_diff} 日古い")


def main() -> int:
    """メイン関数"""
    print("=" * 80)
    print("moomoo API データ鮮度診断")
    print("=" * 80)

    # 設定読み込み
    try:
        config = load_config("config.yaml")
        print("[OK] 設定ファイル読み込み成功")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    # 対象銘柄
    codes = [
        "JP.7203",  # トヨタ自動車
        "JP.6758",  # ソニーグループ
        "JP.8306",  # 三菱UFJ
        "JP.9984",  # ソフトバンクG
        "JP.2559",  # MAXIS全世界株式
        "JP.1320",  # iFreeETF日経225
    ]

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

        # テスト実行
        diagnose_snapshot(quote_service, codes)
        diagnose_history_kline(quote_service, "JP.7203")
        diagnose_cur_kline(quote_service, "JP.7203")

    # DBの診断
    diagnose_db_freshness()

    print("\n" + "=" * 80)
    print("診断完了")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
