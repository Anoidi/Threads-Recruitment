$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$checkpointDir = Join-Path $workspacePath '.cache/ComfyUI/models/checkpoints'
$checkpointPath = Join-Path $checkpointDir 'v1-5-pruned-emaonly-fp16.safetensors'
$expectedHash = 'e9476a13728cd75d8279f6ec8bad753a66a1957ca375a1464dc63b37db6e3916'
$expectedBytes = 2132696762
if (Test-Path -LiteralPath $checkpointPath) {
    if ((Get-FileHash -LiteralPath $checkpointPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
        throw 'Existing checkpoint hash differs. It has been preserved; inspect it before replacing.'
    }
    Write-Output 'Checkpoint already verified.'
    return
}
New-Item -ItemType Directory -Path $checkpointDir -Force | Out-Null
$partialPath = "$checkpointPath.partial"
$modelUrl = 'https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly-fp16.safetensors'
& curl.exe --fail --location --retry 3 --connect-timeout 20 --max-time 3600 --continue-at - --output $partialPath $modelUrl
if ($LASTEXITCODE -ne 0) { throw 'Checkpoint download failed; partial file retained for resume.' }
if ((Get-Item -LiteralPath $partialPath).Length -ne $expectedBytes) { throw 'Checkpoint byte count mismatch; partial file retained.' }
if ((Get-FileHash -LiteralPath $partialPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    throw 'Checkpoint SHA256 mismatch; partial file retained and not enabled.'
}
Move-Item -LiteralPath $partialPath -Destination $checkpointPath
Write-Output "Checkpoint verified: $checkpointPath"
