using System.Diagnostics;
using System.IO;
using System.Text;
using Npgsql;

namespace TopK.ThreeDViewer.Services;

/// <summary>
/// Tools/*.py 특징점·패턴 생성 스크립트를 하위 프로세스로 실행하고, 대상 테이블 존재 여부를
/// 확인하는 공용 서비스. VectorDBGen/MainWindow.xaml.cs의 RunPythonAsync/TableExistsAsync를
/// 그대로 이식했다 — -u(unbuffered) + PYTHONUNBUFFERED/PYTHONIOENCODING 환경변수 조합이
/// 실시간 로그 스트리밍을 보장하는 가장 안정적인 방식임을 그 코드에서 이미 검증했다.
/// </summary>
public sealed class PythonGeneratorRunner
{
    private readonly string _pythonExe;
    private readonly string _toolsDirectory;
    private Process? _process;

    public PythonGeneratorRunner(string pythonExe, string toolsDirectory)
    {
        _pythonExe = string.IsNullOrWhiteSpace(pythonExe) ? "python" : pythonExe;
        _toolsDirectory = toolsDirectory;
    }

    /// <summary>AppDomain.CurrentDomain.BaseDirectory에서 최대 8단계 상위로 올라가며 Tools/ 를 찾는다.</summary>
    public static string ResolveToolsDirectory()
    {
        var dir = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
        for (var i = 0; i < 8 && dir is not null; i++, dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "Tools");
            if (Directory.Exists(candidate) && File.Exists(Path.Combine(candidate, "tool_config.py")))
                return candidate;
        }
        throw new DirectoryNotFoundException(
            "Tools 디렉터리를 찾을 수 없습니다. TopK.3DViewer 실행 파일 기준 상위 8단계 내에 " +
            "Tools/tool_config.py가 있어야 합니다.");
    }

    public string ResolveScriptPath(string scriptFileName)
    {
        var path = Path.Combine(_toolsDirectory, scriptFileName);
        if (!File.Exists(path))
            throw new FileNotFoundException($"스크립트를 찾을 수 없습니다: {path}", path);
        return path;
    }

    public async Task<bool> TableExistsAsync(NpgsqlConnection conn, string tableName)
    {
        await using var cmd = new NpgsqlCommand(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = @t;", conn);
        cmd.Parameters.AddWithValue("t", tableName);
        var result = await cmd.ExecuteScalarAsync();
        return Convert.ToInt32(result) > 0;
    }

    /// <summary>스크립트를 실행하고 stdout/stderr을 줄 단위로 onLogLine에 전달한다. 종료코드를 반환한다.</summary>
    public async Task<int> RunAsync(
        string scriptFileName, string arguments, Action<string, bool> onLogLine, CancellationToken ct)
    {
        var scriptPath = ResolveScriptPath(scriptFileName);
        var psi = new ProcessStartInfo
        {
            FileName = _pythonExe,
            WorkingDirectory = _toolsDirectory,
            Arguments = $"-u \"{scriptPath}\" {arguments}",
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";

        _process = new Process { StartInfo = psi, EnableRaisingEvents = true };
        _process.OutputDataReceived += (_, e) => { if (e.Data is not null) onLogLine(e.Data, false); };
        _process.ErrorDataReceived += (_, e) => { if (e.Data is not null) onLogLine(e.Data, true); };

        if (!_process.Start())
            throw new InvalidOperationException($"Python 프로세스를 시작하지 못했습니다 (PATH에서 '{_pythonExe}' 확인 필요).");

        _process.BeginOutputReadLine();
        _process.BeginErrorReadLine();

        try
        {
            await _process.WaitForExitAsync(ct).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            if (!_process.HasExited) _process.Kill(entireProcessTree: true);
            throw;
        }
        finally
        {
            _process.WaitForExit();
        }
        return _process.ExitCode;
    }

    /// <summary>현재 실행 중인 프로세스가 있으면 트리 전체를 종료한다.</summary>
    public void CancelCurrent()
    {
        if (_process is { HasExited: false })
            _process.Kill(entireProcessTree: true);
    }
}
