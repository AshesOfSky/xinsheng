$runtimePath = Join-Path $PSScriptRoot '.runtime'
New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
Set-Content -LiteralPath (Join-Path $runtimePath 'stop') -Value 'stop' -Encoding ASCII
Write-Host '已请求停止大肥鱼；正在生成的回复会被取消，不再开始新的发送。'
