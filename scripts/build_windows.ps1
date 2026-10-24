# Reproducible Windows x64 build of the portable DJ Director AI folder.
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 [-SkipTests]
# Output: dist\DJDirectorAI\ (DJDirectorAI.exe + dj-director-cli.exe) and dist\DJDirectorAI-<ver>-win64.zip
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = "py -3.12"
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { $py = "python" }

if (-not (Test-Path .venv-build)) { Invoke-Expression "$py -m venv .venv-build" }
$venvPy = ".\.venv-build\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements-lock.txt
& $venvPy -m pip install -e ".[loopback]" --no-deps
& $venvPy -m pip install "pyinstaller>=6.3" "pytest>=7.4" "websockets>=12"

if (-not $SkipTests) {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $venvPy -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
    Remove-Item Env:\QT_QPA_PLATFORM
}

& $venvPy -m PyInstaller --noconfirm --clean packaging\dj_director.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Verify the frozen build headlessly (console CLI and the windowed exe).
& dist\DJDirectorAI\dj-director-cli.exe selftest
if ($LASTEXITCODE -ne 0) { throw "CLI selftest failed" }
$p = Start-Process dist\DJDirectorAI\DJDirectorAI.exe -ArgumentList "selftest --out selftest-gui.txt" -Wait -PassThru
Get-Content selftest-gui.txt
if ($p.ExitCode -ne 0) { throw "GUI exe selftest failed" }

# User guide (PDF rendered with Qt + Markdown source) shipped inside the portable folder
& $venvPy scripts\make_guide_pdf.py dist\Guida_DJ_Director_AI.pdf
if ($LASTEXITCODE -ne 0) { throw "guide PDF generation failed" }
Copy-Item dist\Guida_DJ_Director_AI.pdf dist\DJDirectorAI\
Copy-Item docs\GUIDA_UTENTE.md dist\DJDirectorAI\GUIDA_UTENTE.md
Copy-Item README.md, LICENSE, THIRD_PARTY_LICENSES.md dist\DJDirectorAI\
$ver = & $venvPy -c "import dj_director; print(dj_director.__version__)"
Compress-Archive -Path dist\DJDirectorAI -DestinationPath "dist\DJDirectorAI-$ver-win64.zip" -Force
Write-Host "Built dist\DJDirectorAI-$ver-win64.zip"
