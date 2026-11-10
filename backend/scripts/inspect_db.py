import sqlite3

con = sqlite3.connect(r'D:\Agents\CrazzyDocs\backend\studio.db')
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()
print('Tables:', tables)
for (t,) in tables:
    cur.execute(f"PRAGMA table_info({t})")
    cols = cur.fetchall()
    print(f"Table {t}: {[c[1] for c in cols]}")
con.close()
