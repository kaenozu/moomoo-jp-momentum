import sqlite3, sys
sys.path.insert(0, ".")

# 古いスキーマでDBを作成
conn = sqlite3.connect("data/moomoo.db")
conn.executescript("""
CREATE TABLE IF NOT EXISTS symbols (code TEXT PRIMARY KEY, name TEXT, market TEXT, sector TEXT, enabled INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS virtual_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_name TEXT, code TEXT, side TEXT, quantity INTEGER, order_type TEXT, limit_price REAL, status TEXT, signal_id INTEGER, submitted_at TEXT, filled_at TEXT, cancelled_at TEXT, fill_price REAL, fill_reason TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, date TEXT, signal_type TEXT, score REAL, reason TEXT, risk_warnings TEXT, price_at_signal REAL, created_at TEXT, UNIQUE(code, date));
CREATE TABLE IF NOT EXISTS daily_bars (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER, turnover REAL, UNIQUE(code, date));
INSERT INTO symbols (code, name) VALUES ('JP.7203', 'test');
INSERT INTO daily_bars (code, date, close, volume) VALUES ('JP.7203', '2026-06-30', 1000, 1000);
CREATE TABLE IF NOT EXISTS virtual_equity_curve (id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_name TEXT NOT NULL, date TEXT NOT NULL, cash REAL, position_value REAL, total_equity REAL, daily_return REAL, benchmark_code TEXT, benchmark_return REAL, excess_return REAL, created_at TEXT, UNIQUE(strategy_name, date));
INSERT INTO virtual_equity_curve (strategy_name, date, cash, total_equity, daily_return) VALUES ('default', '2026-06-30', 100000, 100000, 0);
""")
conn.close()
print("[OK] 旧スキーマDB作成")

# 新コードで開く（マイグレーション）
from src.config import load_config
from src.data_store import DataStore
config = load_config("config.yaml")
ds = DataStore(config)
print("[OK] データストア初期化完了")

# カラム確認
conn = sqlite3.connect("data/moomoo.db")
for table, col in [("virtual_orders", "exit_reason"), ("signals", "strategy_name")]:
    cursor = conn.execute(f"SELECT name FROM pragma_table_info('{table}')")
    cols = [r[0] for r in cursor.fetchall()]
    status = "OK" if col in cols else "FAIL"
    print(f"[{status}] {table}.{col}")
    if col not in cols:
        sys.exit(1)
conn.close()
print("[OK] 全マイグレーション正常")
