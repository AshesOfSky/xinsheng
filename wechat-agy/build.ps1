param([string]$Python = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'))
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Push-Location $PSScriptRoot
try {
    $outputPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'dist\DaFeiYu'))
    $workspacePrefix = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
    if (-not $outputPath.StartsWith($workspacePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Build output must remain inside this workspace.'
    }
    & $Python -m unittest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    & $Python make_icon.py
    if ($LASTEXITCODE -ne 0) { throw 'Icon build failed.' }
    & $Python -m PyInstaller --noconfirm DaFeiYu.spec
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
    & $Python package_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Release staging failed.' }
    $smokeRoot = Join-Path $PSScriptRoot ('.runtime\package-smoke-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $smokeRoot -Force | Out-Null
    $reportPath = Join-Path $smokeRoot 'result.json'
    $smokeArgs = @('--workspace',('"' + $smokeRoot + '"'),'--self-test',('"' + $reportPath + '"'))
    $process = Start-Process -FilePath (Join-Path $outputPath 'DaFeiYu.exe') -ArgumentList $smokeArgs -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(60000)) { throw 'Packaged smoke test timed out. Inspect its desktop.log.' }
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $reportPath)) { throw 'Packaged smoke test failed.' }
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    if (-not $report.ok -or -not $report.frozen -or $report.agents -ne 3 -or -not $report.persona) {
        throw 'Packaged resources are incomplete.'
    }
    New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'release') -Force | Out-Null
    $zip = Join-Path $PSScriptRoot 'release\DaFeiYu-Windows-x64-v0.2.1.zip'
    Compress-Archive -LiteralPath $outputPath -DestinationPath $zip -Force
    Write-Host ('Release package: ' + $zip)
} finally { Pop-Location }
