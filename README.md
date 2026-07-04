# O's flow V2

O's flow V2 is the next production-oriented version of O's flow.

## Direction

- O's flow is a system for ingesting, organizing, storing, and reusing business data.
- RAG is one component inside that system, not the main product itself.
- Keep the current Discord intake flow as a reference, not as the system of record.
- Build the new version around a ledger-first architecture.
- Treat SQLite as the development/test source of truth for cases, documents, artifacts, and RAG index metadata.
- Treat InsForge/PostgreSQL as the production and sales backend target.
- Treat file storage and RAG output as replaceable adapters so the system can move to InsForge Storage, S3, GCS, or other managed services.
- Add API and MCP-facing interfaces incrementally instead of rewriting everything at once.
- Keep the codebase compatible with Docker and future Vercel-style container deployment.
- Treat Vercel as an optional deployment target, not a required part of the core architecture.

## Decisions

- [docs/decisions.md](docs/decisions.md) records the working agreement and the direction we are holding constant while development continues.
- [docs/implementation_directive.md](docs/implementation_directive.md) is the fixed instruction document future AI agents should read first.
- [docs/product_scope.md](docs/product_scope.md) records the intended product behavior, target workflows, and architecture implications.
- [docs/handoff.md](docs/handoff.md) records the current implementation state and the fastest safe continuation path for the next agent.
- [docs/notification_schedule.md](docs/notification_schedule.md) gives the recommended cadence for notification worker jobs.
- [scripts/run_notification_job.ps1](scripts/run_notification_job.ps1) is the Windows-friendly launcher for notification worker jobs.
- [scripts/register_notification_jobs.ps1](scripts/register_notification_jobs.ps1) previews or registers the recommended Windows scheduled tasks.

## Current Workspace Layout

- `discord-fill-bot/`: reference implementation from the earlier MVP
- `app/`: new V2 application skeleton
- `docs/`: design and decision notes
- `README.md`: project overview for O's flow V2

## Next Build Targets

- SQLite schema and repository layer
- Storage adapter abstraction
- Ingestion service
- RAG output generation
- MCP/business search interfaces
- FastAPI service surface

## Current Status

- SQLite repository scaffold is in place.
- Local storage adapter scaffold is in place.
- Runtime adapter selection is centralized so SQLite/local remain the defaults while future backends can be swapped in behind a single factory.
- Repository-backed business query tools are available as a reusable MCP-facing layer.
- A stdio MCP server entrypoint is available for local and agent-hosted use.
- A minimal `/mcp` HTTP transport is available through the existing FastAPI app.
- The `/mcp` HTTP transport now enforces session-based initialize flow and accepts MCP notifications as `202`.
- MCP resources are available for summary, case, and document reads.
- MCP resource templates and prompts are available for case and document workflows.
- MCP resource subscribe and unsubscribe bookkeeping is available in the stdio and HTTP transports for session-scoped tracking.
- The MCP resource `oflow://mcp/subscriptions` exposes the current subscription snapshot.
- The MCP resource `oflow://mcp/events` exposes the current queued event snapshot.
- `/mcp/subscriptions` exposes the current session subscription snapshot for admin-style inspection.
- `/mcp/events` exposes the current queued MCP event snapshot for admin-style inspection, with `session_id`, `event_type`, and `resource_uri` filters plus per-type counts.
- `/mcp/overview` combines the current MCP subscription and event snapshots, including per-type counts.
- `/mcp/dashboard` exposes the compact MCP operational summary for admin-style inspection.
- The MCP dashboard summary includes per-event-type, per-resource, and top-resource counts.
- `GET /mcp` now drains queued subscription-change events as SSE lines before the keep-alive comment.
- Case, document, and ingestion mutations now queue resource-change notifications for subscribed MCP sessions.
- MCP requests are recorded into the operation log for audit visibility.
- `GET /admin/overview` exposes a lightweight admin-facing snapshot with backend configuration flags and system counts.
- `GET /admin/overview` also includes status breakdowns for cases, invoices, outputs, and document source types.
- `GET /admin/recent` exposes the latest cases, documents, operation logs, and notification deliveries for quick operator inspection.
- `GET /admin/activity` exposes a merged admin timeline for cases, documents, operation logs, and notification deliveries.
- Ingestion service scaffold is in place.
- Processing job ledger and API visibility are in place.
- Dockerfile is in place for containerized API startup.
- JSON ingestion API is in place for API-based uploads.
- Multipart file upload ingestion API is in place for direct file uploads.
- Document deletion API is in place with storage cleanup.
- Document listing, search, and fetch APIs are in place.
- Document reprocess API is in place for storage refresh and RAG rebuild.
- Document bulk reprocess API is in place for selected document refresh.
- Document reassign API is in place for operational case corrections.
- Case create/upsert API is in place for ledger registration.
- Case bulk update API is in place for operational batch corrections.
- Case-level batch reprocess API is in place.
- Case metadata patch API is in place for operational corrections.
- Case activity log API is in place for lightweight audit visibility.
- Case search API supports pagination via `limit` and `offset`.
- Due-task, invoice, and RAG search APIs support pagination via `limit` and `offset`.
- Pagination inputs are normalized: `limit` is capped at 100 and negative `offset` values are treated as 0.
- Paginated list and activity endpoints expose `X-Total-Count` response headers.
- Case batch reprocess limits are normalized to the same 1-100 range.
- A lightweight `/summary` endpoint exposes total counts for cases, documents, jobs, logs, and RAG entries.
- Document activity log API is in place for lightweight audit visibility.
- Global operation log API is in place for internal traceability.
- Processing job list API supports pagination via `limit` and `offset`.
- Document and operation-log list endpoints support pagination via `limit` and `offset`.
- A notification digest endpoint is available for due-task and invoice reminders.
- Notification delivery history is recorded in SQLite and exposed through `/notification-deliveries`.
- A notification delivery summary is available through `/notification-deliveries/summary`.
- A daily notification delivery trend view is available through `/notification-deliveries/trends`.
- An alert-focused view is available through `/notification-deliveries/alerts`.
- A combined dashboard-friendly report is available through `/notification-deliveries/report`.
- A plain-text markdown version is available through `/notification-deliveries/report.md`.

The delivery history endpoint supports `deliver_to`, `status`, `created_after`, and `created_before` filters for operational review.
The summary endpoint also accepts `deliver_to` to focus the top-level totals on a specific delivery target.
The summary endpoint supports the same date filters and also returns a `failure_rate` for quick health checks.
You can also adjust how many recent failures it returns with `recent_failures_limit` and `recent_failures_offset`.
It also returns `needs_attention` and `attention_reason` when failure volume crosses the configured threshold.
`by_deliver_to` includes per-target totals, success/failed counts, and failure rates for operational review.
Each target entry also includes `needs_attention` and `attention_reason`, and `attention_targets` lists the targets currently above threshold.
The summary also returns the latest delivery, latest success, and latest failure timestamps for the full scope and each target.
The trend endpoint returns day-by-day totals, success/failed counts, and a failure rate for charting or monitoring.
Set `granularity=week` or `granularity=month` to roll the same data up into larger time buckets.
The alerts endpoint returns only the trend buckets that cross the configured failure threshold.
The report endpoint bundles summary, trends, and alerts into one response for dashboards or ops pages.
The report also lifts the key summary fields, latest delivery times, and attention targets to the top level for quick access.
The markdown report mirrors the same data in a human-readable format for Slack, email, or quick copy/paste sharing.
- Basic automatic text extraction is in place for text, HTML, JSON, DOCX, best-effort PDF files, and optional image OCR routing.
- A chat-ingestion API scaffold is in place so Discord/LINE adapters can hand off messages into the shared ledger pipeline.
- Connector-specific ingestion endpoints are also available under `/connectors/discord/chat-ingestions` and `/connectors/line/chat-ingestions` so external chat adapters can stay separate from the core ingestion path.
- A LINE webhook bridge is also available at `/connectors/line/webhook` for signed LINE Messaging API events, including text and file/media ingestion.
- LINE media events without a case code can fall back to the `LINE_INBOX_CASE_CODE` triage bucket, which defaults to `LINE-INBOX`.
- LINE sticker and location messages are also stored as simple text snapshots in the inbox bucket so non-file business notes are not lost.
- LINE follow-style non-message events are also stored as JSON snapshots in the inbox bucket so contact events are not lost.
- LINE join and leave events are also stored as JSON snapshots in the inbox bucket so group membership changes are not lost.
- LINE memberJoined and memberLeft events are also stored as JSON snapshots with readable summaries.
- LINE postback and beacon events are also stored as JSON snapshots so interaction events are not lost.
- LINE accountLink and videoPlayComplete events are also stored as JSON snapshots with readable summaries.
- Pending LINE retries and non-message snapshots now keep searchable operation-log metadata as well.
- LINE webhook accept / skip / signature-failure outcomes are also recorded in the operation log for later review.
- Pending LINE video/audio items keep the original event JSON in the operation log so they can be retried later through `/line-webhooks/retry-pending`.
- `/line-webhooks/pending` lists the current pending LINE webhook backlog with the original event JSON.
- `/line-webhooks/activity` lists the recent LINE webhook activity with filters for LINE event type, operation log type, case code, and message type.
- `/line-webhooks/report` also shows the latest pending backlog item and the pending backlog count.
- `/line-webhooks/report` raises a simple attention flag when the pending backlog crosses a threshold.
- `/line-webhooks/alerts` returns backlog alerts for automation and monitoring.
- `/line-webhooks/report.md` returns a copy/paste-friendly Markdown report.
- `/line-webhooks/alerts.md` returns a copy/paste-friendly Markdown alert summary.
- `/line-webhooks/report` summarizes LINE webhook ingestion health from those operation logs.
- `python -m app.cli.notification_worker line-webhook-report --report-format markdown` can render the same backlog report from the worker CLI.
- `python -m app.cli.notification_worker line-webhook-alerts --deliver-to auto` can render or deliver backlog alerts through the existing notification routes.
- Video and audio events can surface as `pending` while LINE is still preparing the binary content.
- FastAPI health endpoint scaffold is in place.
- The local test suite is passing in the local venv (99 tests at the time of this update).

## Notification worker

Use the `digest` command for daily digests, the `report` command for delivery history reports, and the `line-webhook-alerts` command for LINE backlog monitoring.

For a shorter operator-focused version, see `docs/notification_quick_reference.md`.
For implementation notes and project context, see `docs/handoff.md`.

```powershell
.\.venv\Scripts\python.exe -m app.cli.notification_worker digest --as-of 2026-07-03 --output output\notification-digest.json
```

### Notification entrypoints at a glance

| Task | Local CLI | Container mode | Docker Compose |
| --- | --- | --- | --- |
| Daily digest | `python -m app.cli.notification_worker digest` | `APP_RUN_MODE=notification-worker` | `docker compose run --rm notification-worker` |
| Delivery report | `python -m app.cli.notification_worker report` | `APP_RUN_MODE=notification-report` | `docker compose run --rm notification-report` |
| LINE webhook report | `python -m app.cli.notification_worker line-webhook-report` | `APP_RUN_MODE=notification-line-webhook-report` | `docker compose run --rm notification-line-webhook-report` |
| LINE webhook alerts | `python -m app.cli.notification_worker line-webhook-alerts` | `APP_RUN_MODE=notification-line-webhook-alerts` | `docker compose run --rm notification-line-webhook-alerts` |

All four commands support `--output`. The digest command also supports `--dry-run`, `--deliver-to`, and retry flags. The report command supports `--report-format json|markdown` and delivery-history filters. The LINE webhook report command supports `--report-format json|markdown`. The LINE webhook alerts command supports `--report-format json|markdown`, `--deliver-to`, `--dry-run`, and retry flags.

Run `.\.venv\Scripts\python.exe -m app.cli.notification_worker --help` to see all four commands and examples.

Detailed delivery target setup lives in `docs/notification_quick_reference.md`.

The digest worker renders a Markdown digest and can send it to Discord, Slack, LINE, or email when the matching environment variables are set.

## Docker run modes

The container defaults to the API server. You can switch it to the notification worker by setting:

- `APP_RUN_MODE=notification-worker`

To run the delivery report instead, set:

- `APP_RUN_MODE=notification-report`

To run the LINE webhook report instead, set:

- `APP_RUN_MODE=notification-line-webhook-report`

To run LINE webhook alerts instead, set:

- `APP_RUN_MODE=notification-line-webhook-alerts`

The same image can therefore be used both for the API and for scheduled notification jobs.

The digest worker also supports `--deliver-to auto`, which routes urgent digests to LINE/Discord and lower-priority digests to Slack/email when those destinations are configured.

You can override the auto-routing split with:

- `NOTIFICATION_AUTO_URGENT_TARGETS`
- `NOTIFICATION_AUTO_WARNING_TARGETS`

## Docker Compose

To run the API server:

```powershell
docker compose up api
```

To run a one-off notification digest job, report, LINE webhook report job, or LINE webhook alert job, use the `notification-worker`, `notification-report`, `notification-line-webhook-report`, or `notification-line-webhook-alerts` service as needed.
