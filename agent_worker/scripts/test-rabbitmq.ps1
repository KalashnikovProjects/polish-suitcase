param([string]$Python = '.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
$agentRoot = Split-Path -Parent $PSScriptRoot
$containerName = 'ostrovok-agent-test-' + [guid]::NewGuid().ToString('N')
$testPassword = [guid]::NewGuid().ToString('N')
$savedTestUrl = $env:RABBITMQ_TEST_URL
$savedPythonPath = $env:PYTHONPATH
Push-Location $agentRoot
try {
    & docker run --detach --user 100:101 --name $containerName --label ostrovok.agent.test=true --memory 512m --cpus 1 --tmpfs '/var/lib/rabbitmq:rw,uid=100,gid=101,mode=0700' --publish '127.0.0.1::5672' --env 'RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS=+S 2:2 +A 4' --env 'RABBITMQ_CTL_ERL_ARGS=+S 1:1 +A 2' --env RABBITMQ_DEFAULT_USER=agent_test --env "RABBITMQ_DEFAULT_PASS=$testPassword" rabbitmq:4.2-alpine
    if ($LASTEXITCODE -ne 0) { throw 'Could not start disposable RabbitMQ' }
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        $running = & docker inspect --format '{{.State.Running}}' $containerName
        if ($running -ne 'true') { break }
        & docker exec $containerName rabbitmq-diagnostics -q check_running 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        & docker logs --tail 60 $containerName
        throw 'Disposable RabbitMQ did not become ready'
    }
    $binding = & docker port $containerName 5672/tcp
    $testPort = ($binding.Trim() -split ':')[-1]
    $env:RABBITMQ_TEST_URL = "amqp://agent_test:${testPassword}@127.0.0.1:${testPort}/"
    $env:PYTHONPATH = Join-Path $agentRoot 'src'
    & $Python -m pytest -q -m rabbitmq
    if ($LASTEXITCODE -ne 0) { throw 'RabbitMQ integration test failed' }
}
finally {
    & docker rm --force --volumes $containerName 2>$null | Out-Null
    $env:RABBITMQ_TEST_URL = $savedTestUrl
    $env:PYTHONPATH = $savedPythonPath
    Pop-Location
}
