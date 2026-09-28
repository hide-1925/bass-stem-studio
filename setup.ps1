# Bass Stem Studio setup (Windows / PowerShell)
#   powershell -ExecutionPolicy Bypass -File setup.ps1          # CPU (default)
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -Gpu     # NVIDIA GPU (CUDA 12.6 build of torch)
param([switch]$Gpu)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = $null
foreach ($cand in @("py -3.11", "python")) {
    try {
        $v = & ([scriptblock]::Create("$cand -c `"import sys; print('%d.%d' % sys.version_info[:2])`"")) 2>$null
        if ($v -match '^3\.(10|11|12)$') { $py = $cand; break }
    } catch {}
}
if (-not $py) { throw "Python 3.10-3.12 not found (3.11 recommended). Install it from https://www.python.org/" }
Write-Host "Python: $py ($v)"

if (-not (Test-Path .venv)) { & ([scriptblock]::Create("$py -m venv .venv")) }
$vpy = ".\.venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip

if ($Gpu) {
    # cu126 still supports Pascal GPUs such as the GTX 10 series
    & $vpy -m pip install torch --index-url https://download.pytorch.org/whl/cu126
} else {
    & $vpy -m pip install torch --index-url https://download.pytorch.org/whl/cpu
}
& $vpy -m pip install -r requirements.txt
# Basic Pitch's metadata would pull TensorFlow on Windows/Python 3.11+; the bundled ONNX model only needs onnxruntime.
& $vpy -m pip install basic-pitch==0.4.0 --no-deps

Write-Host ""
Write-Host "The Demucs model (about 110 MB) is downloaded automatically on the first separation."
Write-Host "Setup finished. Start the app with run.bat"
