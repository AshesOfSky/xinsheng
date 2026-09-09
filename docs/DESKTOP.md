# 心声桌面工作台

桌面端使用 WPF 和 WebView2，复用本地服务，支持工作台和独立的“QQ 登录与连接”子窗口。

## 构建

安装 .NET SDK 10 和 WebView2 Runtime，在项目根目录执行：

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/publish-desktop.ps1
~~~

脚本生成 dist/desktop/Echo.Desktop.exe，并创建桌面快捷方式“心声 QQ 助手”。也可使用根目录的“启动桌面工作台.cmd”。请保留完整发布目录，不要只移动 EXE。

打开窗口会检查并按需启动本机服务和 QQ 接入；已有连接正常时不重连。关闭主窗口只结束桌面界面，QQ 和回复后台继续运行。数据保存在当前项目中，WebView2 会话单独保存在 data/desktop-webview。

## 新消息提醒

指定联系人有新来信且工作台在后台或最小化时，任务栏闪烁；切回主窗口后停止。提醒不抢占焦点，每 2 秒检查一次。

首次打开或切换联系人会建立消息基线；历史、本人消息、应用发送回流和重复读取不提醒。暂停回复生成不影响新来信提醒。

完全关闭桌面窗口后没有任务栏提醒，因此需要提醒时请使用最小化。

## 启动日志

桌面日志保存在 data/desktop.log。启动输出由 scripts/desktop-startup.ps1 写入文件，桌面等待脚本进程退出，不等待后台 QQ 关闭输出管道。

~~~powershell
dotnet run --project desktop-tests/Echo.Desktop.Tests.csproj -c Release
~~~
