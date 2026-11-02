[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$BackupFile,

    [Parameter(Mandatory)]
    [string]$ConfirmDatabase,

    [Parameter()]
    [string]$TargetDatabase = "",

    [Parameter()]
    [switch]$ConfirmRestore
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

$source = (Resolve-Path -LiteralPath $BackupFile).Path
$isGzip = $source.EndsWith(".sql.gz", [System.StringComparison]::OrdinalIgnoreCase)
$isSql = $source.EndsWith(".sql", [System.StringComparison]::OrdinalIgnoreCase)
if (-not ($isGzip -or $isSql)) {
    throw "Backup must end in .sql.gz or .sql."
}

$envDatabaseName = (& docker compose exec -T mysql printenv MYSQL_DATABASE)
Assert-LastExitCode "Reading the target database name"
$envDatabaseName = ($envDatabaseName | Out-String).Trim()

# Default keeps the historic behaviour: restore into the configured database.
# A -TargetDatabase limited to threads_restore_test_* names enables the
# monthly restore drill without ever touching real data.
if ($TargetDatabase) {
    if (-not $TargetDatabase.StartsWith("threads_restore_test_", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "-TargetDatabase may only be used for drill databases named threads_restore_test_<suffix>."
    }
    $databaseName = $TargetDatabase
    & docker compose exec -T mysql sh -c 'set -e; MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -u root -e "CREATE DATABASE IF NOT EXISTS \`$1\`"' -- $databaseName
    Assert-LastExitCode "Creating the drill database"
}
else {
    $databaseName = $envDatabaseName
}

if ($ConfirmDatabase -cne $databaseName) {
    throw "Confirmation mismatch. Pass -ConfirmDatabase '$databaseName' to target this database explicitly."
}

if (-not $ConfirmRestore) {
    $answer = Read-Host "Restore into '$databaseName'? Existing tables may be replaced. Type RESTORE to continue"
    if ($answer -cne "RESTORE") {
        Write-Host "Restore cancelled. No database changes were made."
        exit 0
    }
}

$suffix = if ($isGzip) { ".sql.gz" } else { ".sql" }
$containerPath = "/tmp/trm-restore-$([Guid]::NewGuid().ToString('N'))$suffix"

Write-Host "Restoring '$source' into '$databaseName'..."
try {
    & docker compose cp $source "mysql:$containerPath"
    Assert-LastExitCode "Copying the backup into the MySQL container"

    if ($isGzip) {
        $restoreCommand = 'set -e; umask 077; gzip -t "$1"; tmp=$(mktemp /tmp/trm-restore-XXXXXX.sql); trap ''rm -f -- "$tmp"'' EXIT; gzip -dc "$1" > "$tmp"; test -s "$tmp"; MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --binary-mode=1 -u root "$MYSQL_DATABASE" < "$tmp"'
    }
    else {
        $restoreCommand = 'set -e; test -s "$1"; MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --binary-mode=1 -u root "$MYSQL_DATABASE" < "$1"'
    }

    & docker compose exec -T mysql sh -c $restoreCommand -- $containerPath
    Assert-LastExitCode "Restoring the database"
    Write-Host "Restore completed for '$databaseName'."
}
finally {
    & docker compose exec -T mysql rm -f -- $containerPath 2>$null
}

