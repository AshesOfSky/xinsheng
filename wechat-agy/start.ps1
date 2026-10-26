$ErrorActionPreference = 'Stop'
$hostRoot = $PSScriptRoot
$pythonPath = Join-Path $hostRoot '.venv\Scripts\pythonw.exe'
$botPath = Join-Path $hostRoot 'bot.py'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '缺少本地 Python 环境，请按 README 安装依赖。' }
$env:PYTHONUTF8 = '1'
Start-Process -FilePath $pythonPath -ArgumentList @('"' + $botPath + '"') -WorkingDirectory $hostRoot -WindowStyle Hidden
Write-Host '已请求启动大肥鱼。状态见 data\status.json；停止请运行停止大肥鱼.cmd。'
