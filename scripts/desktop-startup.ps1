param([Parameter(Mandatory = $true)][ValidateSet('start.ps1', 'start-napcat.ps1')][string]$ScriptName)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskData = Join-Path $taskRoot 'data'
New-Item -ItemType Directory -Path $taskData -Force | Out-Null
$taskLog = Join-Path $taskData ('desktop-' + [IO.Path]::GetFileNameWithoutExtension($ScriptName) + '.log')
try {
    & (Join-Path $PSScriptRoot $ScriptName) -NoBrowser *> $taskLog
    exit $LASTEXITCODE
} catch {
    $_ | Out-File -LiteralPath $taskLog -Append
    exit 1
}
