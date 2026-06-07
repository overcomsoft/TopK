import psycopg2
conn=psycopg2.connect("host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno")
cur=conn.cursor()
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
for row in cur.fetchall():
    print(row[0])
