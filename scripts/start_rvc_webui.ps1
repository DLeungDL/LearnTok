# LearnTok AI — 啟動 RVC WebUI（訓練推理界面 / Training + Inference UI）
# 用法（Usage）：powershell -ExecutionPolicy Bypass -File scripts/start_rvc_webui.ps1 [-NoOpen] [-Port 7865]

param(
    [switch]$NoOpen,
    [int]$Port = 7865
)

$ErrorActionPreference = "Stop"

$PROJECT = Split-Path -Parent $PSScriptRoot
$WEBUI = Join-Path $PROJECT "RVC-WebUI"
$PYTHON = Join-Path $WEBUI ".venv\Scripts\python.exe"
$LOG = Join-Path $WEBUI "webui.log"

if (-not (Test-Path $PYTHON)) {
    Write-Host "[ERROR] RVC WebUI venv not found at $PYTHON" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $WEBUI "webui.py"))) {
    Write-Host "[ERROR] RVC-WebUI source not found at $WEBUI" -ForegroundColor Red
    exit 1
}

Push-Location $WEBUI
try {
    $env:GRADIO_ANALYTICS_ENABLED = "False"
    $env:NO_PROXY = "localhost,127.0.0.1,::1"
    $env:MPLCONFIGDIR = Join-Path $WEBUI "TEMP\matplotlib-cache"
    $webuiArgs = @("webui.py", "--port", "$Port", "--pycmd", $PYTHON)
    if ($NoOpen) { $webuiArgs += "--noautoopen" }
    Write-Host "RVC WebUI: http://localhost:$Port  (Ctrl+C 停止)" -ForegroundColor Cyan
    & $PYTHON @webuiArgs 2>&1 | Tee-Object -FilePath $LOG
} finally {
    Pop-Location
}