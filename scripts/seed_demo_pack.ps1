param(
    [string]$WorkingDirectory = ""
)

$ErrorActionPreference = "Stop"

$repositoryRoot = if ([string]::IsNullOrWhiteSpace($WorkingDirectory)) {
    Split-Path -Parent $PSScriptRoot
} else {
    $WorkingDirectory
}

Set-Location $repositoryRoot

$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

& $python -m app.cli.demo_pack seed
exit $LASTEXITCODE
