# scripts/update-fbs-docs.ps1
# 從富邦官網重新抓取 llms.txt 索引覆寫本地 docs/api/fubon-neo-llms.txt
#
# 用法:
#   .\scripts\update-fbs-docs.ps1            # 預設更新中文版
#   .\scripts\update-fbs-docs.ps1 -English   # 同時更新英文版(若以後需要)

[CmdletBinding()]
param(
    [switch]$English
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$repoRoot = Split-Path -Parent $PSScriptRoot
$apiDir = Join-Path $repoRoot 'docs\api'

if (-not (Test-Path $apiDir)) {
    New-Item -ItemType Directory -Path $apiDir -Force | Out-Null
}

$jobs = @(
    @{ Url = 'https://www.fbs.com.tw/TradeAPI/llms.txt'; Target = (Join-Path $apiDir 'fubon-neo-llms.txt'); Label = 'zh' }
)
if ($English) {
    $jobs += @{ Url = 'https://www.fbs.com.tw/TradeAPI/en/llms.txt'; Target = (Join-Path $apiDir 'fubon-neo-llms.en.txt'); Label = 'en' }
}

foreach ($job in $jobs) {
    Write-Host "[$($job.Label)] fetching $($job.Url) ..."
    $tmp = New-TemporaryFile
    try {
        Invoke-WebRequest -Uri $job.Url -OutFile $tmp.FullName -UseBasicParsing -TimeoutSec 30
        $newSize = (Get-Item $tmp.FullName).Length
        if ($newSize -lt 10000) {
            throw "Downloaded file too small ($newSize bytes) — aborting"
        }

        $oldSize = if (Test-Path $job.Target) { (Get-Item $job.Target).Length } else { 0 }
        Move-Item -Path $tmp.FullName -Destination $job.Target -Force

        Write-Host "  done -> $($job.Target)"
        Write-Host "  size: $oldSize -> $newSize bytes"
    }
    finally {
        if (Test-Path $tmp.FullName) { Remove-Item $tmp.FullName -Force -ErrorAction SilentlyContinue }
    }
}

Write-Host ""
Write-Host "Next steps:"
Write-Host "  git diff docs/api/fubon-neo-llms.txt"
Write-Host "  git add docs/api ; git commit -m 'docs(api): refresh fubon-neo-llms index'"
