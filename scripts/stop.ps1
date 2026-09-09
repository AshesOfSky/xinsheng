$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskData = Join-Path $taskRoot 'data'
$taskPort = if ($env:QQ_REPLY_PORT) { [int]$env:QQ_REPLY_PORT } else { 23210 }
$taskUrl = "http://127.0.0.1:$taskPort"
try { $taskHealth = Invoke-RestMethod -Uri "$taskUrl/api/health" -TimeoutSec 2 }
catch { Write-Output 'The reply service is not running.'; exit 0 }
if ($taskHealth.app -ne 'qq-reply-assistant' -or $taskHealth.dataDir -ne $taskData) { throw 'The port does not belong to this project. Nothing was stopped.' }
Invoke-RestMethod -Uri "$taskUrl/api/control/stop" -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
    Start-Sleep -Milliseconds 200
    try { Invoke-RestMethod -Uri "$taskUrl/api/health" -TimeoutSec 1 | Out-Null }
    catch { Write-Output 'Reply service stopped. QQ/NapCat was not force-terminated.'; exit 0 }
}
Write-Output 'Stop requested. The service is finishing its active jobs; check data/server.stdout.log.'
