# Notification Quick Reference

Use this page when you want the shortest path to the notification worker and report commands.

For broader project context, read `README.md` first; for implementation notes and project history, see `docs/handoff.md`.
For recommended cadences and cron examples, see `docs/notification_schedule.md`.
For Windows Task Scheduler runs, use `powershell -ExecutionPolicy Bypass -NoProfile -File scripts/run_notification_job.ps1`.

## Commands

| Task | Local CLI | Docker run mode | Docker Compose |
| --- | --- | --- | --- |
| Daily digest | `python -m app.cli.notification_worker digest` | `APP_RUN_MODE=notification-worker` | `docker compose run --rm notification-worker` |
| Delivery report | `python -m app.cli.notification_worker report` | `APP_RUN_MODE=notification-report` | `docker compose run --rm notification-report` |
| LINE webhook report | `python -m app.cli.notification_worker line-webhook-report` | `APP_RUN_MODE=notification-line-webhook-report` | `docker compose run --rm notification-line-webhook-report` |
| LINE webhook alerts | `python -m app.cli.notification_worker line-webhook-alerts` | `APP_RUN_MODE=notification-line-webhook-alerts` | `docker compose run --rm notification-line-webhook-alerts` |

## HTTP Endpoints

- Delivery report JSON: `GET /notification-deliveries/report`
- Delivery report Markdown: `GET /notification-deliveries/report.md`
- LINE webhook alerts JSON: `GET /line-webhooks/alerts`
- LINE webhook alerts Markdown: `GET /line-webhooks/alerts.md`

## Common Examples

```powershell
.\.venv\Scripts\python.exe -m app.cli.notification_worker digest --dry-run --deliver-to auto
.\.venv\Scripts\python.exe -m app.cli.notification_worker digest --deliver-to auto --retry-attempts 2 --retry-delay-seconds 3
.\.venv\Scripts\python.exe -m app.cli.notification_worker report --report-format markdown --report-granularity week
.\.venv\Scripts\python.exe -m app.cli.notification_worker line-webhook-report --report-format markdown
.\.venv\Scripts\python.exe -m app.cli.notification_worker line-webhook-alerts --report-format markdown --deliver-to auto
```

## Helpful Flags

- `digest` supports `--dry-run`, `--deliver-to`, `--retry-attempts`, and `--retry-delay-seconds`
- `report` supports `--report-format json|markdown` and delivery-history filters
- `line-webhook-report` supports `--report-format json|markdown`
- `line-webhook-alerts` supports `--report-format json|markdown`, `--deliver-to`, `--dry-run`, and retry flags
- All commands support `--output`

## Minimum Environment Variables

- `APP_RUN_MODE=notification-worker`, `APP_RUN_MODE=notification-report`, `APP_RUN_MODE=notification-line-webhook-report`, or `APP_RUN_MODE=notification-line-webhook-alerts` for container runs
- `NOTIFICATION_WEBHOOK_URL` for Discord webhook delivery
- `NOTIFICATION_SLACK_WEBHOOK_URL` for Slack webhook delivery
- `NOTIFICATION_LINE_CHANNEL_ACCESS_TOKEN` and `NOTIFICATION_LINE_RECIPIENT_IDS` for LINE push delivery
- `NOTIFICATION_EMAIL_SMTP_HOST` and `NOTIFICATION_EMAIL_FROM` for email delivery

## Full Help

Run:

```powershell
.\.venv\Scripts\python.exe -m app.cli.notification_worker --help
```

The help text includes the digest, report, LINE webhook report, and LINE webhook alerts examples.
