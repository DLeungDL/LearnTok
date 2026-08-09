# make_video.ps1 — one-command video generation (TTS -> RVC -> calibrate -> compose).
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/make_video.ps1 -ScriptPath pipeline/examples/script_xxx.json
#   powershell -ExecutionPolicy Bypass -File scripts/make_video.ps1 -ScriptPath <json> -Seed 42 -SkipRvc
#   省略 -Seed＝背景/BGM 每次隨機（與 compose.py 預設一致）
#   powershell -ExecutionPolicy Bypass -File scripts/make_video.ps1 -Generate -Source 素材.md -Id my_topic   # LLM 腳本 → 出片
param(
    [string]$ScriptPath,
    [Nullable[int]]$Seed = $null,
    [switch]$Generate,
    [string]$Source,
    [string]$Id,
    [string]$Provider,
    [string]$Model,
    [int]$MaxSections = 6,
    [switch]$SkipTts,
    [switch]$SkipRvc,
    [switch]$SkipCalibrate,
    [switch]$DryRun
)

chcp 65001 > $null
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'  # 子程序印中文時避免 cp1252 編碼錯誤
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$learntok = Join-Path $root '.venv\Scripts\learntok.exe'

if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "error: $py not found - run scripts/setup.ps1 first" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $learntok)) {
    Write-Host "error: learntok CLI not found - run scripts/setup.ps1 (or: .venv\Scripts\python.exe -m pip install -e .) first" -ForegroundColor Red
    exit 1
}

# Step 0: LLM script generation (optional, -Generate)
if ($Generate) {
    if (-not $Source) {
        Write-Host "error: -Generate 需要 -Source（學習素材 .md/.txt/.json/.srt/.pdf 或資料夾）" -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path -LiteralPath $Source)) {
        Write-Host "error: source not found: $Source" -ForegroundColor Red
        exit 1
    }
    if (-not $ScriptPath) {
        $baseName = [System.IO.Path]::GetFileNameWithoutExtension((Split-Path -Leaf $Source))
        if ($Id) { $baseName = $Id }
        $scriptId = ($baseName -replace '[^0-9a-zA-Z_]+', '_').Trim('_').ToLower()
        if (-not $scriptId) { $scriptId = 'script' }
        $ScriptPath = Join-Path $root "pipeline\examples\script_$scriptId.json"
    }
    $genArgs = @('script-gen', '--source', $Source, '--out', $ScriptPath)
    if ($Id) { $genArgs += '--id', $Id }
    if ($Provider) { $genArgs += '--provider', $Provider }
    if ($Model) { $genArgs += '--model', $Model }
    if ($MaxSections -gt 0) { $genArgs += '--max-sections', "$MaxSections" }
    if ($DryRun) { $genArgs += '--dry-run' }
    Write-Host "==> learntok script-gen $($genArgs -join ' ')" -ForegroundColor Cyan
    & $learntok @genArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (-not $DryRun) { Write-Host "==> 腳本已生成：$ScriptPath" -ForegroundColor Green }
}
elseif (-not $ScriptPath) {
    Write-Host "error: 需要 -ScriptPath，或使用 -Generate -Source <素材>" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path -LiteralPath $ScriptPath) -and -not $DryRun) {
    Write-Host "error: script not found: $ScriptPath" -ForegroundColor Red
    exit 1
}

$args = @('make', '--script', $ScriptPath)
if ($null -ne $Seed) { $args += '--seed', $Seed }
if ($SkipTts) { $args += '--skip-tts' }
if ($SkipRvc) { $args += '--skip-rvc' }
if ($SkipCalibrate) { $args += '--skip-calibrate' }
if ($DryRun) { $args += '--dry-run' }

Write-Host "==> learntok make $($args -join ' ')" -ForegroundColor Cyan
& $learntok @args
exit $LASTEXITCODE