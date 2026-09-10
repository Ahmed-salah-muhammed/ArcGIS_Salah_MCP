using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;

namespace SalahAIBridge
{
    /// <summary>
    /// One shared button style for every dialog in the add-in: fully rounded
    /// ("pill") corners, matching the reference UI. WPF's default Button chrome
    /// doesn't support CornerRadius, so this swaps in a small ControlTemplate
    /// (a Border + centered ContentPresenter) instead of restyling each dialog's
    /// buttons individually. Corner radius is always height/2, so the pill stays
    /// fully rounded regardless of the button's size in a given dialog.
    /// </summary>
    internal static class PillButton
    {
        private static readonly FontFamily Seg = new("Segoe UI");

        public static Button Create(object content, double width, double height, Brush background, Brush foreground,
            bool isDefault = false, bool isCancel = false)
        {
            var button = new Button
            {
                Content = content,
                Width = width,
                Height = height,
                Background = background,
                Foreground = foreground,
                BorderThickness = new Thickness(0),
                FontFamily = Seg,
                FontSize = 13,
                FontWeight = FontWeights.SemiBold,
                Cursor = System.Windows.Input.Cursors.Hand,
                IsDefault = isDefault,
                IsCancel = isCancel,
                SnapsToDevicePixels = true,
                Template = BuildTemplate(height),
            };
            return button;
        }

        private static ControlTemplate BuildTemplate(double height)
        {
            var template = new ControlTemplate(typeof(Button));

            var border = new FrameworkElementFactory(typeof(Border));
            border.Name = "PillBorder";
            border.SetValue(Border.CornerRadiusProperty, new CornerRadius(height / 2));
            border.SetBinding(Border.BackgroundProperty,
                new Binding("Background") { RelativeSource = RelativeSource.TemplatedParent });
            border.SetBinding(Border.BorderBrushProperty,
                new Binding("BorderBrush") { RelativeSource = RelativeSource.TemplatedParent });
            border.SetBinding(Border.BorderThicknessProperty,
                new Binding("BorderThickness") { RelativeSource = RelativeSource.TemplatedParent });

            var content = new FrameworkElementFactory(typeof(ContentPresenter));
            content.SetValue(ContentPresenter.HorizontalAlignmentProperty, HorizontalAlignment.Center);
            content.SetValue(ContentPresenter.VerticalAlignmentProperty, VerticalAlignment.Center);
            content.SetValue(ContentPresenter.MarginProperty, new Thickness(6, 0, 6, 0));
            border.AppendChild(content);

            template.VisualTree = border;

            // Subtle hover/pressed feedback so the pill doesn't feel static.
            var hover = new Trigger { Property = Button.IsMouseOverProperty, Value = true };
            hover.Setters.Add(new Setter(UIElement.OpacityProperty, 0.85, "PillBorder"));
            template.Triggers.Add(hover);

            var pressed = new Trigger { Property = Button.IsPressedProperty, Value = true };
            pressed.Setters.Add(new Setter(UIElement.OpacityProperty, 0.7, "PillBorder"));
            template.Triggers.Add(pressed);

            var disabled = new Trigger { Property = Button.IsEnabledProperty, Value = false };
            disabled.Setters.Add(new Setter(UIElement.OpacityProperty, 0.4, "PillBorder"));
            template.Triggers.Add(disabled);

            return template;
        }
    }
}
