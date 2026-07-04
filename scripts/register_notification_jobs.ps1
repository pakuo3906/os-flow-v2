param(
    [string]$TaskPrefix = "O's flow v2",
    [string]$WorkingDirectory = "",
    [switch]$Apply,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$repositoryRoot = if ([string]::IsNullOrWhiteSpace($WorkingDirectory)) {
    Split-Path -Parent $PSScriptRoot
} else {
    $WorkingDirectory
}

Set-Location $repositoryRoot

$launcher = Join-Path $repositoryRoot "scripts\run_notification_job.ps1"
if (-not (Test-Path $launcher)) {
    throw "Launcher script not found: $launcher"
}

$tasks = @(
    @{
        Name = "$TaskPrefix - digest"
        Schedule = @("/SC", "WEEKLY", "/D", "MON,TUE,WED,THU,FRI", "/ST", "08:00")
        Arguments = @("-Job", "digest", "-DeliverTo", "auto")
        Summary = "Daily digest on business days."
    },
    @{
        Name = "$TaskPrefix - report"
        Schedule = @("/SC", "DAILY", "/ST", "08:15")
        Arguments = @("-Job", "report", "-ReportFormat", "markdown")
        Summary = "Delivery report after the digest job."
    },
    @{
        Name = "$TaskPrefix - line-webhook-report"
        Schedule = @("/SC", "HOURLY", "/MO", "1", "/ST", "00:00")
        Arguments = @("-Job", "line-webhook-report", "-ReportFormat", "markdown")
        Summary = "Hourly LINE backlog snapshot."
    },
    @{
        Name = "$TaskPrefix - line-webhook-alerts"
        Schedule = @("/SC", "MINUTE", "/MO", "15", "/ST", "00:00")
        Arguments = @("-Job", "line-webhook-alerts", "-DeliverTo", "auto")
        Summary = "Frequent LINE backlog alerts."
    }
)

foreach ($task in $tasks) {
    $taskRun = @(
        "powershell.exe",
        "-ExecutionPolicy",
        "Bypass",
        "-NoProfile",
        "-File",
        "`"$launcher`""
    ) + $task.Arguments
    $taskRunCommand = $taskRun -join " "
    $schtasksArgs = @("/Create", "/TN", $task.Name) + $task.Schedule + @("/TR", $taskRunCommand)
    if ($Force) {
        $schtasksArgs += "/F"
    }

    if (-not $Apply) {
        [pscustomobject]@{
            task_name = $task.Name
            summary = $task.Summary
            schedule = ($task.Schedule -join " ")
            command = "schtasks.exe " + ($schtasksArgs -join " ")
        }
        continue
    }

    & schtasks.exe @schtasksArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to register task: $($task.Name)"
    }
}
