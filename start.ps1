# start.ps1 - launch backend + frontend in two PowerShell windows
# Run: .\start.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# ---------- Check backend .env ----------
$envFile = Join-Path $root "backend\.env"
if (-not (Test-Path $envFile)) {
    Write-Host "ERROR: backend\.env not found. Copy from backend\.env.example and fill in values." -ForegroundColor Red
    exit 1
}
if (-not (Select-String -Path $envFile -Pattern "^USER_LABEL=.+" -Quiet)) {
    Write-Host "ERROR: backend\.env is missing USER_LABEL (required)." -ForegroundColor Red
    Write-Host "Edit backend\.env and set a 2-20 char [a-z0-9_-] label." -ForegroundColor Yellow
    exit 1
}

# ---------- Check frontend .env ----------
$feEnv = Join-Path $root "frontend\.env"
if (-not (Test-Path $feEnv)) {
    Write-Host "WARN: frontend\.env not found. Copy from frontend\.env.example and set VITE_BFF_API_KEY." -ForegroundColor Yellow
}

# ---------- Launch backend ----------
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$root\backend'; `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000"
)

# ---------- Launch frontend ----------
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$root\frontend'; npm run dev"
)

Write-Host ""
Write-Host "Backend + Frontend launched in new windows." -ForegroundColor Green
Write-Host "  Open http://localhost:5173 in your browser." -ForegroundColor Cyan
Write-Host "  Masthead should show 'You are: <your label>'. If not, check the backend window log." -ForegroundColor Cyan
