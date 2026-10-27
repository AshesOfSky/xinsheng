param([string]$Workspace = '')
$ErrorActionPreference = 'Stop'
$installPath = Join-Path $env:LOCALAPPDATA 'Programs\DaFeiYu\0.2.7'
New-Item -ItemType Directory -Path $installPath -Force | Out-Null
if ([IO.Path]::GetFullPath($PSScriptRoot) -ne [IO.Path]::GetFullPath($installPath)) {
    Get-ChildItem -LiteralPath $PSScriptRoot | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $installPath -Recurse -Force
    }
}
$shell = New-Object -ComObject WScript.Shell
$shortcutDirs = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($directory in $shortcutDirs) {
    $shortcut = $shell.CreateShortcut((Join-Path $directory '大肥鱼.lnk'))
    $shortcut.TargetPath = Join-Path $installPath 'DaFeiYu.exe'
    $shortcut.WorkingDirectory = $installPath
    $shortcut.IconLocation = (Join-Path $installPath 'DaFeiYu.exe') + ',0'
    $shortcut.Description = '大肥鱼 · 微信群聊助手'
    if ($Workspace) { $shortcut.Arguments = '--workspace "' + [IO.Path]::GetFullPath($Workspace) + '"' }
    $shortcut.Save()
}
Write-Host '安装完成。桌面和开始菜单已添加“大肥鱼”。'
Write-Host '升级不会删除本地聊天记录、表情和人格。'
