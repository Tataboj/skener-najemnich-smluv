# NajemniRadar - spousteni prikazu, napr.:  .\run.ps1 krok1
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Chybi .venv - nejdriv spust .\install.ps1" -ForegroundColor Red; exit 1 }
Push-Location $PSScriptRoot
try { & $py -m najemniradar @args; $code = $LASTEXITCODE } finally { Pop-Location }
exit $code
