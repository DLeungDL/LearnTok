# LearnTok AI - verification renders (A: vertical, B: landscape crop, C: landscape blur)
$ErrorActionPreference = "Stop"
# 路徑自動由腳本位置推導（pipeline/tools -> 專案根目錄）
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

# Python：優先使用專案內 .venv，其次 PATH 上的 python
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $py) {
    Write-Host "python not found. Run scripts/setup.ps1 first." -ForegroundColor Red
    exit 1
}

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
  $pkg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($pkg) { $env:Path = "$($pkg.DirectoryName);$env:Path" }
}
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) { Write-Host "ffmpeg NOT found. Please run: winget install --id Gyan.FFmpeg -e --silent"; exit 1 }
Write-Host "ffmpeg found: $($ffmpeg.Source)"

New-Item -ItemType Directory -Path "output\verify_frames" -Force | Out-Null

$jobs = @(
  @{ name = "A_vertical";  manifest = "pipeline\build\verify_vertical.json"  },
  @{ name = "B_land_crop"; manifest = "pipeline\build\verify_land_crop.json" },
  @{ name = "C_land_blur"; manifest = "pipeline\build\verify_land_blur.json" }
)

foreach ($j in $jobs) {
  $out = "output\verify_$($j.name).mp4"
  Write-Host "`n=== render $($j.name) -> $out ==="
  & $py -m learntok.compose --script pipeline\examples\sample_script.json --manifest $j.manifest --out $out --max-duration 30 --seed 7
  if ($LASTEXITCODE -ne 0) { Write-Host "render failed: $($j.name)"; exit 1 }
  foreach ($t in @(8, 20)) {
    $frame = "output\verify_frames\$($j.name)_t$($t)s.png"
    & ffmpeg -y -ss $t -i $out -frames:v 1 -q:v 2 $frame 2>$null
    Write-Host "frame: $frame"
  }
}
Write-Host "`nAll verification renders complete."