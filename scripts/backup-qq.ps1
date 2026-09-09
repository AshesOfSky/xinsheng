param([Parameter(Mandatory = $true)][string]$DataPath)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskProcesses = Get-CimInstance Win32_Process -Filter "Name = 'QQ.exe'"
if ($taskProcesses) { throw 'Exit QQ normally from its tray menu before backing up its databases.' }
$taskSource = (Resolve-Path -LiteralPath $DataPath).Path
$taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$taskBackup = Join-Path $taskRoot "backups\qq-before-napcat-$taskStamp"
New-Item -ItemType Directory -Path $taskBackup -Force | Out-Null
$taskEntries = @(
    @{ source = $taskSource; folder = 'QQFile' },
    @{ source = (Join-Path $env:APPDATA 'QQ'); folder = 'RoamingQQ' }
)
foreach ($taskEntry in $taskEntries) {
    if (-not (Test-Path -LiteralPath $taskEntry.source)) { continue }
    $taskDest = Join-Path $taskBackup $taskEntry.folder
    $taskLog = Join-Path $taskBackup ($taskEntry.folder + '.log')
    Write-Output "Backing up $($taskEntry.source) to $taskDest"
    & robocopy.exe $taskEntry.source $taskDest /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /MT:16 /NP /NFL /NDL "/LOG:$taskLog" | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Backup failed (robocopy $LASTEXITCODE). See $taskLog" }
    Get-Content -LiteralPath $taskLog -Tail 10
}
$taskInfo = Join-Path ([Environment]::GetFolderPath('CommonDocuments')) 'Tencent\QQ\UserDataInfo.ini'
if (Test-Path -LiteralPath $taskInfo) { Copy-Item -LiteralPath $taskInfo -Destination (Join-Path $taskBackup 'UserDataInfo.ini') }
$taskRecord = @{ completedAt = (Get-Date).ToString('o'); dataPath = $taskSource; backupPath = $taskBackup; entries = $taskEntries }
$taskJson = $taskRecord | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $taskBackup 'backup.json'), $taskJson, [Text.UTF8Encoding]::new($false))
New-Item -ItemType Directory -Path (Join-Path $taskRoot 'data') -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $taskRoot 'data\qq-backup.json'), $taskJson, [Text.UTF8Encoding]::new($false))
Write-Output "Backup complete: $taskBackup"
