param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskData = Join-Path $taskRoot 'data'
$taskPort = 23210
$taskUrl = "http://127.0.0.1:$taskPort"
if ($env:QQ_REPLY_PORT) { $taskPort = [int]$env:QQ_REPLY_PORT; $taskUrl = "http://127.0.0.1:$taskPort" }
$taskExisting = $null
try { $taskExisting = Invoke-RestMethod -Uri "$taskUrl/api/health" -TimeoutSec 2 } catch {}
if ($taskExisting) {
    if ($taskExisting.app -ne 'qq-reply-assistant' -or $taskExisting.dataDir -ne $taskData) {
        throw "Port $taskPort is occupied by another service."
    }
    if (-not $NoBrowser) { Start-Process $taskUrl }
    Write-Output "Already running: $taskUrl"
    exit 0
}
$taskNode = (Get-Command node -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $taskNode) {
    $taskBundledNode = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
    if (Test-Path -LiteralPath $taskBundledNode) { $taskNode = $taskBundledNode }
}
if (-not $taskNode) { throw 'Node.js 24 was not found.' }
$taskNodeVersion = & $taskNode --version
if ($taskNodeVersion -notmatch '^v24\.') { throw "Node.js 24 is required; found $taskNodeVersion" }
$taskMain = Join-Path $taskRoot 'dist\server\main.js'
if (-not (Test-Path -LiteralPath $taskMain)) { throw 'Build is missing. Run pnpm install and pnpm build first.' }
$taskCodex = (Get-Command codex -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $taskCodex) {
    $taskCodexBin = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'
    if (Test-Path -LiteralPath $taskCodexBin) {
        $taskCodex = Get-ChildItem -LiteralPath $taskCodexBin -Filter codex.exe -Recurse -File |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
}
$taskExtraPath = Split-Path -Parent $taskNode
if ($taskCodex) { $taskExtraPath += ';' + (Split-Path -Parent $taskCodex) }
$env:PATH = $taskExtraPath + ';' + $env:PATH
$env:QQ_REPLY_DATA_DIR = $taskData
$env:QQ_REPLY_PORT = [string]$taskPort
New-Item -ItemType Directory -Path $taskData -Force | Out-Null
$taskStdout = Join-Path $taskData 'server.stdout.log'
$taskStderr = Join-Path $taskData 'server.stderr.log'
$taskProcess = Start-Process -FilePath $taskNode -ArgumentList @('--disable-warning=ExperimentalWarning', ('"' + $taskMain + '"')) -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput $taskStdout -RedirectStandardError $taskStderr -PassThru
for ($taskAttempt = 0; $taskAttempt -lt 40; $taskAttempt++) {
    Start-Sleep -Milliseconds 200
    try {
        $taskHealth = Invoke-RestMethod -Uri "$taskUrl/api/health" -TimeoutSec 1
        if ($taskHealth.app -eq 'qq-reply-assistant' -and $taskHealth.dataDir -eq $taskData) {
            if (-not $NoBrowser) { Start-Process $taskUrl }
            Write-Output "Started: $taskUrl (PID $($taskHealth.pid))"
            exit 0
        }
    } catch {}
    $taskProcess.Refresh()
    if ($taskProcess.HasExited) { break }
}
if (Test-Path -LiteralPath $taskStderr) { Get-Content -LiteralPath $taskStderr -Tail 20 }
throw "Service did not start. See $taskStdout and $taskStderr"
