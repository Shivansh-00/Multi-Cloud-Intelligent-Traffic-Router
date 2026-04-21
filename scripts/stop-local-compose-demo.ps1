$ErrorActionPreference = "Stop"

Push-Location (Join-Path $PSScriptRoot "..")
try {
    docker compose -f deploy/docker-compose/docker-compose.yml down --remove-orphans
}
finally {
    Pop-Location
}
