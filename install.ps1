# install.ps1 - one-shot installer (backend venv + frontend node_modules)
# Run: .\install.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "=== trading-king installer ===" -ForegroundColor Cyan

# ---------- Check Fubon SDK wheel ----------
$wheelDir = Join-Path $root "backend\wheels"
if (-not (Test-Path $wheelDir)) {
    New-Item -ItemType Directory -Path $wheelDir | Out-Null
}
$wheel = Get-ChildItem -Path $wheelDir -Filter "fubon_neo-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $wheel) {
    Write-Host ""
    Write-Host "ERROR: Fubon SDK wheel not found." -ForegroundColor Red
    Write-Host "Please log in to https://www.fbs.com.tw/TradeAPI/docs/welcome and download" -ForegroundColor Yellow
    Write-Host "  fubon_neo-X.Y.Z-cpNN-abi3-win_amd64.whl" -ForegroundColor Yellow
    Write-Host "Place it under $wheelDir and re-run this script." -ForegroundColor Yellow
    exit 1
}
Write-Host "Found wheel: $($wheel.Name)" -ForegroundColor Green

# ---------- Backend venv ----------
Write-Host ""
Write-Host "Creating backend venv" -ForegroundColor Cyan
Set-Location (Join-Path $root "backend")
if (-not (Test-Path .venv)) {
    python -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install --upgrade pip

Write-Host ""
Write-Host "Installing Fubon SDK wheel" -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m pip install $wheel.FullName

Write-Host ""
Write-Host "Installing Python deps" -ForegroundColor Cyan
# Backend is a flat module (no packages= config), pip install -e . will fail.
# List pyproject.toml dependencies explicitly here.
$pyDeps = @(
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "supabase>=2.4",
    "httpx>=0.27",
    "pydantic>=2.6",
    "python-dotenv>=1.0",
    "tzdata>=2024.1",
    "pytest>=8"
)
.\.venv\Scripts\python.exe -m pip install @pyDeps

# ---------- Frontend deps ----------
Write-Host ""
Write-Host "Installing frontend deps" -ForegroundColor Cyan
Set-Location (Join-Path $root "frontend")
npm install

# ---------- Install Discord bot deps ----------
Write-Host ""
Write-Host "Installing Discord bot deps" -ForegroundColor Cyan
Set-Location (Join-Path $root "bot"); npm install

Set-Location $root
Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Next steps:" -ForegroundColor Cyan
Write-Host "  1. Edit backend\.env and frontend\.env (copy from .env.example)" -ForegroundColor Cyan
Write-Host "  2. Run .\start.ps1" -ForegroundColor Cyan
