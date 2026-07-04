param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("digest", "report", "line-webhook-report", "line-webhook-alerts")]
    [string]$Job,

    [string]$DeliverTo = "",
    [ValidateSet("json", "markdown")]
    [string]$ReportFormat = "json",
    [int]$RetryAttempts = 0,
    [double]$RetryDelaySeconds = 1.0,
    [switch]$DryRun,
    [string]$Output = "",
    [string]$AsOf = "",
    [int]$ReportLimit = 20,
    [int]$PendingBacklogThreshold = 5,
    [int]$DueLookaheadDays = 1,
    [int]$InvoiceLookaheadDays = 7,
    [string]$CaseStatus = "in_progress",
    [string]$InvoiceStatus = "pending",
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

$arguments = @("-m", "app.cli.notification_worker", $Job)

if ($Job -eq "digest") {
    if ($AsOf) { $arguments += @("--as-of", $AsOf) }
    $arguments += @("--due-lookahead-days", "$DueLookaheadDays")
    $arguments += @("--invoice-lookahead-days", "$InvoiceLookaheadDays")
    $arguments += @("--case-status", $CaseStatus)
    $arguments += @("--invoice-status", $InvoiceStatus)
    if ($DeliverTo) { $arguments += @("--deliver-to", $DeliverTo) }
    if ($DryRun) { $arguments += "--dry-run" }
    if ($RetryAttempts -gt 0) { $arguments += @("--retry-attempts", "$RetryAttempts") }
    if ($RetryDelaySeconds -ne 1.0) { $arguments += @("--retry-delay-seconds", "$RetryDelaySeconds") }
}
elseif ($Job -eq "report") {
    $arguments += @("--report-format", $ReportFormat)
    if ($DeliverTo) { $arguments += @("--report-deliver-to", $DeliverTo) }
}
elseif ($Job -eq "line-webhook-report") {
    $arguments += @("--report-format", $ReportFormat)
    $arguments += @("--report-limit", "$ReportLimit")
    $arguments += @("--report-pending-backlog-threshold", "$PendingBacklogThreshold")
}
elseif ($Job -eq "line-webhook-alerts") {
    $arguments += @("--report-format", $ReportFormat)
    $arguments += @("--report-limit", "$ReportLimit")
    $arguments += @("--report-pending-backlog-threshold", "$PendingBacklogThreshold")
    if ($DeliverTo) { $arguments += @("--deliver-to", $DeliverTo) }
    if ($DryRun) { $arguments += "--dry-run" }
    if ($RetryAttempts -gt 0) { $arguments += @("--retry-attempts", "$RetryAttempts") }
    if ($RetryDelaySeconds -ne 1.0) { $arguments += @("--retry-delay-seconds", "$RetryDelaySeconds") }
}

if ($Output) {
    $arguments += @("--output", $Output)
}

& $python @arguments
exit $LASTEXITCODE
