import psycopg2
conn = psycopg2.connect("host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno")
cur = conn.cursor()

query = """
SELECT rp.EQUIPMENT_NAME, sd.FROM_POSX, sd.FROM_POSY, sd.FROM_POSZ, sd.TO_POSX, sd.TO_POSY, sd.TO_POSZ, sd.SIZE
FROM TB_ROUTE_PATH rp
JOIN TB_ROUTE_SEGMENTS rs ON rp.ROUTE_PATH_GUID = rs.ROUTE_PATH_GUID
JOIN TB_ROUTE_SEGMENT_DETAIL sd ON rs.SEGMENT_GUID = sd.SEGMENT_GUID
WHERE rp.EQUIPMENT_NAME LIKE '%MAN%'
LIMIT 10
"""
cur.execute(query)
rows = cur.fetchall()
for r in rows:
    print(r)
