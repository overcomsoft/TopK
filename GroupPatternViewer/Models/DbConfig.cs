namespace GroupPatternViewer.Models
{
    public class DbConfig
    {
        public string Host { get; set; } = "localhost";
        public int Port { get; set; } = 5432;
        public string Database { get; set; } = "DDW_AI_DB";
        public string User { get; set; } = "postgres";
        public string Password { get; set; } = "dinno";

        public string ConnectionString =>
            $"Host={Host};Port={Port};Database={Database};Username={User};Password={Password};Encoding=UTF8";
    }
}
