import psycopg2
conn = psycopg2.connect("host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno")
cur = conn.cursor()

cur.execute('SELECT "GROUP_ID", "UTILITY_GROUP", "UTILITY", "N_MEMBERS" FROM "TB_ROUTE_GROUP_PATTERN" WHERE "N_MEMBERS" < 2')
rows = cur.fetchall()

print(f"Group patterns with < 2 members: {len(rows)}")
for r in rows:
    print(r)
