import psycopg2
conn = psycopg2.connect("host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno")
cur = conn.cursor()

query = """
SELECT DISTINCT "EQUIPMENT_NAME" FROM "TB_ROUTE_PATH" LIMIT 20
"""
cur.execute(query)
rows = cur.fetchall()
for r in rows:
    print(r[0])
