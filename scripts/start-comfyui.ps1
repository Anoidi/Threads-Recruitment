$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$comfyPath = Join-Path $workspacePath '.cache/ComfyUI'
$comfyPython = Join-Path $comfyPath '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $comfyPython)) {
    throw 'ComfyUI isolated Python environment is missing. Complete local runtime setup first.'
}
if (-not (Test-Path -LiteralPath (Join-Path $comfyPath 'main.py'))) {
    throw 'Official ComfyUI source is missing from .cache/ComfyUI.'
}
Push-Location -LiteralPath $comfyPath
try {
    & $comfyPython -u main.py --listen 127.0.0.1 --port 8188 --lowvram --offline --disable-all-custom-nodes
    if ($LASTEXITCODE -ne 0) { throw "ComfyUI exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
