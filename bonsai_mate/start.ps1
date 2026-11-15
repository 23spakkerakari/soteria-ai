# Start Bonsai Mate on Windows.
#   .\start.ps1                 # model server (4B, Vulkan) + app on http://localhost:8000
#   .\start.ps1 -Size 2B        # 2B | 4B | 9B
#   .\start.ps1 -NoModel        # app only: protocol cards still work without the model
#   .\start.ps1 -NoVoice        # skip the voice packages (typing only)
#   .\start.ps1 -ShimPath D:\code\argmax-windows-shim
# The first run needs internet (Python packages, llama.cpp runtime, language and speech models).
# After that it runs offline.
param(
    [ValidateSet("2B", "4B", "9B")] [string]$Size = "4B",
    [ValidateSet("vulkan", "cpu", "cuda-12.4", "cuda-13.3")] [string]$Backend = "vulkan",
    [int]$Port = 8000,
    [string]$ShimPath = $env:ARGMAX_SHIM_PATH,
    [switch]$NoModel,
    [switch]$NoVoice
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    python -m venv (Join-Path $root ".venv")
    & $python -m pip install --quiet -r (Join-Path $root "requirements.txt")
}

if (-not $NoVoice) {
    # Voice runs on the Argmax SDK API. On Windows that is the development shim.
    & $python -c "import argmax_shim, faster_whisper, kokoro" 2>$null
    if ($LASTEXITCODE -ne 0) {
        if (-not $ShimPath) {
            $ShimPath = @((Join-Path $root "..\..\argmax-windows-shim"), (Join-Path $HOME "argmax-windows-shim")) |
                Where-Object { Test-Path $_ } | Select-Object -First 1
        }
        if ($ShimPath) {
            Write-Host "Installing voice packages from $ShimPath (large download the first time)..."
            & $python -m pip install --quiet -e "$ShimPath[whisper,tts-kokoro]"
        } else {
            Write-Host "argmax-windows-shim not found: voice is off. Pass -ShimPath <folder> to enable it."
        }
    }
    # Once the speech models are downloaded, never go looking for the network again.
    $hub = Join-Path $HOME ".cache\huggingface\hub"
    if ((Test-Path "$hub\models--Systran--faster-whisper-base.en") -and (Test-Path "$hub\models--hexgrad--Kokoro-82M")) {
        $env:HF_HUB_OFFLINE = "1"
    }
}

if (-not $NoModel) {
    # Reuse the Bonsai sample's launcher: it downloads the runtime and model if needed
    # and serves an OpenAI-compatible API on port 8080, in its own window.
    $llm = Join-Path $root "..\bonsai-models\gguf\start.ps1"
    Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-File", "`"$llm`"", "-Size", $Size, "-Backend", $Backend
}

Write-Host "`nOpen http://localhost:$Port in your browser.`n"
Set-Location $root
& $python -m uvicorn backend.main:app --port $Port
