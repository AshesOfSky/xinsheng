# 验证

本项目包含 33 项服务与界面接口自动测试，以及 13 项桌面新消息识别检查。测试使用合成资料与模拟传输，不包含真实账号、联系人、聊天正文或测试回执。

~~~powershell
pnpm check
dotnet run --project desktop-tests/Echo.Desktop.Tests.csproj -c Release
dotnet publish desktop/Echo.Desktop.csproj -c Release -r win-x64 --self-contained false -o dist/desktop
~~~

覆盖协议响应乱序、重复推送、连续消息、生成取消、模式切换、人工接管、发送结果待核、历史分页、来源校验、回放时间边界，以及桌面提醒的初始基线和消息去重。

这些测试不代表已在每个 QQ 版本完成实测，也不代替真实新来信的任务栏闪烁目视检查。模型连接探针和实际 QQ 验证须由使用者在自己的环境中执行；本仓库不携带原开发环境的私人验收记录。
