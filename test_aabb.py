import psycopg2
conn=psycopg2.connect("host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno")
cur=conn.cursor()
cur.execute('SELECT COUNT(*) FROM "TB_LATERAL_PIPE" WHERE "AABB_MINX" IS NOT NULL')
print('Lateral pipes with AABB:', cur.fetchall())
