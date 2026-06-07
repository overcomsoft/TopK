import psycopg2
conn=psycopg2.connect("host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno")
cur=conn.cursor()
tables = ["TB_LATERAL_PIPE", "TB_DUCT", "TB_ROUTE_PATH"]
for t in tables:
    print(f"\n--- {t} ---")
    cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{t}' or table_name = '{t.lower()}'")
    for r in cur.fetchall():
        print(f"{r[0]} ({r[1]})")
