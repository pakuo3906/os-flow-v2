# Notification Schedule Guide

Use this guide when you want to run the notification worker commands on a fixed cadence.

The commands themselves are already available through `app.cli.notification_worker`, `app.cli.entrypoint`, Docker Compose, and the worker-specific environment modes.

For Windows hosts, use `scripts/run_notification_job.ps1` to keep the job invocation consistent across Task Scheduler and ad hoc runs.
If you want to generate or register the four recommended scheduled tasks together, use `scripts/register_notification_jobs.ps1`.

## Recommended Cadence

| Purpose | Recommended cadence | Command |
| --- | --- | --- |
| Daily digest | Once per business day | `python -m app.cli.notification_worker digest --deliver-to auto` |
| Delivery report | Once per day after the digest job | `python -m app.cli.notification_worker report --report-format markdown` |
| LINE webhook report | Every hour, or whenever you want a backlog snapshot | `python -m app.cli.notification_worker line-webhook-report --report-format markdown` |
| LINE webhook alerts | Every 15 minutes, or faster during high-volume periods | `python -m app.cli.notification_worker line-webhook-alerts --deliver-to auto` |

## Example Cron Entries

```cron
# Daily digest at 08:00
0 8 * * 1-5 cd /path/to/o-s-flow-v2 && ./.venv/Scripts/python.exe -m app.cli.notification_worker digest --deliver-to auto

# Delivery report at 08:15
15 8 * * 1-5 cd /path/to/o-s-flow-v2 && ./.venv/Scripts/python.exe -m app.cli.notification_worker report --report-format markdown

# LINE webhook report every hour
0 * * * * cd /path/to/o-s-flow-v2 && ./.venv/Scripts/python.exe -m app.cli.notification_worker line-webhook-report --report-format markdown

# LINE webhook alerts every 15 minutes
*/15 * * * * cd /path/to/o-s-flow-v2 && ./.venv/Scripts/python.exe -m app.cli.notification_worker line-webhook-alerts --deliver-to auto
```

## Example PowerShell Invocations

```powershell
# Daily digest
powershell -ExecutionPolicy Bypass -NoProfile -File .\scripts\run_notification_job.ps1 -Job digest -DeliverTo auto

# Delivery report
powershell -ExecutionPolicy Bypass -NoProfile -File .\scripts\run_notification_job.ps1 -Job report -ReportFormat markdown

# LINE webhook report
powershell -ExecutionPolicy Bypass -NoProfile -File .\scripts\run_notification_job.ps1 -Job line-webhook-report -ReportFormat markdown

# LINE webhook alerts
powershell -ExecutionPolicy Bypass -NoProfile -File .\scripts\run_notification_job.ps1 -Job line-webhook-alerts -DeliverTo auto
```

## Example Task Registration

```powershell
# Preview the task definitions without changing the machine
powershell -ExecutionPolicy Bypass -NoProfile -File .\scripts\register_notification_jobs.ps1

# Create or replace the scheduled tasks on the local machine
powershell -ExecutionPolicy Bypass -NoProfile -File .\scripts\register_notification_jobs.ps1 -Apply -Force
```

## Container Notes

- `APP_RUN_MODE=notification-worker`
- `APP_RUN_MODE=notification-report`
- `APP_RUN_MODE=notification-line-webhook-report`
- `APP_RUN_MODE=notification-line-webhook-alerts`

The same image can run all of the notification jobs, which keeps the deployment surface small.

## Operational Tips

- Run the report job after the digest job if you want a post-run summary in your logs.
- Use `--dry-run` before enabling a new delivery target.
- Use `--output` when you want to persist the rendered payload to a file for review or archiving.
- Keep the LINE webhook alert cadence shorter than the report cadence if backlog visibility matters more than dashboards.
- On Windows, prefer the PowerShell wrapper with `-ExecutionPolicy Bypass` so the same job shape can be reused in Task Scheduler and manual runs.
