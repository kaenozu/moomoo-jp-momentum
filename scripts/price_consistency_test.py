"""
moomoo vs yfinance 価格整合性テスト

ファイルパス: scripts/price_consistency_test.py
何をするか: daily_barsのmoomoo最終日とyfinance初日のclose価格を比較
なぜ存在するか: yfinance補完データの品質保証（遷移点の連続性確認）
関連ファイル: data/moomoo.db, src/quote_service.py

発見:
- moomoo: 2025-01-06~2026-07-02, yfinance: 2025-01-06~2026-07-09
- JP 127銘柄: 日付範囲が重複していないため、遷移点のclose価格差を検証
- JP遷移点の乖離は3-6%（正常な日次変動範囲）
- US銘柄はmoomoo最終日(7/2)とyfinance初日(1/6 or 7/1)で大きく乖離（意味なし）
"""

import sqlite3
import sys
import os

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PYTHONIOENCODING"] = "utf-8"


def run_consistency_test(db_path: str = "data/moomoo.db"):
    """moomoo/yfinance価格整合性をテストする。"""
    conn = sqlite3.connect(db_path)

    # JP銘柄のみ比較（USは日付範囲が大きく異なるため）
    jp_both = [row[0] for row in conn.execute("""
        SELECT DISTINCT a.code
        FROM (SELECT code FROM daily_bars WHERE source='moomoo' AND code LIKE 'JP.%') a
        JOIN (SELECT code FROM daily_bars WHERE source='yfinance' AND code LIKE 'JP.%') b
        ON a.code = b.code
    """).fetchall()]

    print(f"=== moomoo vs yfinance price consistency test (JP only) ===")
    print(f"Targets: {len(jp_both)} codes")
    print()

    # 日付範囲
    m_range = conn.execute("""
        SELECT MIN(date), MAX(date) FROM daily_bars WHERE source='moomoo' AND code LIKE 'JP.%'
    """).fetchone()
    y_range = conn.execute("""
        SELECT MIN(date), MAX(date) FROM daily_bars WHERE source='yfinance' AND code LIKE 'JP.%'
    """).fetchone()
    print(f"moomoo JP range: {m_range[0]} ~ {m_range[1]}")
    print(f"yfinance JP range: {y_range[0]} ~ {y_range[1]}")
    print()

    # 遷移点比較（10%以上で警告）
    threshold = 0.10
    anomalies = []
    checked = 0
    ok = 0

    for code in sorted(jp_both):
        m_last = conn.execute("""
            SELECT date, close FROM daily_bars
            WHERE code=? AND source='moomoo'
            ORDER BY date DESC LIMIT 1
        """, (code,)).fetchone()

        y_first = conn.execute("""
            SELECT date, close FROM daily_bars
            WHERE code=? AND source='yfinance'
            ORDER BY date ASC LIMIT 1
        """, (code,)).fetchone()

        if not m_last or not y_first:
            continue

        checked += 1
        m_date, m_close = m_last
        y_date, y_close = y_first

        if m_close and y_close and m_close > 0:
            pct_diff = abs(y_close - m_close) / m_close
            if pct_diff < threshold:
                ok += 1
            else:
                anomalies.append({
                    "code": code,
                    "m_date": m_date,
                    "y_date": y_date,
                    "m_close": m_close,
                    "y_close": y_close,
                    "diff_pct": pct_diff * 100,
                })

    print(f"--- Transition point results (10% threshold) ---")
    print(f"Checked: {checked} codes")
    print(f"Within threshold: {ok}/{checked} ({ok/checked*100:.1f}%)" if checked else "")

    if anomalies:
        print(f"\n--- Anomalies ({len(anomalies)} codes) ---")
        anomalies.sort(key=lambda x: x["diff_pct"], reverse=True)
        for a in anomalies:
            print(f"  {a['code']}: {a['m_date']}={a['m_close']:.0f} -> "
                  f"{a['y_date']}={a['y_close']:.0f} ({a['diff_pct']:.1f}%)")
    else:
        print("\n[OK] All JP transitions within 10% threshold")

    # 重複日付がある銘柄の整合性（もしあれば）
    overlap_count = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT a.code, a.date
            FROM daily_bars a
            JOIN daily_bars b ON a.code = b.code AND a.date = b.date
            WHERE a.source='moomoo' AND b.source='yfinance'
            AND a.code LIKE 'JP.%'
        )
    """).fetchone()[0]
    print(f"\nOverlapping dates (JP): {overlap_count}")

    if overlap_count > 0:
        # 重複日付のclose価格差を確認
        rows = conn.execute("""
            SELECT a.code, a.date, a.close AS m_close, b.close AS y_close
            FROM daily_bars a
            JOIN daily_bars b ON a.code = b.code AND a.date = b.date
            WHERE a.source='moomoo' AND b.source='yfinance'
            AND a.code LIKE 'JP.%'
            ORDER BY a.code, a.date
        """).fetchall()

        match = 0
        total = 0
        for code, date, m, y in rows:
            if m and y and m > 0:
                total += 1
                if abs(m - y) / m < 0.001:
                    match += 1

        print(f"Close match within 0.1%: {match}/{total} ({match/total*100:.1f}%)" if total else "")

    conn.close()
    return len(anomalies) == 0


if __name__ == "__main__":
    success = run_consistency_test()
    sys.exit(0 if success else 1)
