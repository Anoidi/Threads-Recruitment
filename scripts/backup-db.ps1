[CmdletBinding()]
param(
    [Parameter()]
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\backups")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-LastExitCode {
    param([Parameter(Mandatory)][string]$Operation)
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Start Docker Desktop and try again."
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
[System.IO.Directory]::CreateDirectory($resolvedOutput) | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupName = "threads-recruitment-$timestamp.sql.gz"
$backupPath = Join-Path $resolvedOutput $backupName
$partialPath = "$backupPath.partial"
$containerPath = "/tmp/$backupName"

if (Test-Path -LiteralPath $backupPath) {
    throw "Backup already exists: $backupPath. Nothing was overwritten."
}

Write-Host "Creating a transaction-consistent MySQL backup..."
try {
    $dumpCommand = 'set -e; umask 077; tmp=$(mktemp /tmp/trm-dump-XXXXXX.sql); trap ''rm -f -- "$tmp"'' EXIT; MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump --single-transaction --routines --triggers --events --hex-blob --set-gtid-purged=OFF -u root "$MYSQL_DATABASE" > "$tmp"; test -s "$tmp"; gzip -c "$tmp" > "$1"; gzip -t "$1"'
    & docker compose exec -T mysql sh -c $dumpCommand -- $containerPath
    Assert-LastExitCode "mysqldump"

    & docker compose cp "mysql:$containerPath" $partialPath
    Assert-LastExitCode "Copying the backup to the host"

    if (-not (Test-Path -LiteralPath $partialPath) -or (Get-Item -LiteralPath $partialPath).Length -eq 0) {
        throw "The backup file was not created or is empty."
    }
    Move-Item -LiteralPath $partialPath -Destination $backupPath

    Write-Host "Backup created: $backupPath"
}
finally {
    & docker compose exec -T mysql rm -f -- $containerPath 2>$null
}

