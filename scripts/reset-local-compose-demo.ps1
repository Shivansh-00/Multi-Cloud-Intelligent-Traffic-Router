$ErrorActionPreference = "Stop"

Push-Location (Join-Path $PSScriptRoot "..")
try {
    docker compose -f deploy/docker-compose/docker-compose.yml up -d sample-app-aws
}
finally {
    Pop-Location
}
