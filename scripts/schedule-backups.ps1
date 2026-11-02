# Scheduled backups for Threads Recruitment Manager (audit D14 / I-5, phase 1.3).
#
# Modes:
#   (default)     Dry run: prints exactly what would be registered/executed and
#                 validates paths. Nothing is changed on the system.
#   -Register     Creates (or replaces) a daily Windows Scheduled Task that
#                 runs scripts/backup-db.ps1, optionally copies the newest
#                 archive to a second drive and prunes old copies everywhere.
#   -Run          The mode the scheduled task calls: one backup cycle now
#                 (backup + secondary copy + retention).
#
# Examples:
#   powershell -ExecutionPolicy Bypass -File scripts\schedule-backups.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\schedule-backups.ps1 -Register -SecondaryPath "D:\Backups\threads-recruitment"
#   powershell -ExecutionPolicy Bypass -File scripts\schedule-backups.ps1 -Run
#
# Monthly restore drill goes to a temporary database; see README.md,
# section "Проверка восстановления" — it must stay a manual, verified step.

[CmdletBinding()]
param(
    [switch]$Register,
    [switch]$Run,
    [string]$SecondaryPath = "",
    [int]$KeepCopies = 14,
    [string]$At = "03:30"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$taskName = "Threads Recruitment - DB Backup"
$backupScript = Join-Path $PSScriptRoot "backup-db.ps1"
$mainBackupDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\backups"))

function Assert-Environment {
    if (-not (Test-Path -LiteralPath $backupScript)) {
        throw "backup-db.ps1 not found next to this script: $backupScript"
    }
    if ($KeepCopies -lt 2) {
        throw "KeepCopies must be at least 2 (never prune down to a single archive)."
    }
    if ($SecondaryPath -and -not (Test-Path -LiteralPath $SecondaryPath -PathType Container)) {
        # Created on first run; dry run only reports the path that would be created.
        Write-Host "Note: secondary path does not exist yet, it will be created on first copy:"
        Write-Host "  $SecondaryPath"
    }
}

function Get-TaskCommandRaw {
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $backupScript)
    if ($SecondaryPath) {
        $argList += @("-SecondaryPath", $SecondaryPath, "-KeepCopies", $KeepCopies)
    }
    return ($argList | ForEach-Object { if ($_ -match '\s') { '"{0}"' -f $_ } else { $_ } }) -join " "
}

function Invoke-BackupCycle {
    # One cycle: primary dump, optional secondary copy, retention prune.
    & $backupScript
    if ($LASTEXITCODE -ne 0) { throw "backup-db.ps1 failed with exit code $LASTEXITCODE." }

    $newest = Get-ChildItem -LiteralPath $mainBackupDir -Filter "threads-recruitment-*.sql.gz" |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $newest) { throw "Backup finished but no threads-recruitment-*.sql.gz was found in $mainBackupDir." }

    if ($SecondaryPath) {
        if (-not (Test-Path -LiteralPath $SecondaryPath -PathType Container)) {
            [System.IO.Directory]::CreateDirectory($SecondaryPath) | Out-Null
        }
        Copy-Item -LiteralPath $newest.FullName -Destination (Join-Path $SecondaryPath $newest.Name) -Force
        Write-Host "Secondary copy written: $(Join-Path $SecondaryPath $newest.Name)"
        Prune-Archives -Directory $SecondaryPath
    }
    Prune-Archives -Directory $mainBackupDir
}

function Prune-Archives {
    param([Parameter(Mandatory)][string]$Directory)
    $victims = Get-ChildItem -LiteralPath $Directory -Filter "threads-recruitment-*.sql.gz" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip $KeepCopies
    foreach ($victim in $victims) {
        Remove-Item -LiteralPath $victim.FullName -Force
        Write-Host "Pruned old archive: $($victim.FullName)"
    }
}

function Register-BackupTask {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (Get-TaskCommandRaw)
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Daily Threads Recruitment MySQL backup (backup-db.ps1), secondary copy and retention." -Force | Out-Null
    Write-Host "Scheduled task '$taskName' registered: daily at $At."
}

function Get-TaskCommandRaw {
    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $backupScript)
    if ($SecondaryPath) {
        $args += @("-SecondaryPath", $SecondaryPath, "-KeepCopies", $KeepCopies)
    }
    return ($args | ForEach-Object { if ($_ -match '\s') { '"{0}"' -f $_ } else { $_ } }) -join " "
}

Assert-Environment

if ($Register) {
    Register-BackupTask
} elseif ($Run) {
    Invoke-BackupCycle
} else {
    Write-Host "DRY RUN - nothing was registered or executed."
    Write-Host ("Would register task '{0}': daily at {1}." -f $taskName, $At)
    Write-Host ("  action: powershell.exe {0}" -f (Get-TaskCommandRaw))
    Write-Host ("  backup script: {0}" -f $backupScript)
    Write-Host ("  main backups dir: {0}" -f $mainBackupDir)
    Write-Host ("  secondary copies: {0}" -f ($(if ($SecondaryPath) { $SecondaryPath } else { "(not configured; pass -SecondaryPath)" })))
    Write-Host ("  retention: keep {0} newest archives per location" -f $KeepCopies)
    Write-Host "Run with -Register to create the task, or -Run to execute one backup cycle now."
}
