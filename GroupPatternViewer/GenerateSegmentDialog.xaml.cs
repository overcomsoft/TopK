using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using GroupPatternViewer.Models;

namespace GroupPatternViewer
{
    public partial class GenerateSegmentDialog : Window
    {
        private readonly DbConfig _db;
        private Process? _currentProcess;

        public GenerateSegmentDialog(DbConfig db)
        {
            InitializeComponent();
            _db = db;

            TxtHost.Text = _db.Host;
            TxtPort.Text = _db.Port.ToString();
            TxtDb.Text = _db.Database;
            TxtUser.Text = _db.User;
        }

        private async void BtnRun_Click(object sender, RoutedEventArgs e)
        {
            BtnRun.IsEnabled = false;
            TxtOutput.Clear();
            AppendLog("그룹 세그먼트 다발 추출(ExtractGroupSegments.py)을 시작합니다...\n");

            try
            {
                string exeDir = AppDomain.CurrentDomain.BaseDirectory;
                string scriptPath = "";

                var currentDir = new DirectoryInfo(exeDir);
                while (currentDir != null)
                {
                    string potentialPath = Path.Combine(currentDir.FullName, "Tools", "ExtractGroupSegments.py");
                    if (File.Exists(potentialPath))
                    {
                        scriptPath = potentialPath;
                        break;
                    }
                    currentDir = currentDir.Parent;
                }

                if (string.IsNullOrEmpty(scriptPath))
                {
                    AppendLog("오류: ExtractGroupSegments.py 스크립트를 찾을 수 없습니다.\n");
                    BtnRun.IsEnabled = true;
                    return;
                }

                var args = $"\"{scriptPath}\" --host \"{_db.Host}\" --port \"{_db.Port}\" --db \"{_db.Database}\" --user \"{_db.User}\" --password \"{_db.Password}\"";

                var startInfo = new ProcessStartInfo
                {
                    FileName = "python",
                    Arguments = args,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8
                };

                _currentProcess = new Process { StartInfo = startInfo };
                
                var tcs = new TaskCompletionSource<bool>();

                _currentProcess.OutputDataReceived += (s, ev) =>
                {
                    if (ev.Data != null)
                    {
                        Dispatcher.Invoke(() => AppendLog(ev.Data + "\n"));
                    }
                };
                
                _currentProcess.ErrorDataReceived += (s, ev) =>
                {
                    if (ev.Data != null)
                    {
                        Dispatcher.Invoke(() => AppendLog("ERROR: " + ev.Data + "\n"));
                    }
                };

                _currentProcess.Exited += (s, ev) => tcs.TrySetResult(true);
                _currentProcess.EnableRaisingEvents = true;

                _currentProcess.Start();
                _currentProcess.BeginOutputReadLine();
                _currentProcess.BeginErrorReadLine();

                await tcs.Task;
                AppendLog("\n프로세스가 종료되었습니다.\n");
            }
            catch (Exception ex)
            {
                AppendLog($"\n실행 중 오류 발생: {ex.Message}\n");
            }
            finally
            {
                _currentProcess?.Dispose();
                _currentProcess = null;
                BtnRun.IsEnabled = true;
            }
        }

        private void AppendLog(string text)
        {
            TxtOutput.AppendText(text);
            TxtOutput.ScrollToEnd();
        }

        private void BtnClose_Click(object sender, RoutedEventArgs e)
        {
            if (_currentProcess != null && !_currentProcess.HasExited)
            {
                try
                {
                    _currentProcess.Kill();
                }
                catch { }
            }
            this.Close();
        }
    }
}
