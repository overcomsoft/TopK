import psycopg2

def inspect():
    conn_str = "host=localhost port=5432 dbname=DDW_AI_DB user=postgres password=dinno"
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'TB_EQUIPMENTS'")
    print("TB_EQUIPMENTS cols:", [r[0] for r in cur.fetchall()])
    
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'TB_ROUTE_PATH'")
    print("TB_ROUTE_PATH cols:", [r[0] for r in cur.fetchall()])
    
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'TB_ROUTE_NODES'")
    print("TB_ROUTE_NODES cols:", [r[0] for r in cur.fetchall()])

    conn.close()

if __name__ == '__main__':
    inspect()
