using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace RubberBandRouting.Viewer
{
    public partial class ObjectInfoWindow : Window
    {
        public ObjectInfoWindow()
        {
            InitializeComponent();
        }

        public void SetInfo(string category, string name, List<KeyValuePair<string, string>> properties)
        {
            TxtCategory.Text = category.ToUpper();
            TxtName.Text = name;

            GridProps.Children.Clear();
            GridProps.RowDefinitions.Clear();

            for (int i = 0; i < properties.Count; i++)
            {
                var kv = properties[i];
                
                GridProps.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

                var txtKey = new TextBlock
                {
                    Text = kv.Key,
                    Foreground = new SolidColorBrush(Color.FromRgb(156, 163, 175)), // gray-400
                    FontSize = 12,
                    Margin = new Thickness(0, 6, 10, 6),
                    TextWrapping = TextWrapping.Wrap,
                    FontWeight = FontWeights.SemiBold
                };
                Grid.SetRow(txtKey, i);
                Grid.SetColumn(txtKey, 0);
                GridProps.Children.Add(txtKey);

                var txtVal = new TextBlock
                {
                    Text = kv.Value,
                    Foreground = Brushes.White,
                    FontSize = 12,
                    Margin = new Thickness(0, 6, 0, 6),
                    TextWrapping = TextWrapping.Wrap
                };
                Grid.SetRow(txtVal, i);
                Grid.SetColumn(txtVal, 1);
                GridProps.Children.Add(txtVal);
            }
        }

        private void BtnClose_Click(object sender, RoutedEventArgs e)
        {
            this.Hide();
        }

        protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
        {
            // Instead of destroying the window, hide it so we can reuse it
            e.Cancel = true;
            this.Hide();
        }
    }
}
