using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using System.Windows;
using GroupPatternViewer.Models;

namespace GroupPatternViewer
{
    public partial class GeneratePatternDialog : Window
    {
        private Process? _pythonProcess;

        public GeneratePatternDialog(DbConfig? db)
        {
            InitializeComponent();
            
            if (db != null)
            {
                TxtHost.Text = db.Host;
                TxtPort.Text = db.Port.ToString();
                TxtDb.Text = db.Database;
                TxtUser.Text = db.User;
                TxtPassword.Password = db.Password;
            }
        }

        private async void BtnRun_Click(object sender, RoutedEventArgs e)
        {
            string host = TxtHost.Text.Trim();
            string port = TxtPort.Text.Trim();
            string dbname = TxtDb.Text.Trim();
            string user = TxtUser.Text.Trim();
            string password = TxtPassword.Password;

            if (string.IsNullOrEmpty(host) || string.IsNullOrEmpty(dbname) || string.IsNullOrEmpty(user))
            {
                MessageBox.Show(this, "Host, DB, User 값을 입력해주세요.", "오류", MessageBoxButton.OK, MessageBoxImage.Error);
                return;
            }

            string command = RdoRunAll.IsChecked == true ? "run-all" : "extract";
            bool dryRun = ChkDryRun.IsChecked == true;

            BtnRun.IsEnabled = false;
            TxtOutput.Clear();
            AppendLog($"--- 시작: 패턴 데이터 분석 ({command}) ---\n");

            try
            {
                string exeDir = AppDomain.CurrentDomain.BaseDirectory;
                string scriptPath = Path.GetFullPath(Path.Combine(exeDir, "..", "..", "..", "..", "Tools", "ExportGroupPattern.py"));
                
                if (!File.Exists(scriptPath))
                {
                    // Fallback just in case
                    scriptPath = Path.GetFullPath(Path.Combine(exeDir, "..", "Tools", "ExportGroupPattern.py"));
                }

                if (!File.Exists(scriptPath))
                {
                    // If still not found, try using direct path based on project structure
                    scriptPath = @"d:\DINNO\DEV\AI-AutoRouting\TopKGen\Tools\ExportGroupPattern.py";
                }

                if (!File.Exists(scriptPath))
                {
                    AppendLog($"오류: 파이썬 스크립트를 찾을 수 없습니다: {scriptPath}\n");
                    BtnRun.IsEnabled = true;
                    return;
                }

                string args = $"\"{scriptPath}\" --host \"{host}\" --port \"{port}\" --db \"{dbname}\" --user \"{user}\" --password \"{password}\" {command}";
                if (dryRun)
                {
                    args += " --dry-run";
                }

                AppendLog($"> python ExportGroupPattern.py --host {host} --port {port} --db {dbname} ... {command}\n");

                _pythonProcess = new Process
                {
                    StartInfo = new ProcessStartInfo
                    {
                        FileName = "python",
                        Arguments = args,
                        UseShellExecute = false,
                        RedirectStandardOutput = true,
                        RedirectStandardError = true,
                        CreateNoWindow = true,
                        WorkingDirectory = Path.GetDirectoryName(scriptPath)
                    }
                };

                _pythonProcess.OutputDataReceived += (s, ev) =>
                {
                    if (ev.Data != null) AppendLog(ev.Data + "\n");
                };
                _pythonProcess.ErrorDataReceived += (s, ev) =>
                {
                    if (ev.Data != null) AppendLog(ev.Data + "\n");
                };

                _pythonProcess.Start();
                _pythonProcess.BeginOutputReadLine();
                _pythonProcess.BeginErrorReadLine();

                await _pythonProcess.WaitForExitAsync();

                AppendLog($"\n--- 종료: 프로세스가 코드 {_pythonProcess.ExitCode}로 종료되었습니다. ---\n");
            }
            catch (Exception ex)
            {
                AppendLog($"\n[실행 중 오류 발생]\n{ex.Message}\n");
            }
            finally
            {
                BtnRun.IsEnabled = true;
                _pythonProcess = null;
            }
        }

        private void AppendLog(string text)
        {
            Dispatcher.Invoke(() =>
            {
                TxtOutput.AppendText(text);
                TxtOutput.ScrollToEnd();
            });
        }

        private void BtnClose_Click(object sender, RoutedEventArgs e)
        {
            if (_pythonProcess != null && !_pythonProcess.HasExited)
            {
                if (MessageBox.Show(this, "파이썬 프로세스가 실행 중입니다. 강제로 종료하고 창을 닫으시겠습니까?", "확인", MessageBoxButton.YesNo, MessageBoxImage.Warning) == MessageBoxResult.Yes)
                {
                    try { _pythonProcess.Kill(); } catch { }
                }
                else
                {
                    return;
                }
            }
            this.Close();
        }
        private async void BtnTestDb_Click(object sender, RoutedEventArgs e)
        {
            string host = TxtHost.Text.Trim();
            string port = TxtPort.Text.Trim();
            string dbname = TxtDb.Text.Trim();
            string user = TxtUser.Text.Trim();
            string password = TxtPassword.Password;

            if (string.IsNullOrEmpty(host) || string.IsNullOrEmpty(dbname) || string.IsNullOrEmpty(user) || string.IsNullOrEmpty(port))
            {
                MessageBox.Show(this, "접속 정보를 모두 입력해주세요.", "오류", MessageBoxButton.OK, MessageBoxImage.Error);
                return;
            }

            string connStr = $"Host={host};Port={port};Database={dbname};Username={user};Password={password}";

            BtnTestDb.IsEnabled = false;
            BtnTestDb.Content = "테스트 중...";

            try
            {
                await Task.Run(() =>
                {
                    using var conn = new Npgsql.NpgsqlConnection(connStr);
                    conn.Open();
                });
                MessageBox.Show(this, "데이터베이스에 성공적으로 연결되었습니다.", "접속 성공", MessageBoxButton.OK, MessageBoxImage.Information);
            }
            catch (Exception ex)
            {
                MessageBox.Show(this, $"데이터베이스 연결 실패:\n{ex.Message}", "접속 실패", MessageBoxButton.OK, MessageBoxImage.Error);
            }
            finally
            {
                BtnTestDb.IsEnabled = true;
                BtnTestDb.Content = "DB 접속 테스트";
            }
        }
    }
}
