. (Join-Path $PSScriptRoot 'common.ps1')

$db = Find-MariaDbBin
if (-not $db -or -not (Test-Path $RootCnf) -or -not (Test-Path (Join-Path $ApiDir '.env')) -or -not (Test-Path $VenvPy)) {
    Fail 'Setup has not been completed. Double-click setup.cmd first.'
}

function Test-Port($port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $port); return $true } catch { return $false } finally { $c.Close() }
}

Step 'Starting the database'
Start-Db $db

Step "Starting the API on http://127.0.0.1:$ApiPort"
$apiHealthy = $false
try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 3; $apiHealthy = ($h.database -eq 'connected') } catch { }
$apiOwner = Get-PortOwner $ApiPort
if ($apiHealthy -and $apiOwner -and $apiOwner.CommandLine -like '*uvicorn*') {
    Info "The API is already running and healthy."
} else {
    Clear-Port $ApiPort 'uvicorn' 'API'
    $api = Start-Process -FilePath $VenvPy -WorkingDirectory $ApiDir -WindowStyle Hidden -PassThru `
        -ArgumentList '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', "$ApiPort" `
        -RedirectStandardError (Join-Path $LogDir 'api.err.log') -RedirectStandardOutput (Join-Path $LogDir 'api.out.log')
    Set-Content -Path (Join-Path $RunDir 'api.pid') -Value $api.Id
}

Step "Starting the web pages on http://127.0.0.1:$WebPort"
$webOwner = Get-PortOwner $WebPort
if ($webOwner -and $webOwner.CommandLine -like '*http.server*') {
    Info "The web pages are already being served."
} else {
    Clear-Port $WebPort 'http.server' 'web server'
    $web = Start-Process -FilePath $VenvPy -WorkingDirectory $WebDir -WindowStyle Hidden -PassThru `
        -ArgumentList '-m', 'http.server', "$WebPort", '--bind', '127.0.0.1' `
        -RedirectStandardError (Join-Path $LogDir 'web.err.log') -RedirectStandardOutput (Join-Path $LogDir 'web.out.log')
    Set-Content -Path (Join-Path $RunDir 'web.pid') -Value $web.Id
}

Step 'Checking the API'
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 5
        if ($h.database -eq 'connected') { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    $tail = (Get-Content (Join-Path $LogDir 'api.err.log') -Tail 15 -ErrorAction SilentlyContinue) -join "`n"
    Fail "The API did not come up. Last lines of $LogDir\api.err.log:`n$tail"
}
Info 'API is healthy and connected to the database.'

Start-Process "http://127.0.0.1:$WebPort/Impilo_Insights_Management_Demo.html"
Write-Host ''
Write-Host "Running. App: http://127.0.0.1:$WebPort/Impilo_Insights_Management_Demo.html   Registers: http://127.0.0.1:$WebPort/   API docs: http://127.0.0.1:$ApiPort/docs" -ForegroundColor Green
Write-Host 'Everything keeps running in the background. Double-click stop.cmd to shut it down.'
