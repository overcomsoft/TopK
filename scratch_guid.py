import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "RubberBandRouter"))

import psycopg2
import psycopg2.extras
import config as cfg

conn = psycopg2.connect(cfg.get_conninfo())
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute('SELECT * FROM "TB_ROUTE_PATH" LIMIT 1;')
row = cur.fetchone()
for k, v in row.items():
    print(f"  {k}: {v}")
