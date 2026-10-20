$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeFile = Join-Path $projectRoot 'logs\local-acceptance.runtime.json'
if (!(Test-Path -LiteralPath $runtimeFile)) { throw 'No local acceptance server record was found.' }
$runtime = Get-Content -LiteralPath $runtimeFile -Raw | ConvertFrom-Json
$serverProcess = Get-Process -Id $runtime.ProcessId -ErrorAction SilentlyContinue
if (!$serverProcess) { Write-Output 'Local acceptance server is already stopped.'; return }
$command = (Get-CimInstance Win32_Process -Filter "ProcessId = $($runtime.ProcessId)").CommandLine
if ($runtime.Root -ne $projectRoot -or $serverProcess.StartTime.ToUniversalTime().Ticks -ne ([datetime]$runtime.StartedAt).ToUniversalTime().Ticks `
    -or $command -notmatch 'scripts[/\\]local_server\.py' -or $command -notmatch "--port\s+$($runtime.Port)(\s|$)") {
    throw 'The recorded process does not match this local server. Nothing was stopped.'
}
Stop-Process -Id $serverProcess.Id
Write-Output 'Local acceptance server stopped.'
