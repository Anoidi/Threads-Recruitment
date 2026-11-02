[CmdletBinding()]
param([string]$OutputDirectory = (Join-Path $PSScriptRoot '..\backups'))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
[IO.Directory]::CreateDirectory($outputRoot) | Out-Null
$backupName = 'media-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N') + '.zip'
$backupPath = Join-Path $outputRoot $backupName
$containerPath = '/tmp/' + $backupName
$archiveCode = @'
import sys, zipfile
from pathlib import Path
from app.config import settings
from app.comfyui import _media_write_lock
root = Path(settings.media_output_dir).resolve()
root.mkdir(parents=True, exist_ok=True)
with _media_write_lock(root), zipfile.ZipFile(sys.argv[1], 'x', compression=zipfile.ZIP_STORED) as archive:
    for asset in sorted(root.glob('asset-*')):
        if asset.is_file() and not asset.is_symlink():
            archive.write(asset, asset.name)
with zipfile.ZipFile(sys.argv[1]) as archive:
    assert archive.testzip() is None, 'Media archive integrity failure'
    print('Archived assets:', len(archive.namelist()))
'@
try {
    & docker compose exec -T backend python -c $archiveCode $containerPath
    if ($LASTEXITCODE -ne 0) { throw 'Media archive creation failed' }
    & docker compose cp "backend:$containerPath" "$backupPath.partial"
    if ($LASTEXITCODE -ne 0) { throw 'Media archive copy failed; partial file retained' }
    Move-Item -LiteralPath "$backupPath.partial" -Destination $backupPath
    Get-FileHash -LiteralPath $backupPath -Algorithm SHA256 | Select-Object Path, Hash
}
finally {
    & docker compose exec -T backend python -c 'import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)' $containerPath
}
