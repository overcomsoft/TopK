import psycopg2
conn = psycopg2.connect("host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno")
cur = conn.cursor()
tables = ["TB_ROUTE_SEGMENT_DETAIL"]
for t in tables:
    cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{t}' or table_name = '{t.lower()}' or table_name = '{t.upper()}'")
    cols = cur.fetchall()
    print(f"Table: {t}")
    for c in cols:
        print(f"  {c[0]} ({c[1]})")
