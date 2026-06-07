using System;
using Npgsql;
class Program {
    static void Main() {
        var connStr = "Host=localhost;Port=5432;Database=DDW_AI_DB;Username=postgres;Password=dinno";
        using var conn = new NpgsqlConnection(connStr);
        conn.Open();
        using var cmd = new NpgsqlCommand("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='TB_ROUTE_NODES';", conn);
        using var reader = cmd.ExecuteReader();
        while(reader.Read()) {
            Console.WriteLine($"{reader.GetString(0)} : {reader.GetString(1)}");
        }
    }
}
