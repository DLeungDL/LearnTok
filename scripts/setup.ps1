# LearnTok AI — 環境一鍵安裝（One-Click Environment Setup）
# 用法（Usage）：powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
#
# 策略：在專案目錄內建立 .venv/，所有套件裝在裡面。
#       Codex 內建 Python 快取被清除時，.venv 不受影響。
#       若 .venv 已存在且通過驗證，直接跳過安裝。
#
# 參數：
#   -BasePython <path>：指定基底 Python 路徑（預設自動偵測）

param(
    [string]$BasePython = ""
)

$ErrorActionPreference = "Stop"

$PROJECT = $PSScriptRoot | Split-Path -Parent
$VENV = Join-Path $PROJECT ".venv"
$VENV_PYTHON = Join-Path $VENV "Scripts\python.exe"

# === 偵測基底 Python（自動偵測，可用 -BasePython 指定覆寫）===
if (-not $BasePython) {
    $candidates = @()
    if ($env:CODEX_PYTHON) { $candidates += $env:CODEX_PYTHON }
    $runtimeRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes"
    if (Test-Path $runtimeRoot) {
        $candidates += Get-Item (Join-Path $runtimeRoot "*\dependencies\python\python.exe") -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty FullName
    }
    $candidates += (Get-Command python -ErrorAction SilentlyContinue).Source
    $BasePython = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if (-not $BasePython -or -not (Test-Path $BasePython)) {
    Write-Host "[ERROR] Base Python not found. Install Python 3.12 or pass: -BasePython <path>" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== LearnTok AI Environment Setup ===" -ForegroundColor Cyan
Write-Host "Project:  $PROJECT" -ForegroundColor Gray
Write-Host "Venv:     $VENV" -ForegroundColor Gray
Write-Host "Base Py:  $BASE_PYTHON`n" -ForegroundColor Gray

# === 檢查 .venv 是否已就緒 ===
if ((Test-Path $VENV_PYTHON)) {
    Write-Host "[Check] Verifying existing .venv..." -ForegroundColor Yellow
    $verify = & $VENV_PYTHON -c @"
import importlib
ok = True
for mod in ['torch', 'rvc_python', 'edge_tts', 'fairseq']:
    try:
        importlib.import_module(mod)
    except:
        ok = False
        break
print('READY' if ok else 'MISSING')
"@ 2>&1
    if ($verify -match "READY") {
        $pkg = & $VENV_PYTHON -c "import importlib.util; print('YES' if importlib.util.find_spec('learntok') else 'NO')" 2>&1
        if ($pkg -notmatch "YES") {
            Write-Host "  learntok package missing — installing editable..." -ForegroundColor Yellow
            & $VENV_PYTHON -m pip install -e $PROJECT --quiet
        }
        Write-Host "  .venv already ready — skipping installation." -ForegroundColor Green
        Write-Host "`n=== Setup Complete (no changes needed) ===" -ForegroundColor Green
        Write-Host "Use: $VENV_PYTHON`n" -ForegroundColor Gray
        exit 0
    }
    Write-Host "  .venv exists but packages missing — reinstalling..." -ForegroundColor Yellow
}

# === Step 1: 建立 venv ===
Write-Host "[1/7] Creating .venv..." -ForegroundColor Yellow
& $BASE_PYTHON -m venv --copies $VENV
& $VENV_PYTHON -m pip install --upgrade pip --quiet
Write-Host "  Done." -ForegroundColor Green

# === Step 2: edge-tts ===
Write-Host "[2/7] Installing edge-tts..." -ForegroundColor Yellow
& $VENV_PYTHON -m pip install edge-tts --quiet
Write-Host "  Done." -ForegroundColor Green

# === Step 3: PyTorch (CUDA 12.1) ===
Write-Host "[3/7] Installing PyTorch (CUDA 12.1)..." -ForegroundColor Yellow
Write-Host "  Large download (~2.5GB), please wait..." -ForegroundColor Gray
& $VENV_PYTHON -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121 --quiet
Write-Host "  Done." -ForegroundColor Green

# === Step 4: RVC dependencies ===
Write-Host "[4/7] Installing RVC dependencies..." -ForegroundColor Yellow
& $VENV_PYTHON -m pip install rvc-python --no-deps --quiet
& $VENV_PYTHON -m pip install faiss-cpu librosa pyworld torchcrepe praat-parselmouth --quiet
& $VENV_PYTHON -m pip install loguru python-multipart uvicorn ffmpeg-python av omegaconf hydra-core bitarray sacrebleu --quiet
Write-Host "  Done." -ForegroundColor Green

# === Step 5: fairseq (from local build) ===
Write-Host "[5/7] Preparing fairseq from local build..." -ForegroundColor Yellow
$fairseqDir = Join-Path $PROJECT "fairseq_build"
$fairseqSetup = Join-Path $fairseqDir "setup.py"
if (-not (Test-Path $fairseqSetup)) {
    Write-Host "[ERROR] fairseq source not found in fairseq_build/." -ForegroundColor Red
    Write-Host "  Please put the fairseq source (tag v0.12.2) into fairseq_build/ first," -ForegroundColor Yellow
    Write-Host "  e.g. download the v0.12.2 source zip from the GitHub repo page and extract it there." -ForegroundColor Yellow
    Write-Host "  See README.md > 首次設定 for details." -ForegroundColor Yellow
    exit 1
}
$versionFile = Join-Path $fairseqDir "fairseq\version.txt"
if (-not (Test-Path $versionFile)) {
    Set-Content -Path $versionFile -Value "0.12.2" -Encoding ASCII
}
& $VENV_PYTHON -m pip install -e $fairseqDir --no-deps --quiet
Write-Host "  Done." -ForegroundColor Green

# === Step 6: Apply Python 3.12 compatibility patch ===
Write-Host "[6/7] Applying fairseq Python 3.12 patch..." -ForegroundColor Yellow
$patchTarget = Join-Path $VENV "Lib\site-packages\rvc_python\modules\vc\utils.py"
if (Test-Path $patchTarget) {
    Copy-Item (Join-Path $PROJECT "src\learntok\tools\utils_patched.py") $patchTarget -Force
    Write-Host "  Patch applied." -ForegroundColor Green
} else {
    Write-Host "  WARNING: rvc_python not found, skipping patch." -ForegroundColor Yellow
}

# === Step 7: Install learntok package (editable) ===
Write-Host "[7/7] Installing learntok package (editable)..." -ForegroundColor Yellow
& $VENV_PYTHON -m pip install -e $PROJECT --quiet
Write-Host "  Done." -ForegroundColor Green

# === Verify ===
Write-Host "`n=== Verification ===" -ForegroundColor Cyan
& $VENV_PYTHON -c @"
import torch
print(f'torch: {torch.__version__}')
print(f'CUDA: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'GPU: {torch.cuda.get_device_name(0)}')
import rvc_python
print('rvc-python: OK')
import edge_tts
print('edge-tts: OK')
import fairseq
print('fairseq: OK')
"@
Write-Host "`n=== Setup Complete! ===" -ForegroundColor Green
Write-Host "Venv Python: $VENV_PYTHON" -ForegroundColor Gray
Write-Host "`nNext: connect VPN, then run:" -ForegroundColor Gray
Write-Host "  $VENV_PYTHON -m learntok.tools.tts_edge --script <script.json>" -ForegroundColor White
Write-Host "  $VENV_PYTHON -m learntok.tools.rvc_convert --script <script.json>" -ForegroundColor White
Write-Host "  $VENV_PYTHON -m learntok.compose --script <script.json> --seed 42" -ForegroundColor White
