# O's flow V2 Handoff

## What This Project Is

O's flow V2 is a system for ingesting, organizing, storing, and reusing business data.
RAG is a supporting component, not the product center.
The operational goal is to make data reusable later for templates, reminders, billing, and other office workflows.

## What Is Already In Place

- `app/config.py` loads settings from environment variables.
- `app/runtime.py` centralizes repository/storage adapter creation and currently defaults to SQLite/local backends.
- `app/config.py` now exposes explicit InsForge placeholder settings from `.env.example`.
- `app/repositories/insforge.py` and `app/storage/insforge.py` now exist as explicit placeholders for the future managed backend.
- `app/domain/models.py` defines the core ledger entities.
- `app/repositories/sqlite.py` provides a SQLite-backed repository with schema initialization.
- `app/storage/local.py` provides a local file storage adapter.
- `app/services/ingestion.py` wires case registration, document registration, artifact registration, RAG output generation, and processing job tracking.
- `app/services/extraction.py` adds best-effort automatic text extraction for common file types.
- `app/services/documents.py` handles document deletion, storage cleanup, single-document reprocessing, selected-document batch reprocessing, case batch reprocessing, and related job tracking.
- `app/mcp/tools.py` provides repository-backed business query tools that can later be wrapped by a real MCP server.
- `app/mcp/server.py` provides a pure-Python stdio MCP server that exposes those repository-backed tools.
- `app/mcp/http.py` provides a minimal FastAPI-backed MCP HTTP transport on `/mcp`.
- `app/mcp/http.py` now enforces a session-backed initialize flow, returns `202` for MCP notifications, and requires `text/event-stream` for GET.
- `app/mcp/http.py` also supports closing sessions through `DELETE /mcp`.
- `app/mcp/server.py` and `app/mcp/http.py` now keep lightweight resource subscription bookkeeping for `resources/subscribe` and `resources/unsubscribe`.
- `app/mcp/server.py` exposes `oflow://mcp/subscriptions` as a resource snapshot for current subscriptions.
- `app/mcp/server.py` also exposes `oflow://mcp/events` as a resource snapshot for queued MCP events.
- `app/mcp/server.py` also exposes `oflow://mcp/dashboard` as a compact operational summary resource.
- `app/api/main.py` exposes `/mcp/subscriptions` so the current session subscription snapshot can be inspected from the API surface.
- `app/api/main.py` exposes `/mcp/events` so the queued MCP event snapshot can be inspected from the API surface, with `session_id`, `event_type`, and `resource_uri` filters plus per-type counts.
- `app/api/main.py` exposes `/mcp/overview` so subscriptions and queued events can be inspected together from the API surface, including per-type counts.
- `app/api/main.py` exposes `/mcp/dashboard` as the compact MCP operational summary endpoint.
- `app/api/main.py` exposes `/admin/overview` as a lightweight admin-facing snapshot with backend configuration flags and system counts.
- The dashboard summary now includes per-event-type, per-resource, and top-resource counts.
- `app/mcp/http.py` now drains queued subscription-change events through `GET /mcp` as SSE lines before the keep-alive comment.
- `app/api/main.py` now queues case, document, and ingestion resource-change notifications into the MCP transport after successful mutations.
- `app/mcp/server.py` now records MCP usage events into the operation log for initialize, tools, resources, prompts, and unknown methods.
- `app/mcp/server.py` now exposes `resources/list` and `resources/read` for summary, case, and document resources.
- `app/mcp/server.py` now exposes `resources/templates/list` and `prompts/list` / `prompts/get` for operational templates.
- `app/mcp/http.py` forwards `resources/list` and `resources/read` through the same HTTP transport.
- `app/api/main.py` exposes `healthz`, `meta`, `cases/search`, `cases`, `cases/{case_id}`, `cases/{case_id}/activity`, `operation-logs`, `cases/bulk`, `cases/{case_id}/reprocess-documents`, `documents`, `documents/bulk-reprocess`, `documents/{id}`, `documents/{id}/activity`, `documents/{id}/reassign`, `documents/{id}/reprocess`, `tasks/due`, `invoices`, `rag/search`, `processing-jobs`, JSON `ingestions`, multipart file `ingestions/upload`, and shared chat ingestion routes.
- `app/api/main.py` also exposes `/connectors/line/webhook` as a signed LINE Messaging API webhook bridge for text and media events.
- `app/api/main.py` also exposes `/line-webhooks/pending` so operators can inspect the current LINE backlog before retrying it.
- `app/api/main.py` also exposes `/line-webhooks/retry-pending` so pending LINE video/audio events can be retried after LINE finishes preparing the binary content.
- `app/api/main.py` also exposes a lightweight `/summary` endpoint for system-wide counts.
- `app/api/main.py` also exposes a lightweight `/notifications/due` endpoint for digest previews.
- `app/api/main.py` also exposes `/notification-deliveries` for notification delivery history.
- `app/api/main.py` also exposes `/notification-deliveries/summary` for quick delivery health checks.
- `app/api/main.py` also exposes `/notification-deliveries/trends` for daily delivery graphs.
- `app/api/main.py` also exposes `/notification-deliveries/alerts` for threshold-based alert buckets.
- `app/api/main.py` also exposes `/notification-deliveries/report` for combined dashboard payloads.
- `app/api/main.py` also exposes `/notification-deliveries/report.md` for human-readable markdown reports.
- `app.cli.notification_worker` now has `digest`, `report`, `line-webhook-report`, and `line-webhook-alerts` commands, and `--help` shows example invocations.
- `app.cli.entrypoint` can start the digest worker (`APP_RUN_MODE=notification-worker`), the report worker (`APP_RUN_MODE=notification-report`), the LINE webhook report worker (`APP_RUN_MODE=notification-line-webhook-report`), or the LINE webhook alert worker (`APP_RUN_MODE=notification-line-webhook-alerts`).
- `docker-compose.yml` includes `notification-worker`, `notification-report`, `notification-line-webhook-report`, and `notification-line-webhook-alerts` services with explicit commands.
- `.env.example` now lists the supported notification worker run modes for operator discoverability.
- `docs/notification_quick_reference.md` provides a shorter operator-focused command reference.
- `docs/notification_schedule.md` provides a recommended cadence and example cron entries for the worker jobs.
- `scripts/run_notification_job.ps1` provides a Windows-friendly launcher that keeps Task Scheduler invocations consistent.
- `scripts/register_notification_jobs.ps1` previews or registers the recommended Windows scheduled tasks in one place.
- `docs/notification_quick_reference.md` also lists the HTTP report endpoints for JSON and Markdown output.
- The trends endpoint supports `granularity=day|week|month`.
- Delivery history filters include `deliver_to`, `status`, `created_after`, and `created_before`.
- The summary endpoint mirrors the same date filters and includes a `failure_rate` metric.
- The summary endpoint accepts `deliver_to` to scope the top-level totals to one delivery target.
- The summary endpoint also supports `recent_failures_limit` and `recent_failures_offset`.
- The summary endpoint also returns `needs_attention` and `attention_reason` when failure volume crosses the threshold.
- `by_deliver_to` includes per-target totals, success/failed counts, and failure rates.
- Each target entry also includes `needs_attention` and `attention_reason`, and `attention_targets` lists the targets above threshold.
- The summary also returns the latest delivery, latest success, and latest failure timestamps.
- The trends endpoint returns day-by-day totals, success/failed counts, and failure rates.
- It can also roll the same data up into weekly or monthly buckets.
- The alerts endpoint returns only the trend buckets that exceed the configured threshold.
- The report endpoint bundles summary, trends, and alerts into one payload.
- The report also lifts key summary fields, latest timestamps, and attention targets to the top level.
- The markdown report mirrors the same payload in a copy/paste-friendly format.
- `discord-fill-bot/` provides a standalone Discord intake bot that can optionally sync generated output into the Discord chat connector route when a case code is present in the request text.
- `tests/test_core.py` covers repository flow, ingestion flow, processing job flow, extraction flow, API ingestion flow, multipart upload flow, delete flow, single reprocess flow, selected document batch reprocess flow, case batch reprocess flow, case patch flow, case search / due-task / invoice / RAG / processing-job pagination flow, document search and pagination flow, document listing flow, and API flow.
- `tests/test_mcp_tools.py` covers the new repository-backed business query tools.
- `tests/test_mcp_server.py` covers the stdio transport, initialize handshake, and tool calls.
- `tests/test_mcp_http.py` covers the HTTP transport initialization, session enforcement, notification handling, and SSE response.
- `tests/test_mcp_server.py` and `tests/test_mcp_http.py` also cover resource listing and reading.
- `tests/test_mcp_server.py` and `tests/test_mcp_http.py` also cover prompt listing and retrieval.
- `tests/test_mcp_server.py` and `tests/test_mcp_http.py` also cover MCP audit logging.
- `tests/test_mcp_server.py` and `tests/test_mcp_http.py` also cover resource subscription bookkeeping.
- `tests/test_mcp_server.py` and `tests/test_mcp_http.py` also cover the `oflow://mcp/subscriptions` resource snapshot.
- `tests/test_mcp_server.py` and `tests/test_mcp_http.py` also cover the `oflow://mcp/events` resource snapshot / inspection endpoint.
- `tests/test_mcp_http.py` also covers the `/mcp/subscriptions` inspection endpoint and cleanup behavior after session deletion.
- `tests/test_mcp_http.py` also covers the `/mcp/events` inspection endpoint, its filters, and per-type counts.
- `tests/test_mcp_http.py` also covers the `/mcp/overview` combined inspection endpoint and per-type counts.
- `tests/test_mcp_http.py` also covers the `/mcp/dashboard` compact summary endpoint.
- The dashboard tests also cover per-resource and top-resource counts.
- `tests/test_mcp_http.py` also covers SSE draining of queued subscription-change events.
- `tests/test_mcp_http.py` also covers queued resource-change notifications after case updates.
- `tests/test_notifications.py` and `tests/test_notification_worker.py` cover digest generation, digest preview, LINE webhook alert generation, delivery adapters, dry-run behavior, and notification delivery history recording.
- Paginated list and activity responses now expose `X-Total-Count` headers while preserving existing body shapes.
- Case batch reprocess requests now normalize their document limit to the 1-100 range.
- `Dockerfile` is in place for containerized startup.
- Repository and storage backends are now selected through a small runtime factory so the API no longer hard-codes concrete adapter construction in `app/api/main.py`.
- The runtime factory now accepts `insforge` backend values and fails fast with a clear placeholder error until those adapters are implemented.

## Confirmed Design Decisions

- SQLite is the development/test system of record.
- InsForge/PostgreSQL is the production and sales backend target.
- Storage and repository are intentionally separated.
- RAG output is one reusable representation, not the whole product.
- Discord is a reference intake channel, and LINE/other chat connectors can be added through dedicated adapter routes.
- The Discord bot can remain standalone, but it is now aligned with the O's flow backend through optional case-based sync into the connector route.
- Docker/Vercel-style deployment should remain possible.

## Important Current Caveats

- Dedicated Discord and LINE connector routes now exist in the new `app/` package, while the shared chat-ingestion API remains the shared ledger entrypoint.
- LINE media events can fall back to the `LINE-INBOX` triage case when they do not include a case code in text or filename.
- LINE sticker and location events are stored as simple text snapshots in the inbox bucket so they remain searchable.
- LINE follow-style non-message events are stored as JSON snapshots in the inbox bucket so contact events are preserved too.
- LINE join and leave events are also stored as JSON snapshots in the inbox bucket so membership changes are preserved too.
- LINE memberJoined and memberLeft events are also stored as JSON snapshots with readable summaries.
- LINE postback and beacon events are also stored as JSON snapshots so interaction events are preserved too.
- LINE accountLink and videoPlayComplete events are also stored as JSON snapshots with readable summaries.
- LINE unsend events are also stored as JSON snapshots with the removed message ID preserved in metadata when present.
- Pending LINE retries and non-message snapshots now keep searchable operation-log metadata as well.
- LINE webhook accept / skip / signature-failure outcomes are now recorded in the operation log.
- Pending LINE video/audio logs now keep the original event JSON so they can be replayed through the retry endpoint later.
- `/line-webhooks/pending` now surfaces those pending logs directly for operators.
- `/line-webhooks/activity` now lists recent LINE webhook activity with event-type and case filters.
- `/line-webhooks/report` now includes the latest pending backlog item and a backlog count.
- `/line-webhooks/report` now raises a simple attention flag when the pending backlog crosses a threshold.
- `/line-webhooks/alerts` returns backlog alerts for automation and monitoring.
- `/line-webhooks/report.md` returns a copy/paste-friendly Markdown report.
- `/line-webhooks/alerts.md` returns a copy/paste-friendly Markdown alert summary.
- `/line-webhooks/report` summarizes LINE webhook ingestion health from the operation log.
- `python -m app.cli.notification_worker line-webhook-report --report-format markdown` can render the same backlog report from the worker CLI.
- `python -m app.cli.notification_worker line-webhook-alerts --deliver-to auto` can render or deliver backlog alerts from the worker CLI.
- LINE video/audio events can surface as `pending` while LINE is still preparing the binary content.
- A basic notification digest worker now exists for due-task and invoice reminders, and Discord webhook / Slack webhook / LINE push / email SMTP delivery adapters are available.
- Notification delivery history is recorded in SQLite and exposed through the API, and the same data powers the `report` command plus `/notification-deliveries/report` and `/notification-deliveries/report.md`.
- MCP server support now exists via stdio and `/mcp` HTTP transports, and resource subscription bookkeeping is now tracked per session, but fuller Streamable HTTP push notifications are still future work.
- An optional OCR/image extraction entry point now exists, but a production-grade OCR backend, tuning, and PDF OCR are still future work.
- The SQLite repository currently uses `check_same_thread=False` so FastAPI threadpool access works, but a cleaner DB/session boundary should be added later.
- The full local test suite is currently passing (103 tests).

## Verified Commands

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe -m py_compile app\api\main.py app\cli\entrypoint.py app\cli\notification_worker.py tests\test_entrypoint.py tests\test_notification_worker.py
powershell -ExecutionPolicy Bypass -NoProfile -File scripts\run_notification_job.ps1 -Job line-webhook-report -ReportFormat markdown -ReportLimit 1 -PendingBacklogThreshold 1
```

## Best Next Steps

1. Run `scripts/register_notification_jobs.ps1 -Apply -Force` on the target machine when you want the recommended Windows schedules to become active.
2. Extend the LINE webhook bridge with richer event support for additional message event variants if they are needed operationally.
3. Expand notification delivery history with retry policies, failure dashboards, or alerting hooks if operational volume increases.
4. Implement the InsForge/PostgreSQL repository adapter and managed storage adapter behind the new runtime boundary.
5. Expand OCR and image/PDF extraction into a production-grade pipeline so smartphone photos become searchable data.
6. Expand the MCP server with richer prompt variants and fuller Streamable HTTP push notification handling if we need remote deployment.

## Related Docs

- `README.md` is the top-level project overview.
- `docs/implementation_directive.md` is the fixed direction future AI agents should read first.
- `docs/notification_quick_reference.md` is the shortest operator-facing notification runbook.
- `GET /admin/overview` is the current best low-friction starting point for a React-admin style UI.

## Working Rule For Future Changes

If a change is about where the data is stored or how it is reused later, update the repository/storage boundary first.
If a change is about user interaction, keep the chat connector separate from the ingestion core.
If a change is about later reuse, route it through the ledger and derived artifacts rather than keeping it only in chat messages.
