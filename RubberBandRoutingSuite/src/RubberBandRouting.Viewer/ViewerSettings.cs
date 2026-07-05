using System;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace RubberBandRouting.Viewer;

/// <summary>
/// Persists the PostgreSQL connection fields (and last selected project) between app launches, so
/// the user doesn't have to re-type host/user/password/database every time. Stored per-Windows-user
/// under %AppData%; the password is DPAPI-encrypted (CurrentUser scope) rather than kept in plain text.
/// </summary>
internal sealed class ViewerSettings
{
    public string Host { get; set; } = "localhost";
    public int Port { get; set; } = 5432;
    public string Username { get; set; } = "postgres";
    public string Database { get; set; } = "DDW_AI_DB";
    public string? EncryptedPassword { get; set; }
    public string? LastProjectDisplayName { get; set; }

    private static string SettingsPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "RubberBandRoutingViewer", "connection.json");

    public static ViewerSettings Load()
    {
        try
        {
            if (!File.Exists(SettingsPath)) return new ViewerSettings();
            var json = File.ReadAllText(SettingsPath);
            return JsonSerializer.Deserialize<ViewerSettings>(json) ?? new ViewerSettings();
        }
        catch
        {
            return new ViewerSettings();
        }
    }

    public void Save()
    {
        try
        {
            var dir = Path.GetDirectoryName(SettingsPath)!;
            Directory.CreateDirectory(dir);
            var json = JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(SettingsPath, json);
        }
        catch
        {
            // Best-effort persistence; a failed save shouldn't block the user from working.
        }
    }

    public string? DecryptPassword()
    {
        if (string.IsNullOrEmpty(EncryptedPassword)) return null;
        try
        {
            var cipher = Convert.FromBase64String(EncryptedPassword);
            var plain = ProtectedData.Unprotect(cipher, null, DataProtectionScope.CurrentUser);
            return Encoding.UTF8.GetString(plain);
        }
        catch
        {
            return null;
        }
    }

    public void EncryptPassword(string? password)
    {
        if (string.IsNullOrEmpty(password))
        {
            EncryptedPassword = null;
            return;
        }
        var plain = Encoding.UTF8.GetBytes(password);
        var cipher = ProtectedData.Protect(plain, null, DataProtectionScope.CurrentUser);
        EncryptedPassword = Convert.ToBase64String(cipher);
    }

    public string PortText => Port.ToString(CultureInfo.InvariantCulture);
}
