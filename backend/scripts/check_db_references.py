import sqlite3

con = sqlite3.connect(r'D:\Agents\CrazzyDocs\backend\studio.db')
cur = con.cursor()

targets = [
    'showcase_vesna_vulovic_verified',
    'thumb_vesna_vulovic_verified',
    'showcase_cycle_4_20261110_1800',
    'thumb_cycle_4_20261110_1800',
    'showcase_test_vesna_integrity_run',
    'thumb_test_vesna_integrity_run'
]

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cur.fetchall()]

for table in tables:
    cur.execute(f"PRAGMA table_info({table})")
    cols = [c[1] for c in cur.fetchall()]
    text_cols = []
    for c in cols:
        try:
            cur.execute(f"SELECT [{c}] FROM [{table}] WHERE [{c}] IS NOT NULL LIMIT 1")
            val = cur.fetchone()
            if val and isinstance(val[0], str):
                text_cols.append(c)
        except Exception:
            pass
    for c in text_cols:
        for t in targets:
            try:
                cur.execute(f"SELECT COUNT(*) FROM [{table}] WHERE [{c}] LIKE ?", (f"%{t}%",))
                cnt = cur.fetchone()[0]
                if cnt > 0:
                    print(f"Table {table}, Col {c}: {cnt} matches for {t}")
            except Exception as e:
                pass

con.close()
