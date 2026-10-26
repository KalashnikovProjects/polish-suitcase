param(
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$project = "travel-bot-int-$PID"
$usedPorts = [System.Collections.Generic.HashSet[int]]::new()
$savedPythonPath = $env:PYTHONPATH

function Get-FreePort {
    do {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
        $listener.Start()
        $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
        $listener.Stop()
    } while (-not $usedPorts.Add($port))
    return $port
}

Push-Location $projectRoot
try {
    $env:BACKEND_PORT = [string](Get-FreePort)
    $env:MONGO_PORT = [string](Get-FreePort)
    $env:RABBITMQ_PORT = [string](Get-FreePort)
    $env:RABBITMQ_MANAGEMENT_PORT = [string](Get-FreePort)
    $env:MONGO_DATABASE = 'travel_bot_integration'
    $env:MONGO_USER = 'travel_int'
    $env:MONGO_PASSWORD = [guid]::NewGuid().ToString('N')
    $env:RABBITMQ_USER = 'travel_int'
    $env:RABBITMQ_PASSWORD = [guid]::NewGuid().ToString('N')
    $env:AGENT_SERVICE_TOKEN = "integration-agent-$([guid]::NewGuid().ToString('N'))"
    $env:JWT_SECRET = "integration-jwt-$([guid]::NewGuid().ToString('N'))"
    $env:TRAVEL_INTEGRATION_BASE_URL = "http://127.0.0.1:$env:BACKEND_PORT"
    $env:TRAVEL_INTEGRATION_MONGO_URL = "mongodb://$($env:MONGO_USER):$($env:MONGO_PASSWORD)@127.0.0.1:$($env:MONGO_PORT)/?authSource=admin"
    $env:TRAVEL_INTEGRATION_RABBIT_URL = "amqp://$($env:RABBITMQ_USER):$($env:RABBITMQ_PASSWORD)@127.0.0.1:$($env:RABBITMQ_PORT)/%2F"
    $env:TRAVEL_INTEGRATION_COMPOSE_PROJECT = $project
    $env:PYTHONPATH = @('backend', 'backend/.deps', 'agent_worker/src', $savedPythonPath) -join [IO.Path]::PathSeparator

    docker compose -p $project config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration failed' }
    # These tests consume tasks themselves: never start the real worker or call paid APIs.
    docker compose -p $project up -d --build mongodb rabbitmq backend
    if ($LASTEXITCODE -ne 0) {
        docker compose -p $project ps --all
        docker compose -p $project logs --tail 60 mongodb rabbitmq backend
        throw 'Compose startup failed'
    }

    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri "$env:TRAVEL_INTEGRATION_BASE_URL/healthz" -TimeoutSec 2 -UseBasicParsing
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) {
        docker compose -p $project ps --all
        docker compose -p $project logs --tail 100 backend mongodb rabbitmq
        throw 'Backend did not become healthy'
    }

    & $Python -m pytest backend/integration_tests/test_live_backend.py -q --tb=short --show-capture=no
    if ($LASTEXITCODE -ne 0) { throw 'Live integration tests failed' }
    $env:RABBITMQ_TEST_URL = $env:TRAVEL_INTEGRATION_RABBIT_URL
    & $Python -m pytest agent_worker/tests/test_rabbitmq.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Worker broker integration test failed' }
} finally {
    docker compose -p $project down -v --remove-orphans
    $env:PYTHONPATH = $savedPythonPath
    Remove-Item Env:RABBITMQ_TEST_URL -ErrorAction SilentlyContinue
    Pop-Location
}
