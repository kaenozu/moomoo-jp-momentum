import sqlite3

conn = sqlite3.connect('data/moomoo.db')

# daily_barsの期間を確認
cursor = conn.execute('SELECT MIN(date), MAX(date), COUNT(*) FROM daily_bars WHERE code = ?', ('JP.4502',))
print('JP.4502 daily_bars:', cursor.fetchone())

cursor = conn.execute('SELECT MIN(date), MAX(date), COUNT(*) FROM daily_bars')
print('全daily_bars:', cursor.fetchone())

# signalsの期間を確認
cursor = conn.execute('SELECT MIN(date), MAX(date), COUNT(*) FROM signals')
print('全signals:', cursor.fetchone())

conn.close()
