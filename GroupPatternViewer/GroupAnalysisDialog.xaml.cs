using System.Collections.Generic;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace GroupPatternViewer
{
    public partial class GroupAnalysisDialog : Window
    {
        public GroupAnalysisDialog(string jsonResult)
        {
            InitializeComponent();
            ParseAndDisplayResult(jsonResult);
        }

        private void ParseAndDisplayResult(string jsonResult)
        {
            try
            {
                using var doc = JsonDocument.Parse(jsonResult);
                var root = doc.RootElement;

                if (root.TryGetProperty("success", out var success) && success.GetBoolean() == false)
                {
                    SetErrorState(root.GetProperty("error").GetString() ?? "Unknown error");
                    return;
                }

                // 파싱
                bool finalVerdict = root.GetProperty("final_verdict").GetBoolean();
                int nMembers = root.GetProperty("n_members").GetInt32();
                
                var utilMatch = root.GetProperty("utility_match");
                var sim = root.GetProperty("similarity");
                var bends = root.GetProperty("bends");
                var pitchCv = root.GetProperty("pitch_cv");
                var messages = root.GetProperty("messages");

                // 1. 헤더 및 요약
                TxtPipeCount.Text = $"{nMembers}개";
                if (finalVerdict)
                {
                    TxtFinalVerdict.Text = "분석 결과: 패턴 그룹화 가능 (Pass)";
                    TxtFinalVerdict.Foreground = Brushes.White;
                    BorderResult.Background = new SolidColorBrush(Color.FromRgb(46, 125, 50)); // Dark Green
                }
                else
                {
                    TxtFinalVerdict.Text = "분석 결과: 패턴 그룹화 불가 (Fail)";
                    TxtFinalVerdict.Foreground = Brushes.White;
                    BorderResult.Background = new SolidColorBrush(Color.FromRgb(198, 40, 40)); // Dark Red
                }

                // 2. 형상 유사도 (Similarity)
                double avgSim = sim.GetProperty("avg").GetDouble();
                double threshold = sim.GetProperty("threshold").GetDouble();
                bool simPass = sim.GetProperty("pass").GetBoolean();

                ProgSimilarity.Value = avgSim * 100.0;
                TxtSimValue.Text = $"{(avgSim * 100.0):F1}% (기준: {(threshold * 100.0):F0}% 이상)";
                ProgSimilarity.Foreground = simPass ? new SolidColorBrush(Color.FromRgb(46, 204, 113)) : new SolidColorBrush(Color.FromRgb(231, 76, 60));

                // 3. 간격 불균일도 (Pitch CV)
                double cv = pitchCv.GetProperty("cv").GetDouble();
                double maxCv = pitchCv.GetProperty("max").GetDouble();
                bool cvPass = pitchCv.GetProperty("pass").GetBoolean();

                ProgCv.Value = cv;
                TxtCvValue.Text = $"{cv:F2} (기준: {maxCv:F2} 이하)";
                ProgCv.Foreground = cvPass ? new SolidColorBrush(Color.FromRgb(46, 204, 113)) : new SolidColorBrush(Color.FromRgb(231, 76, 60));
                // CV는 초과하면 게이지가 넘어가게 보이도록 하되 최대값을 0.6으로 제한
                if (cv > 0.6) ProgCv.Value = 0.6;

                // 4. 유틸리티 매칭
                bool utilPass = utilMatch.GetProperty("pass").GetBoolean();
                string utilVal = utilMatch.GetProperty("value").GetString() ?? "";
                if (utilPass)
                {
                    TxtUtilStatus.Text = $"✅ 일치 ({utilVal})";
                    TxtUtilStatus.Foreground = new SolidColorBrush(Color.FromRgb(46, 204, 113));
                }
                else
                {
                    TxtUtilStatus.Text = $"❌ 불일치 ({utilVal})";
                    TxtUtilStatus.Foreground = new SolidColorBrush(Color.FromRgb(231, 76, 60));
                }

                // 5. 최소 꺾임 수 (Bends)
                bool bendsPass = bends.GetProperty("pass").GetBoolean();
                int medianBends = bends.GetProperty("median").GetInt32();
                if (bendsPass)
                {
                    TxtBendsStatus.Text = $"✅ {medianBends}회 (통과)";
                    TxtBendsStatus.Foreground = new SolidColorBrush(Color.FromRgb(46, 204, 113));
                }
                else
                {
                    TxtBendsStatus.Text = $"❌ {medianBends}회 (부족)";
                    TxtBendsStatus.Foreground = new SolidColorBrush(Color.FromRgb(231, 76, 60));
                }

                // 6. 상세 메시지 파싱
                foreach (var msg in messages.EnumerateArray())
                {
                    string m = msg.GetString() ?? "";
                    if (m.Contains("통과하였습니다"))
                    {
                        AddMessage("✅ " + m, Brushes.LimeGreen);
                    }
                    else if (m.Contains("일치하지 않습니다") || m.Contains("낮습니다") || m.Contains("불규칙합니다") || m.Contains("없습니다"))
                    {
                        AddMessage("❌ " + m, Brushes.Orange);
                    }
                    else
                    {
                        AddMessage("ℹ️ " + m, Brushes.LightGray);
                    }
                }
            }
            catch (System.Exception ex)
            {
                SetErrorState($"JSON 파싱 실패: {ex.Message}\n원문:\n{jsonResult}");
            }
        }

        private void SetErrorState(string errorMsg)
        {
            TxtFinalVerdict.Text = "분석 시스템 오류";
            TxtFinalVerdict.Foreground = Brushes.White;
            BorderResult.Background = new SolidColorBrush(Color.FromRgb(198, 40, 40));

            TxtPipeCount.Text = "-";
            ProgSimilarity.Value = 0;
            ProgCv.Value = 0;
            TxtUtilStatus.Text = "알 수 없음";
            TxtBendsStatus.Text = "알 수 없음";

            AddMessage($"오류 상세:\n{errorMsg}", Brushes.Red);
        }

        private void AddMessage(string text, Brush color)
        {
            var tb = new TextBox
            {
                Text = text,
                Foreground = color,
                Background = Brushes.Transparent,
                BorderThickness = new Thickness(0),
                IsReadOnly = true,
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 8),
                FontSize = 13
            };
            PanelMessages.Children.Add(tb);
        }

        private void BtnClose_Click(object sender, RoutedEventArgs e)
        {
            this.Close();
        }
    }
}
