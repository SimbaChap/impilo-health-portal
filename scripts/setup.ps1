param([string]$DumpPath)
. (Join-Path $PSScriptRoot 'common.ps1')

Write-Host 'Impilo Registers - one-time setup' -ForegroundColor Green
Write-Host "Project folder: $Root"

# ---------------------------------------------------------------- 1. MariaDB
Step 'Checking for MariaDB'
$db = Find-MariaDbBin
if (-not $db) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail 'MariaDB is not installed and winget is not available. Install MariaDB Server from https://mariadb.org/download/ and run setup again.'
    }
    Info 'Installing MariaDB Server with winget (Windows may ask for administrator approval)...'
    # REMOVE=DBInstance installs the programs only, without creating a Windows service on port 3306.
    winget install -e --id MariaDB.Server --accept-package-agreements --accept-source-agreements --override 'ADDLOCAL=ALL REMOVE=DBInstance /qn'
    $db = Find-MariaDbBin
    if (-not $db) {
        Info 'Retrying with the default installer options...'
        winget install -e --id MariaDB.Server --accept-package-agreements --accept-source-agreements
        $db = Find-MariaDbBin
    }
    if (-not $db) { Fail 'MariaDB installation did not complete. Install it from https://mariadb.org/download/ and run setup again.' }
}
Info "Using $($db.Server)"

# ---------------------------------------------------------------- 2. Python 3.12/3.13, 64-bit
# 64-bit 3.12/3.13 have ready-made packages for everything the API needs. Newer or 32-bit
# Pythons make pip compile packages from source, which needs Visual C++ Build Tools.
Step 'Checking for 64-bit Python 3.12 or 3.13'
$PyCheck = 'import sys, struct; print(sys.version_info[:2] in ((3, 12), (3, 13)) and struct.calcsize(''P'') == 8)'
function Test-PyCandidate($c) {
    $exe = $c[0]; $rest = @($c | Select-Object -Skip 1)
    $ok = & $exe @rest -c $PyCheck 2>$null
    return ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq 'True')
}
function Find-Python {
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $cands += ,@('py', '-3.13-64'); $cands += ,@('py', '-3.12-64')
    }
    foreach ($v in @('313', '312')) {
        $cands += ,@(Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe")
        $cands += ,@(Join-Path $env:ProgramFiles "Python$v\python.exe")
    }
    foreach ($c in $cands) {
        if ($c[0] -ne 'py' -and -not (Test-Path $c[0])) { continue }
        if (Test-PyCandidate $c) { return ,$c }
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Fail 'Python 3.12 (64-bit) is required. Install it from https://www.python.org/downloads/ and run setup again.' }
    Info 'Installing Python 3.12 (64-bit) with winget - your other Python versions are left alone...'
    winget install -e --id Python.Python.3.12 --scope user --architecture x64 --accept-package-agreements --accept-source-agreements
    $py = Find-Python
    if (-not $py) { Fail 'Python 3.12 (64-bit) was not found after installing. Close this window, open a new one and run setup again.' }
}
Info "Using Python: $($py -join ' ')"

# Rebuild the API environment if it was made with a different Python (e.g. 32-bit 3.14).
$VenvDir = Join-Path $ApiDir '.venv'
if ((Test-Path $VenvPy) -and -not (Test-PyCandidate @($VenvPy))) {
    Info 'Removing the old API environment (wrong Python version)...'
    Remove-Item -Recurse -Force $VenvDir
}
if (-not (Test-Path $VenvPy)) {
    Info 'Creating the API virtual environment...'
    $pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
    & $pyExe @pyArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail 'Could not create the Python virtual environment.' }
}
& $VenvPy -m pip install --disable-pip-version-check -q --upgrade pip
Info 'Installing API packages (FastAPI, MySQL connector, openpyxl, reportlab)...'
& $VenvPy -m pip install --disable-pip-version-check -q -r (Join-Path $ApiDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail 'Installing Python packages failed. Copy the red error text above into the chat.' }

# ---------------------------------------------------------------- 3. Database instance on port 3307
Step "Preparing a private database instance on port $DbPort"
if (-not (Test-Path (Join-Path $DbDir 'mysql'))) {
    $rootPw = New-Secret
    & $db.InstallDb "--datadir=$DbDir" "--port=$DbPort" "--password=$rootPw"
    if ($LASTEXITCODE -ne 0) { Fail 'Creating the database instance failed.' }
    Set-Content -Path $RootCnf -Encoding ASCII -Value @(
        '[client]', 'host=127.0.0.1', "port=$DbPort", 'user=root', "password=$rootPw")
    Info 'Database instance created.'
}
Set-Content -Path $ServerIni -Encoding ASCII -Value @(
    '[mysqld]',
    "datadir=$(Fwd $DbDir)",
    "port=$DbPort",
    'bind-address=127.0.0.1',
    'skip-name-resolve',
    'sql_mode=NO_ENGINE_SUBSTITUTION',
    'max_allowed_packet=1G',
    'innodb_buffer_pool_size=1G',
    'innodb_log_file_size=512M',
    'innodb_flush_log_at_trx_commit=2',
    "log-error=$(Fwd (Join-Path $LogDir 'mariadb-server.log'))")
Start-Db $db

# ---------------------------------------------------------------- 4. Import the Avondale dump
$importedFlag = Join-Path $DataDir '.imported'
if (-not (Test-Path $importedFlag)) {
    Step 'Locating Avondale_application_only.sql'
    $dumpName = 'Avondale_application_only.sql'
    if (-not $DumpPath) {
        $searchRoots = @($Root, (Join-Path $env:USERPROFILE 'Downloads'), (Join-Path $env:USERPROFILE 'Documents'), (Join-Path $env:USERPROFILE 'Desktop')) | Where-Object { Test-Path $_ }
        foreach ($s in $searchRoots) {
            $hit = Get-ChildItem -Path $s -Filter $dumpName -Recurse -Depth 5 -File -ErrorAction SilentlyContinue |
                   Where-Object { $_.Length -gt 1GB } | Select-Object -First 1
            if ($hit) { $DumpPath = $hit.FullName; break }
        }
    }
    if (-not $DumpPath) {
        Info 'Not found as a file; looking inside 2026-11-02*.zip ...'
        Add-Type -AssemblyName System.IO.Compression
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zips = foreach ($s in @((Join-Path $env:USERPROFILE 'Downloads'), (Join-Path $env:USERPROFILE 'Documents'), (Join-Path $env:USERPROFILE 'Desktop'))) {
            if (Test-Path $s) { Get-ChildItem -Path $s -Filter '2026-11-02*.zip' -Recurse -Depth 3 -File -ErrorAction SilentlyContinue }
        }
        foreach ($z in $zips) {
            $zip = [System.IO.Compression.ZipFile]::OpenRead($z.FullName)
            try {
                $entry = $zip.Entries | Where-Object { $_.Name -eq $dumpName -and $_.FullName -notlike '__MACOSX*' } | Select-Object -First 1
                if ($entry) {
                    $DumpPath = Join-Path $DataDir $dumpName
                    Info "Extracting from $($z.FullName) (2.1 GB, a few minutes)..."
                    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $DumpPath, $true)
                    break
                }
            } finally { $zip.Dispose() }
        }
    }
    if (-not $DumpPath -or -not (Test-Path $DumpPath)) {
        Fail "Could not find $dumpName. Run: setup.cmd -DumpPath ""C:\path\to\$dumpName"""
    }
    Info "Using $DumpPath"

    Step 'Importing the database (2.1 GB - this usually takes 10 to 40 minutes, please leave this window open)'
    $t0 = Get-Date
    Invoke-SqlFile $db $DumpPath
    Set-Content -Path $importedFlag -Value (Get-Date -Format o)
    Info ("Import finished in {0:N0} minutes." -f ((Get-Date) - $t0).TotalMinutes)
} else {
    Info 'Database already imported - skipping.'
}

# ---------------------------------------------------------------- 5. Read-only API user
Step 'Creating the read-only attendance_api user'
$apiPw = New-Secret
Invoke-Sql $db "CREATE USER IF NOT EXISTS 'attendance_api'@'127.0.0.1'; ALTER USER 'attendance_api'@'127.0.0.1' IDENTIFIED BY '$apiPw';"
Get-ChildItem -Path $SqlDir -Filter '*.sql' | Sort-Object Name | ForEach-Object {
    Info "Applying $($_.Name)"
    Invoke-SqlFile $db $_.FullName
}
Invoke-Sql $db 'FLUSH PRIVILEGES;'
Set-Content -Path (Join-Path $ApiDir '.env') -Encoding ASCII -Value @(
    'DB_HOST=127.0.0.1', "DB_PORT=$DbPort", 'DB_USER=attendance_api', "DB_PASSWORD=""$apiPw""")
Info 'Saved the API connection settings to api\.env'

$check = & $db.Client "--defaults-extra-file=$RootCnf" '-N' '-e' "SELECT COUNT(*) FROM report.patient_register WHERE date BETWEEN '2025-08-01' AND '2025-08-31'"
Info "Check: August 2025 attendance rows = $check (expected 820)"

Write-Host ''
Write-Host 'Setup complete. Double-click start.cmd to run the registers.' -ForegroundColor Green
