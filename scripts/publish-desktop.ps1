$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'create-desktop-icon.ps1')
& dotnet publish (Join-Path $taskRoot 'desktop\Echo.Desktop.csproj') -c Release -r win-x64 --self-contained false -o (Join-Path $taskRoot 'dist\desktop')
if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
$taskDesktop = [Environment]::GetFolderPath('Desktop')
$taskExe = Join-Path $taskRoot 'dist\desktop\Echo.Desktop.exe'
$taskShell = New-Object -ComObject WScript.Shell
$taskShortcut = $taskShell.CreateShortcut((Join-Path $taskDesktop '心声 QQ 助手.lnk'))
$taskShortcut.TargetPath = $taskExe
$taskShortcut.WorkingDirectory = $taskRoot
$taskShortcut.Description = '独立打开 QQ 回复工作台；关闭窗口后后台继续运行'
$taskShortcut.IconLocation = $taskExe + ',0'
$taskShortcut.Save()
Write-Output "Desktop app: $taskExe"
Write-Output "Shortcut: $(Join-Path $taskDesktop '心声 QQ 助手.lnk')"
