# Build the standalone Windows statement generator.
#
#   .\scripts\build_exe.ps1
#
# Produces dist\EV-Statement-Package\ containing the .exe plus the config files
# it reads at runtime. Hand that whole folder to the end user; they need no
# Python installation.
#
# WARNING: the package includes .env (Easee credentials) and households.toml
# (tenant names and addresses). Only give it to someone trusted with both.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\Lorenzo\anaconda3\python.exe"
$package = Join-Path $root "dist\EV-Statement-Package"

if (-not (Test-Path $python)) {
    throw "Python not found at $python - edit this script to point at your interpreter."
}

Write-Host "Building executable ..." -ForegroundColor Cyan
& $python -m PyInstaller (Join-Path $root "statement.spec") --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

Write-Host "Assembling package ..." -ForegroundColor Cyan
if (Test-Path $package) { Remove-Item $package -Recurse -Force }
New-Item -ItemType Directory -Path $package | Out-Null
New-Item -ItemType Directory -Path (Join-Path $package "statements") | Out-Null

Copy-Item (Join-Path $root "dist\EV-Statement.exe") $package
foreach ($file in @(".env", "households.toml")) {
    $src = Join-Path $root $file
    if (-not (Test-Path $src)) { throw "Missing $file in the project root." }
    Copy-Item $src $package
}

@"
EV Charging Statement Generator
===============================

Double-click EV-Statement.exe, then enter the month you want as YYYY-MM
(for example 2026-07), or just press Enter to accept the month offered.

The finished PDF is written to the statements\ folder next to the program.

Keep these four items together in the same folder:
    EV-Statement.exe      the program
    .env                  Easee account and tariff settings
    households.toml       tenant name and address per charger
    statements\           where finished statements are saved

To change the price per kWh, edit TARIFF_FLAT_PRICE_PENCE in .env.
To change a tenant name or address, edit households.toml.
Neither needs the program to be rebuilt.

This folder contains account credentials and personal data - do not forward it.
"@ | Out-File -FilePath (Join-Path $package "README.txt") -Encoding utf8

$size = [math]::Round((Get-Item (Join-Path $package "EV-Statement.exe")).Length / 1MB, 1)
Write-Host ""
Write-Host "Done. $package  (exe: $size MB)" -ForegroundColor Green
