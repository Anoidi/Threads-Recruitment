[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install or start Docker Desktop."
}

& docker compose config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose configuration is invalid."
}

$requiredServices = @(
    "frontend", "backend", "worker", "worker-agents", "mysql", "redis", "litellm", "adminer"
)
$actualServices = @(& docker compose config --services)
if ($LASTEXITCODE -ne 0) {
    throw "Could not list Docker Compose services."
}

$missing = @($requiredServices | Where-Object { $_ -notin $actualServices })
if ($missing.Count -gt 0) {
    throw "Missing required service(s): $($missing -join ', ')"
}

$allProfileServices = @(& docker compose --profile hermes config --services)
if ("hermes" -notin $allProfileServices) {
    throw "Missing optional profile service: hermes"
}

$configJson = (& docker compose --profile hermes config --format json) | Out-String
if ($LASTEXITCODE -ne 0) {
    throw "Could not render the Docker Compose model as JSON."
}
$config = $configJson | ConvertFrom-Json

foreach ($serviceName in $requiredServices) {
    $service = $config.services.$serviceName
    if ($service.restart -ne "unless-stopped") {
        throw "Service '$serviceName' must use restart: unless-stopped."
    }
    if ($null -eq $service.healthcheck) {
        throw "Service '$serviceName' is missing a healthcheck."
    }
}

foreach ($serviceName in @("mysql", "adminer")) {
    foreach ($publishedPort in $config.services.$serviceName.ports) {
        if ($publishedPort.host_ip -ne "127.0.0.1") {
            throw "Service '$serviceName' must bind published ports to 127.0.0.1."
        }
    }
}

foreach ($volumeName in @("mysql_data", "redis_data", "hermes_data")) {
    if ($null -eq $config.volumes.$volumeName) {
        throw "Named volume '$volumeName' is missing."
    }
}

Write-Host "Infrastructure validation passed."
Write-Host "Services: $($actualServices -join ', ')"

