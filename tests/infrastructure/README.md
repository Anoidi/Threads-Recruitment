# Infrastructure checks

From the repository root in PowerShell, run:

```powershell
.\scripts\validate-infrastructure.ps1
```

The check renders the Compose model, verifies all required services, healthchecks,
restart policies, loopback-only administrative ports, and durable named volumes.
It does not start containers or contact external services.

For a full smoke test after building the stack:

```powershell
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://localhost:8000/api/health
Invoke-WebRequest http://localhost:3000/healthz
```

