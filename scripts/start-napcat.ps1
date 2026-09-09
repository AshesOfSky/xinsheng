param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskNapcat = Join-Path $taskRoot 'runtime\napcat'
$taskQQ = Join-Path $taskRoot 'runtime\qq\QQ.exe'
$taskData = Join-Path $taskRoot 'data'
if (-not (Test-Path -LiteralPath $taskQQ)) { throw 'The paired QQ runtime is missing. See docs/RUNTIME.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $taskData 'qq-backup.json'))) { throw 'Back up original QQ data with scripts/backup-qq.ps1 first.' }
$taskQQProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'QQ.exe'")
if ($taskQQProcesses | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -ne $taskQQ }) {
    throw 'Exit the original desktop QQ normally from its tray menu before starting NapCat.'
}
$taskWebUIPath = Join-Path $taskNapcat 'config\webui.json'
function Write-TaskJson($Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
}
New-Item -ItemType Directory -Path (Join-Path $taskNapcat 'config') -Force | Out-Null
if (Test-Path -LiteralPath $taskWebUIPath) { $taskWebUI = Get-Content -LiteralPath $taskWebUIPath -Raw -Encoding UTF8 | ConvertFrom-Json }
else { $taskWebUI = @{ host = '127.0.0.1'; port = 6099; token = [Guid]::NewGuid().ToString('N'); autoLoginAccount = ''; disableWebUI = $false } }
$taskWebUI.host = '127.0.0.1'
Write-TaskJson $taskWebUIPath $taskWebUI
$taskWebUrl = "http://127.0.0.1:$($taskWebUI.port)/webui?token=$($taskWebUI.token)"
$taskAlreadyRunning = @($taskQQProcesses | Where-Object { $_.ExecutablePath -eq $taskQQ }).Count -gt 0
$taskAppUrl = 'http://127.0.0.1:23210'
if ($env:QQ_REPLY_PORT) { $taskAppUrl = 'http://127.0.0.1:' + $env:QQ_REPLY_PORT }
$taskHealth = $null
try { $taskHealth = Invoke-RestMethod -Uri "$taskAppUrl/api/health" -TimeoutSec 2 } catch {}
if (-not $taskHealth) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start.ps1') -NoBrowser
    if ($LASTEXITCODE -ne 0) { throw 'The reply service could not start.' }
    $taskHealth = Invoke-RestMethod -Uri "$taskAppUrl/api/health" -TimeoutSec 2
}
if ($taskHealth.app -ne 'qq-reply-assistant' -or $taskHealth.dataDir -ne $taskData) { throw 'The configured port belongs to another application.' }
$taskAppState = Invoke-RestMethod -Uri "$taskAppUrl/api/state"
$taskOBPath = Join-Path $taskNapcat 'config\onebot11.json'
if ($taskAppState.config.accountId) {
    $taskAccountOB = Join-Path $taskNapcat ("config\onebot11_" + $taskAppState.config.accountId + '.json')
    if (Test-Path -LiteralPath $taskAccountOB) { $taskOBPath = $taskAccountOB }
}
$taskWSPort = 3001
$taskToken = [Guid]::NewGuid().ToString('N')
if (Test-Path -LiteralPath $taskOBPath) {
    $taskOB = Get-Content -LiteralPath $taskOBPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $taskServer = $taskOB.network.websocketServers | Where-Object { $_.enable } | Select-Object -First 1
    if ($taskServer) {
        $taskWSPort = [int]$taskServer.port
        if ($taskServer.token) { $taskToken = $taskServer.token }
    }
}
if (-not $taskAlreadyRunning) {
    $taskOB = @{ network = @{ httpServers = @(); httpSseServers = @(); httpClients = @(); websocketClients = @(); plugins = @(); websocketServers = @(@{
        name = 'xinsheng-local'; enable = $true; host = '127.0.0.1'; port = $taskWSPort; messagePostFormat = 'array'; reportSelfMessage = $true;
        token = $taskToken; enableForcePushEvent = $true; debug = $false; heartInterval = 30000
    }) }; musicSignUrl = ''; enableLocalFile2Url = $false; parseMultMsg = $false }
    Write-TaskJson $taskOBPath $taskOB
    Write-TaskJson (Join-Path $taskNapcat 'config\onebot11.json') $taskOB
}
$taskBody = @{ token = $taskToken; wsUrl = "ws://127.0.0.1:$taskWSPort" } | ConvertTo-Json
Invoke-RestMethod -Uri "$taskAppUrl/api/config" -Method Patch -ContentType 'application/json' -Body $taskBody | Out-Null
Invoke-RestMethod -Uri "$taskAppUrl/api/connect" -Method Post -ContentType 'application/json' -Body '{}' | Out-Null
if ($taskAlreadyRunning) {
    if (-not $NoBrowser) { Start-Process $taskWebUrl }
    Write-Output 'NapCat QQ is already running; the reply service is started and a connection was requested.'
    exit 0
}
$taskVersionsPath = Join-Path (Split-Path -Parent $taskQQ) 'versions\config.json'
$taskVersion = (Get-Content -LiteralPath $taskVersionsPath -Raw -Encoding UTF8 | ConvertFrom-Json).curVersion
$taskPackagePath = Join-Path (Split-Path -Parent $taskQQ) "versions\$taskVersion\resources\app\package.json"
$taskPackage = Get-Content -LiteralPath $taskPackagePath -Raw -Encoding UTF8 | ConvertFrom-Json
$taskPackage.main = './loadNapCat.js'
Write-TaskJson (Join-Path $taskNapcat 'qqnt.json') $taskPackage
$env:NAPCAT_PATCH_PACKAGE = Join-Path $taskNapcat 'qqnt.json'
$env:NAPCAT_LOAD_PATH = Join-Path $taskNapcat 'loadNapCat.js'
$env:NAPCAT_INJECT_PATH = Join-Path $taskNapcat 'NapCatWinBootHook.dll'
$env:NAPCAT_MAIN_PATH = Join-Path $taskNapcat 'napcat.mjs'
$env:NAPCAT_WORKDIR = $taskNapcat
$env:NAPCAT_DISABLE_TIME_SYNC = '1'
$taskImportUri = [Uri]::new($env:NAPCAT_MAIN_PATH).AbsoluteUri | ConvertTo-Json -Compress
[IO.File]::WriteAllText($env:NAPCAT_LOAD_PATH, "(async () => { await import($taskImportUri); })();", [Text.UTF8Encoding]::new($false))
$taskLog = Join-Path $taskData 'napcat.stdout.log'
$taskErrorLog = Join-Path $taskData 'napcat.stderr.log'
$taskLauncher = Join-Path $taskNapcat 'NapCatWinBootMain.exe'
Start-Process -FilePath $taskLauncher -ArgumentList @(('"' + $taskQQ + '"'), ('"' + $env:NAPCAT_INJECT_PATH + '"')) -WorkingDirectory $taskNapcat -WindowStyle Hidden -RedirectStandardOutput $taskLog -RedirectStandardError $taskErrorLog | Out-Null
Write-Output "Started paired QQ $taskVersion. Logs: $taskLog"
if (-not $NoBrowser) { Start-Process $taskWebUrl }
