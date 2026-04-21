param(
    [int]$WarmupRequests = 20,
    [int]$FailoverWaitSeconds = 6
)

$ErrorActionPreference = "Stop"

Push-Location (Join-Path $PSScriptRoot "..")
try {
    docker compose -f deploy/docker-compose/docker-compose.yml up -d --build

    Start-Sleep -Seconds 10

    for ($i = 0; $i -lt $WarmupRequests; $i++) {
        Invoke-RestMethod -Uri "http://localhost:8080/api/echo" | Out-Null
    }

    $initialStatus = Invoke-RestMethod -Uri "http://localhost:8080/router/status"

    docker compose -f deploy/docker-compose/docker-compose.yml stop sample-app-aws | Out-Null
    Start-Sleep -Seconds $FailoverWaitSeconds

    for ($i = 0; $i -lt 10; $i++) {
        Invoke-RestMethod -Uri "http://localhost:8080/api/echo" | Out-Null
    }

    $failoverStatus = Invoke-RestMethod -Uri "http://localhost:8080/router/status"

    [pscustomobject]@{
        InitialActiveBackend = $initialStatus.active_backend
        FinalActiveBackend = $failoverStatus.active_backend
        RecentEvents = $failoverStatus.recent_events
        Backends = $failoverStatus.backends
    } | ConvertTo-Json -Depth 6
}
finally {
    Pop-Location
}
