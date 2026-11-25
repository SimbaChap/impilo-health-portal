# Publishes this project to https://github.com/SimbaChap/impilo-health-portal (public).
# Patient data, the database, passwords and portal logins are excluded by .gitignore,
# and this script refuses to push if any of them end up staged.
# Windows PowerShell treats anything a program writes to stderr as an error when this is 'Stop',
# and git/gh write normal progress there, so check exit codes instead.
$ErrorActionPreference = 'Continue'
$Owner = 'SimbaChap'; $Repo = 'impilo-health-portal'
Set-Location (Split-Path $PSScriptRoot -Parent)
trap { Write-Host "`n$_" -ForegroundColor Red; exit 1 }

function Need($cmd, $wingetId) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    Write-Host "Installing $cmd..." -ForegroundColor Yellow
    winget install --id $wingetId -e --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd was installed but isn't on PATH yet. Close this window and run publish-to-github.cmd again." }
  }
}
Need git 'Git.Git'
Need gh 'GitHub.cli'

$null = cmd /c "gh auth status >nul 2>&1"
if ($LASTEXITCODE -ne 0) {
  Write-Host "Sign in to GitHub as $Owner in the browser window that opens..." -ForegroundColor Cyan
  gh auth login --hostname github.com --git-protocol https --web
  if ($LASTEXITCODE -ne 0) { throw 'GitHub sign-in did not finish. Run publish-to-github.cmd again.' }
}
$who = gh api user --jq .login
if ($who -ne $Owner) { throw "GitHub is signed in as '$who', not '$Owner'. Run 'gh auth logout' and try again." }
gh auth setup-git | Out-Null

if (-not (Test-Path .git)) { git init -b main | Out-Null }
if (-not (git config user.name))  { git config user.name $Owner }
if (-not (git config user.email)) { git config user.email ((gh api user --jq .id) + "+$Owner@users.noreply.github.com") }

git add -A
$bad = git diff --cached --name-only | Where-Object { $_ -match '^(data|backups|config|run|logs)/|(^|/)\.env$|auth-data/|ai-data/|report-data/|\.venv/' }
if ($bad) { git reset -q; throw "Stopped: these private files were about to be published:`n$($bad -join "`n")" }

git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { git commit -q -m ("Update National Health Portal " + (Get-Date -Format "yyyy-MM-dd HH:mm")) }

$null = cmd /c "gh repo view $Owner/$Repo >nul 2>&1"
if ($LASTEXITCODE -ne 0) {
  gh repo create "$Owner/$Repo" --public --description "Zimbabwe MoHCC National Health Portal - Impilo registers, national overview and local AI assistant" --source . --remote origin --push
} else {
  if (-not (git remote | Select-String -Quiet '^origin$')) { git remote add origin "https://github.com/$Owner/$Repo.git" }
  gh repo edit "$Owner/$Repo" --visibility public --accept-visibility-change-consequences
  # This PC's copy is the master: replace whatever is on GitHub with it.
  git push --force -u origin main
}
if ($LASTEXITCODE -ne 0) { throw 'The push did not complete - see the message above.' }
Write-Host "`nDone: https://github.com/$Owner/$Repo" -ForegroundColor Green
