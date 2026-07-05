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
- `app/services/extraction.py` adds best-effort automatic text extraction for common file types, now prefers optional PDF parsers (`pypdf` / `pdfplumber`) before falling back to the regex-based extractor, can optionally OCR scanned PDF pages through `pdf2image`, and also applies lightweight image preprocessing, orientation correction, and contrast normalization before OCR.
- `requirements.txt` now documents the optional extraction helper packages so future OCR/PDF setup is easier to reproduce.
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
- `app/api/main.py` also exposes InsForge readiness flags in `/admin/overview` so future production backend setup can be staged incrementally.
- `app/api/main.py` exposes `/admin/backends` as a compact backend configuration and readiness API for future setup screens.
- `app/api/main.py` now also exposes extraction capability readiness in `/admin/overview` and `/admin/backends` so operators can see which optional PDF / OCR helpers are available, including composite readiness flags for PDF text parsing, image OCR, and scanned PDF OCR.
- `app/services/ingestion.py` and `app/services/documents.py` now store extraction provenance in RAG metadata so later audits can see which engine produced a text artifact.
- `app/api/main.py` now surfaces the latest document extraction snapshot through `GET /cases/{case_id}`, `GET /documents`, and `GET /documents/{document_id}` whenever a reusable text artifact exists.
- The admin document tool also shows the latest extraction summary after loading a document, so operators can see the extraction source and engine without opening raw JSON.
- The admin resource browser now adds an `extraction` column for documents so list views and document tools stay aligned.
- The admin recent documents panel also includes extraction snapshots so the dashboard matches the document list/detail view.
- `app/mcp/server.py` now includes extraction snapshots in case/document resource reads so the MCP-facing surface matches the API/admin views.
- `app/api/main.py` exposes `/admin/react-admin` as a React-admin-friendly manifest for the future O's flow Admin app.
- `app/api/main.py` also returns status breakdowns for cases, invoices, outputs, and document source types from `/admin/overview`.
- The dashboard summary now includes per-event-type, per-resource, and top-resource counts.
- `app/api/main.py` exposes `/admin/recent` for quick latest-item inspection and `/admin/activity` for a merged admin timeline with kind/case/document filters.
- `app/api/main.py` exposes `/admin/dashboard` as the first consolidated admin landing payload.
- `app/api/main.py` exposes `/admin` as a lightweight HTML admin landing page.
- `app/api/main.py` exposes `/admin/resources` as a compact admin resource manifest with fields, form metadata, sort order, supported operations, supported actions, detail keys, editable case metadata, and the standard `/cases` list path.
- `app/api/main.py` exposes `/admin/ui` as a lightweight browser-facing admin UI shell with browsable resource data, resource details, a resource action bar, a simple case editor backed by `PATCH /cases/{case_id}`, document actions for reassign/reprocess/delete, notification summary/trends/alerts/report views, and case list filters for due date, invoice state, and output state.
- `app/api/main.py` now also exposes detail endpoints for operation logs and notification deliveries so the admin browser can open the selected row.
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
- Common config and documentation text formats such as `.toml`, `.ini`, `.cfg`, `.env`, `.rst`, and `.adoc` are also treated as text-like for extraction.
- LINE sticker and location events are stored as simple text snapshots in the inbox bucket so they remain searchable.
- LINE sticker and location events now also keep structured sticker/location metadata in operation logs for easier filtering and audit.
- LINE follow-style non-message events are stored as JSON snapshots in the inbox bucket so contact events are preserved too.
- LINE join and leave events are also stored as JSON snapshots in the inbox bucket so membership changes are preserved too.
- LINE memberJoined and memberLeft events are also stored as JSON snapshots with readable summaries.
- LINE postback and beacon events are also stored as JSON snapshots with readable summaries so interaction details are easier to scan.
- LINE accountLink and videoPlayComplete events are also stored as JSON snapshots with readable summaries so connection and playback details remain visible.
- LINE message webhook logs also preserve reply tokens, redelivery flags, and quoted message IDs so retry and reply diagnostics stay visible.
- `/line-webhooks/activity` and `/line-webhooks/pending` now surface the same LINE webhook helper metadata so operators can inspect reply and retry context without opening raw JSON.
- `/line-webhooks/report` and `/line-webhooks/alerts` now include the same latest-pending helper metadata in both JSON and Markdown forms.
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
- An optional OCR/image extraction entry point now exists, PDF extraction now prefers optional parser libraries when they are available, scanned-PDF OCR can be enabled with `pdf2image`, image preprocessing now helps OCR readiness, extraction provenance now stays in RAG metadata, and document list/detail / admin UI now expose the latest extraction snapshot, but a production-grade OCR backend, tuning, and deployment-ready OCR stack are still future work.
- The SQLite repository currently uses `check_same_thread=False` so FastAPI threadpool access works, and it now exposes a clear closed-state guard on its connection, but a cleaner DB/session boundary should still be added later.
- The full local test suite is currently passing (137 tests).

## Verified Commands

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe -m py_compile app\api\main.py app\cli\entrypoint.py app\cli\notification_worker.py tests\test_entrypoint.py tests\test_notification_worker.py
powershell -ExecutionPolicy Bypass -NoProfile -File scripts\run_notification_job.ps1 -Job line-webhook-report -ReportFormat markdown -ReportLimit 1 -PendingBacklogThreshold 1
```

## Best Next Steps

1. Run `scripts/register_notification_jobs.ps1 -Apply -Force` on the target machine when you want the recommended Windows schedules to become active.
2. Extend the LINE webhook bridge with reply / quote / attachment metadata if those LINE variants become operationally useful.
3. Expand notification delivery history with retry policies, failure dashboards, or alerting hooks if operational volume increases.
4. Implement the InsForge/PostgreSQL repository adapter and managed storage adapter behind the new runtime boundary.
5. Expand OCR and image/PDF extraction into a production-grade pipeline so smartphone photos become searchable data.
6. Expand the MCP server with richer prompt variants and fuller Streamable HTTP push notification handling if we need remote deployment.

## Related Docs

- `README.md` is the top-level project overview.
- `docs/implementation_directive.md` is the fixed direction future AI agents should read first.
- `docs/notification_quick_reference.md` is the shortest operator-facing notification runbook.
- `GET /admin/overview` is the current best low-friction starting point for a React-admin style UI.
- `GET /admin/backends` is the current best compact backend setup API for InsForge readiness checks.
- `GET /admin/react-admin` is the current best React-admin manifest for the future O's flow Admin app.
- `GET /admin/recent` is the current best quick-look endpoint for operator timelines and latest activity.
- `GET /admin/activity` is the current best merged timeline endpoint for admin-style dashboards, and it now accepts kind/case/document filters.
- `GET /admin/dashboard` is the current best combined landing endpoint for a React-admin style UI.
- `GET /admin` is the current best human-readable landing page for the admin surface.
- `GET /admin/resources` is the current best resource manifest for a React-admin style integration, and it already includes fields, form hints, supported actions, detail keys, and the standard `/cases` list path.
- `GET /admin/ui` is the current best browser-facing admin shell before a full React-admin app exists, and it can browse resource data directly, open resource details, use the resource action bar, edit cases with the built-in case editor, run document reassign/reprocess/delete actions, inspect notification summary/trends/alerts/report views, and narrow case lists with due/invoice/output filters.
- `GET /operation-logs/{operation_log_id}` and `GET /notification-deliveries/{notification_delivery_id}` now fill the last detail-view gaps for admin browsing.
- `app/services/document_snapshots.py` now holds the shared document extraction snapshot builder used by both the API and MCP resource readers.
- `app/services/extraction.py` now has builtin RTF, XML, and EML text extraction paths alongside the existing text, HTML, JSON, DOCX, image OCR, and PDF extraction flow.
- `app/services/extraction.py` now also has builtin `.xlsx` extraction so simple spreadsheet text becomes searchable without extra dependencies.
- `app/services/extraction.py` now also has builtin `.ods` extraction so OpenDocument spreadsheets become searchable without extra dependencies.
- `app/services/extraction.py` now also has builtin `.odt` extraction so OpenDocument text documents become searchable without extra dependencies.
- `app/services/extraction.py` now also has builtin `.xls` extraction behind optional xlrd support for legacy Excel files.
- `app/services/extraction.py` now also has builtin `.msg` extraction behind optional extract_msg support for Outlook mail files.
- `app/services/extraction.py` now also has builtin `.csv` and `.tsv` extraction that normalizes rows for search-friendly text output.
- `app/services/extraction.py` now strips HTML script/style noise before extracting text, which keeps HTML mail and page snippets cleaner.
- `app/services/extraction.py` now also treats common text-like MIME types such as Markdown, YAML, TOML, and RST as plain-text extraction inputs even when the filename extension is unhelpful.
- `app/services/extraction.py` now also has builtin JSONL / NDJSON extraction so line-delimited JSON stays searchable without extra tooling.

## Working Rule For Future Changes

If a change is about where the data is stored or how it is reused later, update the repository/storage boundary first.
If a change is about user interaction, keep the chat connector separate from the ingestion core.
If a change is about later reuse, route it through the ledger and derived artifacts rather than keeping it only in chat messages.
