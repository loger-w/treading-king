# start.ps1 - launch backend + frontend + Discord bot in one Windows Terminal window (panes)
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

# ---------- Check Discord bot .env (optional) ----------
$botEnv = Join-Path $root "bot\.env"
if (-not (Test-Path $botEnv)) {
    Write-Host "WARN: bot\.env not found - Discord bot will not start (copy bot\.env.example and set DISCORD_BOT_TOKEN)." -ForegroundColor Yellow
}

# ---------- Launch backend + frontend in one Windows Terminal window (two panes) ----------
# Set PYTHONIOENCODING on this (parent) process so the backend pane inherits it.
# Keeping it out of the pane command means neither pane's commandline contains ';',
# which matters because wt parses ';' as its own subcommand delimiter.
$env:PYTHONIOENCODING = "utf-8"
$beCmd = ".\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload"
$feCmd = "npm run dev"
$botCmd = "npm run start"

$wt = Get-Command wt.exe -ErrorAction SilentlyContinue
if ($wt) {
    # WT_SESSION is set in every shell Windows Terminal hosts. If we're inside one,
    # target THIS window (-w 0) so the servers open as a new tab here instead of a
    # separate window popping up. If it's empty we aren't in WT (e.g. classic console
    # or VS Code terminal) - you can't add tabs to a non-WT console, so open a new window.
    $window = if ($env:WT_SESSION) { "0" } else { "new" }
    # backend on top, frontend on the bottom half (split-pane -H = horizontal divider)
    $wtArgs = @(
        "-w", $window,
        "new-tab",    "--title", "backend",  "-d", "$root\backend",  "powershell", "-NoExit", "-Command", $beCmd,
        ";",
        "split-pane", "-H", "--title", "frontend", "-d", "$root\frontend", "powershell", "-NoExit", "-Command", $feCmd
    )
    # Only add the Discord bot pane when bot\.env exists (skip otherwise to avoid a missing-token crash)
    if (Test-Path $botEnv) {
        $wtArgs += @(
            ";",
            "split-pane", "-V", "--title", "bot", "-d", "$root\bot", "powershell", "-NoExit", "-Command", $botCmd
        )
    }
    & wt.exe @wtArgs
    Write-Host ""
    if ($window -eq "0") {
        Write-Host "Backend + Frontend opened in a new tab of THIS window (two panes)." -ForegroundColor Green
    } else {
        Write-Host "Not inside Windows Terminal - opened a new WT window (two panes)." -ForegroundColor Yellow
    }
} else {
    # Windows Terminal not installed (e.g. a teammate's machine) - fall back to two windows.
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "Set-Location '$root\backend'; `$env:PYTHONIOENCODING='utf-8'; $beCmd"
    )
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "Set-Location '$root\frontend'; $feCmd"
    )
    if (Test-Path $botEnv) {
        Start-Process powershell -ArgumentList @(
            "-NoExit", "-Command",
            "Set-Location '$root\bot'; $botCmd"
        )
    }
    Write-Host ""
    Write-Host "Windows Terminal (wt) not found - launched in separate windows instead." -ForegroundColor Yellow
}
Write-Host "  Open http://localhost:5173 in your browser." -ForegroundColor Cyan
Write-Host "  If the page doesn't load, check the backend pane log." -ForegroundColor Cyan
if (Test-Path $botEnv) {
    Write-Host "  Discord bot: type p<symbol> (e.g. p2330) in your channel." -ForegroundColor Cyan
}
