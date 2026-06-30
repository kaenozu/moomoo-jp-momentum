import sqlite3
import pandas as pd

conn = sqlite3.connect('data/moomoo.db')
df = pd.read_sql_query('''
    SELECT i.code, s.name, i.close, i.ma25, i.volume_ratio, i.turnover, i.return_5d,
           CASE WHEN i.close > i.ma25 THEN 1 ELSE 0 END as above_ma25,
           CASE WHEN i.volume_ratio >= 1.2 THEN 1 ELSE 0 END as vol_ok,
           CASE WHEN i.turnover >= 1000000000 THEN 1 ELSE 0 END as turnover_ok,
           CASE WHEN i.return_5d > 0 THEN 1 ELSE 0 END as ret_ok
    FROM indicators i
    LEFT JOIN symbols s ON i.code = s.code
    WHERE i.date = (SELECT MAX(date) FROM indicators)
    ORDER BY i.code
''', conn)

print("=== 条件別集計 ===")
print("MA25之上: %d/%d" % (df['above_ma25'].sum(), len(df)))
print("出来高比>=1.2: %d/%d" % (df['vol_ok'].sum(), len(df)))
print("売買代金>=10億: %d/%d" % (df['turnover_ok'].sum(), len(df)))
print("5日リターン>0: %d/%d" % (df['ret_ok'].sum(), len(df)))

all_ok = df[(df['above_ma25'] == 1) & (df['vol_ok'] == 1) & (df['turnover_ok'] == 1) & (df['ret_ok'] == 1)]
print("\n全条件OK: %d銘柄" % len(all_ok))
if len(all_ok) > 0:
    print(all_ok[['code', 'name', 'close', 'volume_ratio', 'turnover', 'return_5d']].to_string())

conn.close()
