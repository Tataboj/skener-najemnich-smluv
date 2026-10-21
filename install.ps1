# NajemniRadar - instalace / aktualizace knihoven (spoustet ze slozky projektu)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=== NajemniRadar: instalace ===" -ForegroundColor Cyan

if (Get-Command py -ErrorAction SilentlyContinue) { $exe = "py"; $pre = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $exe = "python"; $pre = @() }
else { throw "Python nenalezen. Nainstaluj Python 3.10+ z python.org (zaskrtni 'Add to PATH')." }

& $exe @pre -c "import sys; assert sys.version_info >= (3,10), 'Potreba Python 3.10+, mas ' + sys.version; print('Python', sys.version.split()[0])"
if ($LASTEXITCODE -ne 0) { throw "Nevhodna verze Pythonu." }

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "Vytvarim virtualni prostredi .venv ..."
    & $exe @pre -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Nepodarilo se vytvorit .venv" }
}

$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { throw "Instalace knihoven selhala - posli mi vypis." }

& $py -m pytest -q tests
& $py -m najemniradar version
Write-Host "Hotovo. Krok 1: .\run.ps1 krok1" -ForegroundColor Green
