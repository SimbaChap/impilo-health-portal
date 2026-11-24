# 'Continue' so warnings that native tools print to stderr don't abort the script
# in Windows PowerShell 5.1; exit codes are checked explicitly instead.
$ErrorActionPreference = 'Continue'
$Root      = Split-Path -Parent $PSScriptRoot
$DataDir   = Join-Path $Root 'data'
$DbDir     = Join-Path $DataDir 'mariadb'
$ConfDir   = Join-Path $Root 'config'
$LogDir    = Join-Path $Root 'logs'
$RunDir    = Join-Path $Root 'run'
$ApiDir    = Join-Path $Root 'api'
$WebDir    = Join-Path $Root 'web'
$SqlDir    = Join-Path $Root 'sql'
$VenvPy    = Join-Path $ApiDir '.venv\Scripts\python.exe'
$ServerIni = Join-Path $ConfDir 'my.ini'
$RootCnf   = Join-Path $ConfDir 'root-client.cnf'
$DbPort    = 3307
$ApiPort   = 8765   # moved from 8000, which another program (WSL) uses on this PC
$WebPort   = 8080

foreach ($d in @($DataDir, $ConfDir, $LogDir, $RunDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}

function Step($msg) { Write-Host ''; Write-Host "==> $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" }
function Fail($msg) { Write-Host ''; Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

function Fwd($path) { return ($path -replace '\\', '/') }

function New-Secret {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ([Convert]::ToBase64String($bytes) -replace '[^A-Za-z0-9]', '')
}

function Find-MariaDbBin {
    $dirs = @()
    foreach ($base in @($env:ProgramFiles, ${env:ProgramW6432}, ${env:ProgramFiles(x86)})) {
        if ($base -and (Test-Path $base)) {
            $dirs += Get-ChildItem -Path $base -Directory -Filter 'MariaDB*' -ErrorAction SilentlyContinue |
                     Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'bin' }
        }
    }
    foreach ($bin in $dirs) {
        $server = @('mariadbd.exe', 'mysqld.exe') | ForEach-Object { Join-Path $bin $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
        $client = @('mariadb.exe', 'mysql.exe') | ForEach-Object { Join-Path $bin $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
        $admin  = @('mariadb-admin.exe', 'mysqladmin.exe') | ForEach-Object { Join-Path $bin $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
        $instdb = @('mariadb-install-db.exe', 'mysql_install_db.exe') | ForEach-Object { Join-Path $bin $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($server -and $client -and $admin -and $instdb) {
            return [pscustomobject]@{ Server = $server; Client = $client; Admin = $admin; InstallDb = $instdb }
        }
    }
    return $null
}

function Test-DbUp($db) {
    if (-not (Test-Path $RootCnf)) { return $false }
    & $db.Admin "--defaults-extra-file=$RootCnf" 'ping' 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Start-Db($db) {
    if (Test-DbUp $db) { Info "Database already running on port $DbPort."; return }
    $p = Start-Process -FilePath $db.Server -ArgumentList "--defaults-file=`"$ServerIni`"" -WindowStyle Hidden -PassThru `
         -RedirectStandardError (Join-Path $LogDir 'mariadb.err.log') -RedirectStandardOutput (Join-Path $LogDir 'mariadb.out.log')
    Set-Content -Path (Join-Path $RunDir 'mariadb.pid') -Value $p.Id
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if (Test-DbUp $db) { Info "Database started on port $DbPort."; return }
        if ($p.HasExited) { break }
    }
    Fail "The database did not start. See $LogDir\mariadb.err.log"
}

function Invoke-Sql($db, [string]$sql) {
    & $db.Client "--defaults-extra-file=$RootCnf" '--batch' '-e' $sql
    if ($LASTEXITCODE -ne 0) { Fail "SQL command failed: $sql" }
}

function Invoke-SqlFile($db, [string]$file) {
    & $db.Client "--defaults-extra-file=$RootCnf" '--max-allowed-packet=1G' '-e' ("source " + (Fwd $file))
    if ($LASTEXITCODE -ne 0) { Fail "Running $file failed." }
}

# Which process is listening on a port (null if none). Uses the Windows TCP table and process list.
function Get-PortOwner([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $c) { return $null }
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
    return [pscustomobject]@{ Id = $c.OwningProcess; Name = $p.Name; Path = $p.ExecutablePath; CommandLine = "$($p.CommandLine)" }
}

# Make sure a port is free for us. A leftover copy of our own server (recognised by its command line)
# is stopped; anything else is reported by name and the script stops instead of guessing.
function Clear-Port([int]$port, [string]$marker, [string]$what) {
    $o = Get-PortOwner $port
    if (-not $o) { return }
    if ($o.CommandLine -like "*$marker*") {
        Info "Stopping an old $what still running on port $port (process $($o.Id))."
        taskkill /PID $o.Id /T /F | Out-Null
        for ($i = 0; $i -lt 10 -and (Get-PortOwner $port); $i++) { Start-Sleep -Milliseconds 500 }
        if (Get-PortOwner $port) { Fail "Port $port is still busy after stopping the old $what. Restart the PC and run start.cmd again." }
        return
    }
    $who = if ($o.Name) { $o.Name } else { 'an unknown program' }
    Fail ("Port $port is being used by another program: $who (process $($o.Id))" + $(if ($o.Path) { " - $($o.Path)" } else { '' }) +
          ". Close that program (or end it in Task Manager), then run start.cmd again.")
}
