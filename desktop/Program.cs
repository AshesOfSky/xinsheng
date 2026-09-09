using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;

namespace Echo.Desktop;

public static class Program
{
    [STAThread]
    public static void Main()
    {
        var app = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose };
        app.Run(new EchoWindow());
    }
}

public sealed class EchoWindow : Window
{
    private readonly string projectRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", ".."));
    private readonly HttpClient http = new() { Timeout = TimeSpan.FromSeconds(3) };
    private readonly WebView2 workbench = new();
    private readonly TextBlock startup = new() { Text = "正在启动心声…", FontSize = 16, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(32), VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HorizontalAlignment.Center };
    private readonly Button retry = MakeButton("重新启动");
    private readonly Button login = MakeButton("QQ 登录与连接");
    private readonly Button refresh = MakeButton("刷新页面");
    private CoreWebView2Environment? environment;
    private Window? loginWindow;
    private bool starting;
    private bool flashing;
    private readonly CancellationTokenSource lifetime = new();
    private readonly IncomingMessageTracker incoming = new();
    private Task? notificationTask;
    private string WorkbenchUrl => "http://127.0.0.1:" + (Environment.GetEnvironmentVariable("QQ_REPLY_PORT") ?? "23210");
    private string DataDir => Path.Combine(projectRoot, "data");

    public EchoWindow()
    {
        Title = "心声 · QQ 回复助手";
        Width = 1240; Height = 860; MinWidth = 760; MinHeight = 540;
        WindowStartupLocation = WindowStartupLocation.CenterScreen;
        Background = Brush("#f5f3ed"); Foreground = Brush("#273f36");
        FontFamily = new FontFamily("Microsoft YaHei UI");
        Icon = BitmapFrame.Create(new Uri("pack://application:,,,/Assets/echo.ico"));

        var layout = new DockPanel();
        var toolbar = new DockPanel { Background = Brush("#eeefe7"), LastChildFill = true };
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(6) };
        actions.Children.Add(login); actions.Children.Add(refresh);
        DockPanel.SetDock(actions, Dock.Right); toolbar.Children.Add(actions);
        toolbar.Children.Add(new TextBlock { Text = "心声  /  桌面工作台", VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(18, 0, 0, 0), FontSize = 13 });
        DockPanel.SetDock(toolbar, Dock.Top); layout.Children.Add(toolbar);
        var footer = new TextBlock { Text = "最小化时，新消息会在任务栏闪黄提醒。关闭窗口只保留后台处理；暂停或停止请在工作台操作。", FontSize = 11, Foreground = Brush("#63705f"), Padding = new Thickness(14, 8, 14, 8), TextWrapping = TextWrapping.Wrap };
        DockPanel.SetDock(footer, Dock.Bottom); layout.Children.Add(footer);
        var content = new Grid(); content.Children.Add(workbench); content.Children.Add(startup);
        retry.HorizontalAlignment = HorizontalAlignment.Center; retry.VerticalAlignment = VerticalAlignment.Bottom; retry.Margin = new Thickness(20, 20, 20, 50); retry.Visibility = Visibility.Collapsed;
        content.Children.Add(retry); layout.Children.Add(content); Content = layout;
        login.IsEnabled = false; refresh.IsEnabled = false;
        Loaded += async (_, _) => await StartAsync();
        retry.Click += async (_, _) => await StartAsync();
        login.Click += async (_, _) => await OpenLoginAsync();
        refresh.Click += (_, _) => workbench.Reload();
        Activated += (_, _) => StopFlashing();
        Closed += (_, _) => { lifetime.Cancel(); StopFlashing(); workbench.Dispose(); http.Dispose(); };
    }

    private static Brush Brush(string color) => (Brush)new BrushConverter().ConvertFromString(color)!;
    private static Button MakeButton(string text) => new() { Content = text, Padding = new Thickness(13, 6, 13, 6), Margin = new Thickness(4, 0, 4, 0), Background = Brush("#fffefa"), Foreground = Brush("#285a46"), BorderBrush = Brush("#d9dfd3"), BorderThickness = new Thickness(1) };

    private void Log(string text)
    {
        Directory.CreateDirectory(DataDir);
        File.AppendAllText(Path.Combine(DataDir, "desktop.log"), $"{DateTimeOffset.Now:O} {text}{Environment.NewLine}");
    }

    private async Task<JsonDocument?> ReadJsonAsync(string url, CancellationToken cancellationToken = default)
    {
        try { return JsonDocument.Parse(await http.GetStringAsync(url, cancellationToken)); }
        catch (HttpRequestException) { return null; }
        catch (TaskCanceledException) { return null; }
    }

    private async Task RunStartupAsync(string script)
    {
        // Background QQ processes can inherit redirected pipe handles. Let PowerShell write a
        // file instead, so startup completion depends on its exit, not on QQ closing those pipes.
        var start = new ProcessStartInfo("powershell.exe") { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = projectRoot };
        foreach (var arg in new[] { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", Path.Combine(projectRoot, "scripts", "desktop-startup.ps1"), "-ScriptName", script }) start.ArgumentList.Add(arg);
        using var process = Process.Start(start) ?? throw new InvalidOperationException("未能启动后台服务。");
        await process.WaitForExitAsync();
        var logFile = Path.Combine(DataDir, "desktop-" + Path.GetFileNameWithoutExtension(script) + ".log");
        var result = File.Exists(logFile) ? (await File.ReadAllTextAsync(logFile)).Trim() : "";
        Log($"{script} exit={process.ExitCode} {result}");
        if (process.ExitCode != 0)
        {
            if (result.Contains("Exit the original desktop QQ")) throw new InvalidOperationException("原版桌面 QQ 正在运行。请先从托盘正常退出原版 QQ，再点击重新启动。手机 QQ 可以继续使用。");
            throw new InvalidOperationException(result.Length == 0 ? "后台启动失败，请查看 data/desktop.log。" : result);
        }
    }

    private (string Url, string Token) LoginAddress()
    {
        using var config = JsonDocument.Parse(File.ReadAllText(Path.Combine(projectRoot, "runtime", "napcat", "config", "webui.json")));
        return ($"http://127.0.0.1:{config.RootElement.GetProperty("port").GetInt32()}/webui/", config.RootElement.GetProperty("token").GetString() ?? "");
    }

    private async Task<bool> LoginPageAvailableAsync()
    {
        try { using var response = await http.GetAsync(LoginAddress().Url); return response.IsSuccessStatusCode; }
        catch (HttpRequestException) { return false; }
        catch (TaskCanceledException) { return false; }
        catch (FileNotFoundException) { return false; }
    }

    private async Task StartAsync()
    {
        if (starting) return;
        starting = true; startup.Text = "正在连接心声…"; startup.Visibility = Visibility.Visible; retry.Visibility = Visibility.Collapsed;
        try
        {
            using var previous = await ReadJsonAsync(WorkbenchUrl + "/api/health");
            if (previous is null) await RunStartupAsync("start.ps1");
            using var health = await ReadJsonAsync(WorkbenchUrl + "/api/health") ?? throw new InvalidOperationException("后台服务未能启动。");
            if (health.RootElement.GetProperty("app").GetString() != "qq-reply-assistant" || !string.Equals(health.RootElement.GetProperty("dataDir").GetString(), DataDir, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("这个端口不是当前项目的服务，请先检查连接配置。");
            // Opening another desktop window must not reconnect QQ or invalidate an existing draft.
            if (!await LoginPageAvailableAsync()) { startup.Text = "正在启动 QQ 接入…"; await RunStartupAsync("start-napcat.ps1"); }
            environment ??= await CoreWebView2Environment.CreateAsync(userDataFolder: Path.Combine(DataDir, "desktop-webview"));
            await InitializeBrowserAsync(workbench, "workbench");
            workbench.Source = new Uri(WorkbenchUrl);
            startup.Visibility = Visibility.Collapsed; login.IsEnabled = true; refresh.IsEnabled = true;
            Log("Desktop initialized.");
            notificationTask ??= WatchIncomingAsync(lifetime.Token);
        }
        catch (Exception error) { Log("Startup error: " + error); startup.Text = "启动未完成\n\n" + error.Message; retry.Visibility = Visibility.Visible; }
        finally { starting = false; }
    }

    private async Task WatchIncomingAsync(CancellationToken cancellationToken)
    {
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                using var state = await ReadJsonAsync(WorkbenchUrl + "/api/state", cancellationToken);
                if (state is not null && incoming.Observe(state.RootElement) && (!IsActive || WindowState == WindowState.Minimized) && !flashing)
                {
                    SetTaskbarFlash(0x00000002 | 0x0000000C); // FLASHW_TRAY | FLASHW_TIMERNOFG
                    flashing = true;
                    Log("New incoming message: taskbar attention requested.");
                }
                await Task.Delay(2000, cancellationToken);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { }
    }

    private void StopFlashing()
    {
        if (!flashing) return;
        SetTaskbarFlash(0); // FLASHW_STOP
        flashing = false;
        Log("Taskbar attention cleared.");
    }

    private void SetTaskbarFlash(uint flags)
    {
        var info = new FlashInfo { Size = (uint)Marshal.SizeOf<FlashInfo>(), Window = new WindowInteropHelper(this).Handle, Flags = flags, Count = uint.MaxValue, Timeout = 0 };
        FlashWindowEx(ref info); // Return value describes the previous active state, not success/failure.
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FlashInfo
    {
        public uint Size;
        public IntPtr Window;
        public uint Flags;
        public uint Count;
        public uint Timeout;
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool FlashWindowEx(ref FlashInfo info);

    private async Task InitializeBrowserAsync(WebView2 browser, string name)
    {
        if (browser.CoreWebView2 is not null) return;
        await browser.EnsureCoreWebView2Async(environment);
        var core = browser.CoreWebView2!;
        core.Settings.IsStatusBarEnabled = false;
        browser.NavigationCompleted += (_, args) => Log($"{name} navigation success={args.IsSuccess} http={args.HttpStatusCode} error={args.WebErrorStatus}");
        core.NewWindowRequested += (_, args) =>
        {
            args.Handled = true;
            if (Uri.TryCreate(args.Uri, UriKind.Absolute, out var uri) && uri.Scheme is "http" or "https") Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
        };
    }

    private async Task OpenLoginAsync()
    {
        if (loginWindow is not null) { loginWindow.Show(); loginWindow.Activate(); return; }
        var browser = new WebView2();
        var window = new Window { Title = "QQ 登录与连接 · 心声", Width = 1040, Height = 780, MinWidth = 700, MinHeight = 500, Owner = this, WindowStartupLocation = WindowStartupLocation.CenterOwner, Icon = Icon, Content = browser };
        loginWindow = window;
        window.Closed += (_, _) => { browser.Dispose(); loginWindow = null; };
        window.Show();
        try
        {
            await InitializeBrowserAsync(browser, "qq-login");
            var address = LoginAddress();
            browser.Source = new Uri(address.Url + "?token=" + Uri.EscapeDataString(address.Token));
        }
        catch (Exception error) { Log("Login window error: " + error); window.Content = new TextBlock { Text = "QQ 登录页暂时不可用：" + error.Message, Margin = new Thickness(24), TextWrapping = TextWrapping.Wrap }; browser.Dispose(); }
    }
}
