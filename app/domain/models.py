from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Case:
    id: int
    case_code: str
    title: str
    client_name: str | None
    status: str
    due_date: str | None
    invoice_status: str
    output_status: str
    created_at: str
    updated_at: str
    last_processed_at: str | None


@dataclass(frozen=True)
class Document:
    id: int
    case_id: int
    source_type: str
    source_path: str | None
    storage_key: str
    filename: str
    mime_type: str | None
    content_hash: str
    size_bytes: int
    version: int
    is_deleted: bool
    deleted_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Artifact:
    id: int
    document_id: int
    artifact_type: str
    storage_key: str
    content_hash: str
    generator: str
    created_at: str


@dataclass(frozen=True)
class RagEntry:
    id: int
    document_id: int
    artifact_id: int | None
    chunk_id: str
    title: str
    body_text: str
    metadata_json: str
    content_hash: str
    is_active: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CaseDetail:
    case: Case
    documents: list[Document] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    rag_entries: list[RagEntry] = field(default_factory=list)


@dataclass(frozen=True)
class ProcessingJob:
    id: int
    case_id: int | None
    document_id: int | None
    job_type: str
    job_status: str
    error_code: str | None
    error_message: str | None
    started_at: str
    finished_at: str | None


@dataclass(frozen=True)
class OperationLog:
    id: int
    event_type: str
    entity_type: str
    entity_id: int | None
    case_id: int | None
    document_id: int | None
    message: str
    metadata_json: str
    created_at: str


@dataclass(frozen=True)
class IngestionRequest:
    case_code: str
    title: str
    filename: str
    content: bytes
    mime_type: str | None = None
    source_type: str = "discord"
    source_path: str | None = None
    client_name: str | None = None
    due_date: str | None = None
    invoice_status: str = "unbilled"
    output_status: str = "pending"
    extracted_text: str | None = None
    structured_json: dict[str, object] | None = None
    rag_chunks: list[dict[str, object]] = field(default_factory=list)
    output_html: str | None = None


@dataclass(frozen=True)
class Notification:
    category: str
    severity: str
    case_id: int
    case_code: str
    title: str
    message: str
    due_date: str | None = None
    due_in_days: int | None = None
    source: str = "ledger"


@dataclass(frozen=True)
class NotificationBatch:
    as_of: str
    due_lookahead_days: int
    invoice_lookahead_days: int
    notifications: list[Notification] = field(default_factory=list)


@dataclass(frozen=True)
class NotificationDeliveryLog:
    id: int
    deliver_to: str
    destination: str
    delivered_count: int
    digest_as_of: str
    due_lookahead_days: int
    invoice_lookahead_days: int
    status: str
    message: str
    error_message: str | None
    metadata_json: str
    created_at: str


@dataclass(frozen=True)
class NotificationDeliveryTrend:
    period: str
    granularity: str
    total: int
    success_total: int
    failed_total: int
    failure_rate: float
    needs_attention: bool
    attention_reason: str | None
