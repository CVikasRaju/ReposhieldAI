# RepoShield AI - 1-Click Launch Script (PowerShell)
$Host.UI.RawUI.WindowTitle = "RepoShield AI - Gateway & Dashboard"

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "                     RepoShield AI - Zero-Trust Shield" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Resolve Python
$PyPath = ".venv\Scripts\python.exe"
if (-not (Test-Path $PyPath)) {
    $PyPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $PyPath) {
        Write-Error "Python 3.11+ is not found. Please install Python or initialize .venv."
        exit 1
    }
}

# 2. Token
if (-not $env:RS_APPROVER_TOKEN) {
    $env:RS_APPROVER_TOKEN = "reposhield-approver-token"
}
$token = $env:RS_APPROVER_TOKEN

# 3. Ensure .aishield exists
if (-not (Test-Path ".aishield")) {
    Write-Host "[*] Initializing default .aishield policy file..." -ForegroundColor Yellow
    & $PyPath -m backend.main --init .
}

# 4. Start Gateway Process
Write-Host "[*] Starting RepoShield Gateway (MCP :8001 / API :8000)..." -ForegroundColor Green
$gatewayProcess = Start-Process -FilePath $PyPath -ArgumentList "-m", "backend.main" -PassThru -WindowStyle Minimized

# 5. Start Frontend Process
Write-Host "[*] Starting RepoShield Web Dashboard (:5173)..." -ForegroundColor Green
$npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $npmCmd) { $npmCmd = "npm" }
$frontendProcess = Start-Process -FilePath $npmCmd -ArgumentList "run", "dev" -WorkingDirectory "frontend" -PassThru -WindowStyle Minimized

# 6. Wait for initialization
Start-Sleep -Seconds 3

# 7. Open Browser
$dashboardUrl = "http://localhost:5173"
Write-Host "[*] Launching browser: $dashboardUrl" -ForegroundColor Cyan
Start-Process $dashboardUrl

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Green
Write-Host " STATUS: RepoShield AI is active and protecting your workspace!" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  - Web Dashboard:  $dashboardUrl" -ForegroundColor White
Write-Host "  - MCP Endpoint:   http://127.0.0.1:8001/mcp" -ForegroundColor White
Write-Host "  - Approver Token: $token" -ForegroundColor Yellow
Write-Host ""
Write-Host " Connect your AI Assistant (Cursor / Claude / Cline) to:" -ForegroundColor Cyan
Write-Host " http://127.0.0.1:8001/mcp" -ForegroundColor Cyan
Write-Host ""
Write-Host "======================================================================" -ForegroundColor Gray
Write-Host " Press [Enter] or [Q] to stop all RepoShield services..." -ForegroundColor Gray
$null = Read-Host

Write-Host "[*] Stopping services..." -ForegroundColor Yellow
if ($gatewayProcess -and -not $gatewayProcess.HasExited) { Stop-Process -Id $gatewayProcess.Id -Force -ErrorAction SilentlyContinue }
if ($frontendProcess -and -not $frontendProcess.HasExited) { Stop-Process -Id $frontendProcess.Id -Force -ErrorAction SilentlyContinue }
Get-Process -Name "node", "python" -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "*RepoShield*" } | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host "[✓] RepoShield stopped cleanly." -ForegroundColor Green
