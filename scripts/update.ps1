. (Join-Path $PSScriptRoot 'common.ps1')
# Applies new SQL scripts (for example the facility registry) and restarts the API
# so code changes take effect. Safe to run any time; it does not re-import the data.

$db = Find-MariaDbBin
if (-not $db -or -not (Test-Path $RootCnf)) { Fail 'Setup has not been completed. Double-click setup.cmd first.' }

Step 'Starting the database'
Start-Db $db

Step 'Applying SQL scripts'
Get-ChildItem -Path $SqlDir -Filter '*.sql' | Sort-Object Name | ForEach-Object {
    Info "Applying $($_.Name)"
    Invoke-SqlFile $db $_.FullName
}
Invoke-Sql $db 'FLUSH PRIVILEGES;'
$reg = & $db.Client "--defaults-extra-file=$RootCnf" '-N' '-e' 'SELECT COUNT(*) FROM registry.facility_registry'
Info "Facility registry: $reg facilities"

Step 'Restarting the API'
Remove-Item (Join-Path $RunDir 'api.pid') -Force -ErrorAction SilentlyContinue
Clear-Port $ApiPort 'uvicorn' 'API'
& (Join-Path $PSScriptRoot 'start.ps1')
