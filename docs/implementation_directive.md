# O's flow V2 Implementation Directive

This document is the source of truth for future AI agents working on this repository.

## Fixed Product Definition

O's flow V2 is a system for ingesting, organizing, storing, and reusing business data.
RAG is only one reusable representation inside the system. The product center is the operational data layer that lets customers reuse ingested files, documents, images, due dates, invoice state, templates, and business-specific records later.

The system should be sellable as a base product and then customized per industry or customer.

## Fixed Architecture Direction

Use this direction unless the project owner explicitly changes it.

- InsForge is the production and sales backend target.
- SQLite and local file storage are development/test adapters, not the final hosting strategy.
- O's flow Core must stay independent from any one database or storage provider.
- Admin UI is required for non-engineer customers.
- The preferred Admin UI direction is InsForge plus a React-admin-based O's flow Admin.
- The Discord bot is a reference intake channel, not the whole product.
- LINE, Discord, web upload, and future channels should all feed the same ingestion core.
- Docker/container deployment must remain possible.
- Vercel is an optional runtime/deployment target, not a required architecture dependency.

## Responsibility Split

InsForge should provide the managed backend foundation:

- Auth and user/customer access control
- PostgreSQL-backed operational database
- Managed storage for originals, extracted artifacts, RAG outputs, and final outputs
- Hosting/deploy foundation where appropriate
- MCP/AI Gateway integration points where useful
- A cleaner production path than asking customers to operate raw SQLite files and local folders

Runtime and deployment platforms should be treated as replaceable:

- Vercel can be used for the Admin UI, API, MCP endpoint, and lightweight backend runtime if it fits the workload.
- Railway, Fly.io, Render, VPS, InsForge compute, or another container host can be used instead.
- Long-running bots, heavy OCR, and large background jobs should not be forced onto Vercel if another runtime is safer.
- The correct fixed point is O's flow Core plus InsForge-backed production data, not Vercel itself.

O's flow Core should provide the product-specific business logic:

- Chat/file/image ingestion orchestration
- Case, document, artifact, job, RAG, notification, and audit ledger logic
- Extraction and normalization pipeline
- Business search APIs such as case search, due tasks, invoice lists, and document detail
- RAG generation and search surface
- Notification and reminder workflows
- Industry/customer customization runtime

O's flow Admin UI should provide the non-engineer operating surface:

- Customer setup and settings
- Case/document review and correction
- Due-date and invoice status management
- Template and industry-package configuration
- Notification delivery and failure visibility
- Operational dashboards

The preferred implementation base is React-admin:

- Use React-admin as the long-term base for O's flow Admin.
- Connect it to O's flow API and the InsForge-backed production data layer.
- Keep Appsmith, Directus, Retool, and similar tools as short-term prototypes only, not the primary product direction.
- Keep business workflows in O's flow Core and API services, not inside React-admin view code.
- Design admin views so industry/customer-specific labels, fields, templates, and rules can be configured without rewriting the whole UI.

## InsForge Decision

Use InsForge because the system is intended to be sold to non-engineer customers. Building everything only around local SQLite and folders would make installation, login, customer separation, backups, storage, operations, and updates too fragile for sales.

InsForge is useful because it can give O's flow a stronger backend base while letting O's flow focus on the valuable part: ingestion, business-data reuse, industry customization, reminders, document generation, and agent workflows.

Do not treat InsForge as magic. It does not remove the need to build O's flow Core or the O's flow Admin UI. It should replace infrastructure burden, not product logic.

## Non-Negotiables

- Do not redefine O's flow as "just a RAG app".
- Do not move business rules directly into chat connectors.
- Do not couple ingestion services directly to SQLite-specific code.
- Do not couple storage services directly to local filesystem paths.
- Do not make InsForge-specific code leak into the core domain layer.
- Do not remove SQLite/local adapters while they are useful for tests and local development.
- Do not require end customers to run developer commands for normal operation.
- Do not make Vercel a required dependency for core business behavior.
- Do not treat Appsmith, Directus, Retool, or generic admin tools as the final O's flow product UI.

## Adapter Rule

All database access must go through repository interfaces.

All file/object access must go through storage adapter interfaces.

The intended adapter lineup is:

- Development/test DB: SQLite
- Production DB target: InsForge/PostgreSQL
- Development/test storage: local file storage
- Production storage target: InsForge Storage, or another S3/GCS-like object store if needed

## Industry Customization Rule

The base system should work without heavy customization, but industry-specific sales should be implemented as packages/configuration rather than one-off rewrites.

An industry package can define:

- Required fields
- Extraction prompts/rules
- Validation rules
- Document templates
- Due-date rules
- Invoice rules
- Admin UI labels/views
- Notification policy

## Implementation Order

The next agent should preserve the current working app and move in this order:

1. Keep tests passing with the existing SQLite/local adapters.
2. Add explicit InsForge configuration placeholders and adapter boundaries.
3. Define repository/storage interfaces cleanly enough for PostgreSQL/InsForge adapters.
4. Add admin-oriented APIs before building a heavy UI.
5. Start a React-admin-based O's flow Admin once the first admin API surface is stable.
6. Add MCP tools on top of repository services, not by reading raw files directly.
7. Expand OCR/image/PDF extraction into a production-grade pipeline.
8. Build the non-engineer admin surface into the customer-facing operating UI.
9. Add production deployment documentation around InsForge plus replaceable Docker/container hosting.

## Short Version For Future Agents

Build O's flow as a sellable business-data reuse platform.
Use InsForge for the production backend foundation.
Use React-admin as the preferred base for O's flow Admin.
Keep SQLite/local only as dev/test adapters.
Treat Vercel as an optional deployment target, not the core architecture.
Keep all product value in O's flow Core and the Admin UI.
RAG is a feature, not the identity of the system.
