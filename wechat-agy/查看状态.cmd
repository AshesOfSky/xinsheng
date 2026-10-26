@echo off
powershell.exe -NoProfile -Command "Get-Content -LiteralPath '%~dp0data\status.json' -Encoding UTF8"
pause
