# Install Hexgate from source into an isolated environment.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
#
# Prefers pipx, then uv, then a plain venv. Override the source with
# $env:HEXGATE_REPO, or the install location with $env:HEXGATE_INSTALL_DIR.

$ErrorActionPreference = "Stop"

$Repo = if ($env:HEXGATE_REPO) { $env:HEXGATE_REPO } else { "https://github.com/code-with-idrees/hexgate.git" }
$Package = "git+$Repo"
$InstallDir = if ($env:HEXGATE_INSTALL_DIR) { $env:HEXGATE_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "hexgate" }
$BinDir = Join-Path $InstallDir "bin"

function Write-Say([string]$Message) { Write-Host $Message }

function Test-Python([string]$Exe) {
    $cmd = Get-Command $Exe -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    & $Exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
}

if (Get-Command pipx -ErrorAction SilentlyContinue) {
    Write-Say "Installing Hexgate with pipx..."
    & pipx install --force $Package
    if ($LASTEXITCODE -ne 0) { throw "pipx install failed" }
    Write-Say ""
    Write-Say "Installed. Run: hexgate init; hexgate serve"
    return
}

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Say "Installing Hexgate with uv..."
    & uv tool install --force $Package
    if ($LASTEXITCODE -ne 0) { throw "uv tool install failed" }
    Write-Say ""
    Write-Say "Installed. Run: hexgate init; hexgate serve"
    return
}

$Python = $null
foreach ($candidate in @("python3.13", "python3.12", "python3.11", "python")) {
    if (Test-Python $candidate) { $Python = $candidate; break }
}
if (-not $Python) {
    throw "Python 3.11+ is required. Install it from https://python.org, or install pipx/uv."
}

Write-Say "Using $Python"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

$Venv = Join-Path $InstallDir "venv"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    & $Python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "could not create a virtual environment" }
}

$VenvPython = Join-Path $Venv "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip | Out-Null
Write-Say "Installing Hexgate into $Venv..."
& $VenvPython -m pip install --upgrade $Package
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

$Shim = @"
@echo off
"$VenvPython" -m hexgate %*
"@
Set-Content -Path (Join-Path $BinDir "hexgate.cmd") -Value $Shim -Encoding ASCII
Set-Content -Path (Join-Path $BinDir "hg.cmd") -Value $Shim -Encoding ASCII

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $UserPath) { $UserPath = "" }
$parts = $UserPath.Split(";") | Where-Object { $_ -ne "" }
if ($parts -notcontains $BinDir) {
    $NewPath = (@($BinDir) + $parts) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    Write-Say ""
    Write-Say "Added $BinDir to your user PATH. Open a new terminal to use it."
} else {
    Write-Say ""
    Write-Say "Installed: $BinDir\hexgate.cmd"
}

Write-Say "Then run: hexgate init; hexgate serve"
