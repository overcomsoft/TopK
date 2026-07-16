import psycopg2
conn = psycopg2.connect("host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno")
cur = conn.cursor()

cur.execute('SELECT "GROUP_ID", "N_MEMBERS", ST_NumGeometries("GEOM_3D"), ST_AsText("GEOM_3D") FROM "TB_ROUTE_GROUP_PATTERN" WHERE "GROUP_ID" LIKE \'740f28c61520472b61%\'')
row = cur.fetchone()
if row:
    print(f"ID: {row[0]}")
    print(f"N_MEMBERS: {row[1]}")
    print(f"NumGeometries: {row[2]}")
    wkt = row[3]
    lines = wkt.replace("MULTILINESTRING Z", "").strip("() ").split("), (")
    print(f"Number of lines: {len(lines)}")
    for idx, line in enumerate(lines):
        pts = line.split(", ")
        print(f"Line {idx+1}: {len(pts)} points, start={pts[0]}, end={pts[-1]}")
else:
    print("Group pattern not found")
