# O's flow V2 Product Scope

## Core Goal

O's flow V2 is a chat-driven operations system.
Users interact through channels such as LINE or Discord, and HermesAgent helps ingest files, documents, and smartphone photos into a reusable internal knowledge base.

O's flow is a system for ingesting, organizing, storing, and reusing business data.
The ingested data is not only stored. It is normalized, ledgered, and turned into reusable operational data.
RAG/searchable output is one reuse layer inside the system, so that later workflows can automate document generation, reminders, and office operations.

## Primary Inputs

- Chat instructions from users through HermesAgent
- File attachments such as PDFs, images, and documents
- Smartphone photos of paper documents
- Manual uploads and future API-based ingestion
- Optional chat connector sync from Discord into the ledger backend when a case code is available

## Core System Responsibilities

### 1. Intake and Ingestion

- Accept files and instructions from chat channels
- Support both manual ingestion and automatic ingestion
- Save originals separately from derived artifacts
- Extract usable text and structured data from files and images
- Register all assets and processing state in the ledger database

### 2. Ledger and Searchable Knowledge

- Keep SQLite as the development/test system of record
- Use InsForge/PostgreSQL as the production and sales backend target
- Track cases, documents, artifacts, due dates, invoice state, output state, and processing state
- Generate RAG-ready outputs from ingested data as one reusable representation
- Make the data available for later retrieval and workflow automation

### 3. Business Action Layer

- Fill industry-specific document formats using ingested data
- Monitor due dates and send alerts such as one day before deadline
- Generate invoices or invoice batches and hand them off to office/admin workflows
- Support future operational queries such as case search, due task lists, and billing lists
- Produce daily reminder digests for due cases and pending invoice follow-ups
- Deliver reminder digests through chat/webhook/email adapters such as Discord, Slack, LINE, and SMTP mail

## Target Workflows

### Document Completion

- A user sends files and instructions in chat
- HermesAgent identifies the case and extracts the needed fields
- The system fills a business-specific template and generates output documents

### Deadline Monitoring

- A case has a due date in the ledger
- The system monitors upcoming deadlines
- HermesAgent or the notification layer sends alerts before the deadline

### Billing Support

- The system tracks invoice status per case
- It can gather eligible billing items in bulk
- Office staff can receive grouped invoice outputs or billing-ready lists

## Architecture Implications

- Chat connectors must be separate from the ingestion core
- HermesAgent orchestration must be separate from storage and repository logic
- RAG is a reusable output layer, not the main system identity and not the only data model
- The ledger is the source of truth for operational automation
- The customer-facing admin surface should be O's flow Admin, preferably built on React-admin
- Generic admin tools such as Appsmith, Directus, or Retool may be used for prototypes, but not as the final product UI direction
- File storage must support migration from local disk to InsForge Storage or another managed object store
- Database access must support migration from SQLite to InsForge/PostgreSQL

## Current Build Order

1. Ledger-first ingestion
2. File/artifact separation
3. RAG output generation
4. Search and business query APIs
5. Alerts, document generation, and billing workflows
6. Additional chat connectors and hosted deployment
