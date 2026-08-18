# Configure local git hooks and verify sensitive paths are ignored.
# Run from repo root:  powershell -ExecutionPolicy Bypass -File tools\setup_git_secrets.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "Setting core.hooksPath -> .githooks"
git config core.hooksPath .githooks

$hook = Join-Path $Root ".githooks\pre-commit"
if (-not (Test-Path $hook)) {
    Write-Error "Missing $hook"
}
# On Unix, mark hook executable after it is tracked: git update-index --chmod=+x .githooks/pre-commit

Write-Host ""
Write-Host "Checking ignore rules for local config..."
$checks = @(
    "config/settings.json",
    "config/exception_state.json",
    "logs/pa_agent.log",
    "records/pending/test.json"
)
foreach ($p in $checks) {
    $ignored = git check-ignore -q $p 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK  ignored: $p"
    } else {
        Write-Warning "  NOT ignored: $p (review .gitignore)"
    }
}

Write-Host ""
Write-Host "Done. Pre-commit hook will block settings/logs/records from being committed."
Write-Host "Your config/settings.json stays local; use config/settings.example.json as reference."
