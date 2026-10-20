param([ValidateRange(1024, 65535)][int]$Port = 19013)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$logDirectory = Join-Path $projectRoot 'logs'
$runtimeFile = Join-Path $logDirectory 'local-acceptance.runtime.json'
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Project Python virtual environment is missing.' }
if (!(Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) { throw 'Run npm --prefix frontend run build first.' }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw "Port $Port is already in use. Existing services have not been changed." }
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

# Local overrides apply only to this launcher and its child process.
$savedRedis = $env:REDIS_URL
$savedVectors = $env:TM_VECTOR_ENABLED
$savedEncoding = $env:PYTHONIOENCODING
try {
    $env:REDIS_URL = ''
    $env:TM_VECTOR_ENABLED = 'false'
    $env:PYTHONIOENCODING = 'utf-8'
    $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @(
        'scripts/local_server.py', '--port', "$Port"
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'local-acceptance.out.log') `
        -RedirectStandardError (Join-Path $logDirectory 'local-acceptance.err.log') -PassThru
} finally {
    $env:REDIS_URL = $savedRedis
    $env:TM_VECTOR_ENABLED = $savedVectors
    $env:PYTHONIOENCODING = $savedEncoding
}
@{ ProcessId = $serverProcess.Id; StartedAt = $serverProcess.StartTime.ToString('o'); Port = $Port; Root = $projectRoot } |
    ConvertTo-Json | Set-Content -LiteralPath $runtimeFile -Encoding UTF8
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) {
        Get-Content -LiteralPath (Join-Path $logDirectory 'local-acceptance.err.log') -Tail 20
        throw 'Local server exited. Check logs/local-acceptance.err.log.'
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        if ($health.status -eq 'ok') {
            $listener = Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -First 1
            $listenerProcess = Get-Process -Id $listener.OwningProcess
            @{ ProcessId = $listenerProcess.Id; StartedAt = $listenerProcess.StartTime.ToString('o'); Port = $Port; Root = $projectRoot } |
                ConvertTo-Json | Set-Content -LiteralPath $runtimeFile -Encoding UTF8
            Write-Output "Local acceptance server ready: http://127.0.0.1:$Port (PID $($listenerProcess.Id))"
            return
        }
    } catch { }
    Start-Sleep -Milliseconds 500
}
throw 'Server is still starting. Check logs/local-acceptance.err.log before retrying.'
