from __future__ import annotations

import base64
import json
from html import escape
from datetime import date
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi import Request
from pydantic import BaseModel, Field, field_validator

from app.config import load_settings
from app.domain.models import IngestionRequest
from app.services.chat_connectors import (
    build_chat_metadata_json,
    build_discord_source_path,
    build_line_source_path,
)
from app.services.document_snapshots import (
    attach_document_extraction_snapshots,
    build_document_extraction_snapshot_for_document,
)
from app.services.extraction import get_extraction_capabilities
from app.services.documents import DocumentService
from app.services.notification_delivery_report import (
    build_notification_delivery_alerts,
    build_notification_delivery_report,
    build_notification_delivery_summary,
)
from app.services.notification_delivery import render_notification_delivery_report_markdown
from app.services.line_webhook import LineWebhookClient
from app.services.notifications import NotificationService
from app.services.ingestion import IngestionService
from app.mcp.http import MCPHttpTransport
from app.runtime import create_repository, create_storage


def _serialize(value):
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def build_line_webhook_report_payload(
    repository,
    *,
    limit: int = 20,
    pending_backlog_threshold: int = 5,
) -> dict[str, object]:  # noqa: ANN001
    ingested_total = repository.count_operation_logs(event_type="line_webhook_ingested")
    pending_total = repository.count_operation_logs(event_type="line_webhook_pending")
    skipped_total = repository.count_operation_logs(event_type="line_webhook_skipped")
    signature_invalid_total = repository.count_operation_logs(event_type="line_webhook_signature_invalid")
    retry_ingested_total = repository.count_operation_logs(event_type="line_webhook_retry_ingested")
    retry_pending_total = repository.count_operation_logs(event_type="line_webhook_retry_pending")
    retry_skipped_total = repository.count_operation_logs(event_type="line_webhook_retry_skipped")
    total = (
        ingested_total
        + pending_total
        + skipped_total
        + signature_invalid_total
        + retry_ingested_total
        + retry_pending_total
        + retry_skipped_total
    )
    recent_events: list[Any] = []
    for event_type in (
        "line_webhook_ingested",
        "line_webhook_pending",
        "line_webhook_skipped",
        "line_webhook_signature_invalid",
        "line_webhook_retry_ingested",
        "line_webhook_retry_pending",
        "line_webhook_retry_skipped",
    ):
        recent_events.extend(repository.list_operation_logs(event_type=event_type, limit=max(limit, 20)))
    recent_events = sorted(recent_events, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]
    pending_backlog_count = repository.count_operation_logs(event_type="line_webhook_pending")
    pending_backlog_latest = repository.list_operation_logs(event_type="line_webhook_pending", limit=1)
    needs_attention = pending_backlog_count >= max(1, pending_backlog_threshold)
    attention_reason = (
        f"Pending LINE webhook backlog is {pending_backlog_count}, which meets or exceeds the threshold of {max(1, pending_backlog_threshold)}."
        if needs_attention
        else None
    )
    return {
        "requested_at": date.today().isoformat(),
        "summary": {
            "total": total,
            "ingested_total": ingested_total,
            "pending_total": pending_total,
            "skipped_total": skipped_total,
            "signature_invalid_total": signature_invalid_total,
            "retry_ingested_total": retry_ingested_total,
            "retry_pending_total": retry_pending_total,
            "retry_skipped_total": retry_skipped_total,
            "pending_backlog_count": pending_backlog_count,
            "needs_attention": needs_attention,
            "attention_reason": attention_reason,
        },
        "pending_backlog_latest": _serialize(pending_backlog_latest[0]) if pending_backlog_latest else None,
        "recent_events": _serialize(recent_events),
    }


def _set_total_count_header(response: Response, total: int) -> None:
    response.headers["X-Total-Count"] = str(total)


def _build_insforge_backend_status(settings) -> dict[str, Any]:  # noqa: ANN001
    base_url_configured = bool((settings.insforge_base_url or "").strip())
    api_key_configured = bool((settings.insforge_api_key or "").strip())
    database_url_configured = bool((settings.insforge_database_url or "").strip())
    project_id_configured = bool((settings.insforge_project_id or "").strip())
    storage_bucket_configured = bool((settings.insforge_storage_bucket or "").strip())
    storage_namespace_configured = bool((settings.insforge_storage_namespace or "").strip())
    auth_jwks_url_configured = bool((settings.insforge_auth_jwks_url or "").strip())
    mcp_base_url_configured = bool((settings.insforge_mcp_base_url or "").strip())
    repository_missing = [
        name
        for name, configured in {
            "INSFORGE_BASE_URL": base_url_configured,
            "INSFORGE_API_KEY": api_key_configured,
            "INSFORGE_DATABASE_URL": database_url_configured,
            "INSFORGE_PROJECT_ID": project_id_configured,
        }.items()
        if not configured
    ]
    storage_missing = [
        name
        for name, configured in {
            "INSFORGE_BASE_URL": base_url_configured,
            "INSFORGE_API_KEY": api_key_configured,
            "INSFORGE_STORAGE_BUCKET": storage_bucket_configured,
            "INSFORGE_STORAGE_NAMESPACE": storage_namespace_configured,
        }.items()
        if not configured
    ]
    return {
        "base_url_configured": base_url_configured,
        "api_key_configured": api_key_configured,
        "database_url_configured": database_url_configured,
        "project_id_configured": project_id_configured,
        "storage_bucket_configured": storage_bucket_configured,
        "storage_namespace_configured": storage_namespace_configured,
        "auth_jwks_url_configured": auth_jwks_url_configured,
        "mcp_base_url_configured": mcp_base_url_configured,
        "repository_ready": not repository_missing,
        "storage_ready": not storage_missing,
        "repository_missing": repository_missing,
        "storage_missing": storage_missing,
    }


def _build_extraction_backend_status() -> dict[str, bool]:
    return get_extraction_capabilities()


def _parse_optional_json(raw_value: str | None, *, field_name: str, default: Any | None = None) -> Any:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in {field_name}: {exc.msg}") from exc


def _parse_metadata_json(raw_value: str | None) -> dict[str, Any]:
    if raw_value is None or not raw_value.strip():
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _build_admin_activity_item(
    *,
    kind: str,
    entity_id: int,
    occurred_at: str,
    title: str,
    summary: str,
    resource_uri: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "entity_id": entity_id,
        "occurred_at": occurred_at,
        "title": title,
        "summary": summary,
        "resource_uri": resource_uri,
        "details": details,
    }


def _build_admin_activity_items(repository, *, limit: int) -> list[dict[str, Any]]:  # noqa: ANN001
    source_limit = max(limit, 20)
    items: list[dict[str, Any]] = []

    for case in repository.search_cases(limit=source_limit):
        items.append(
            _build_admin_activity_item(
                kind="case",
                entity_id=case.id,
                occurred_at=case.updated_at,
                title=case.case_code,
                summary=case.title,
                resource_uri=f"oflow://cases/{case.id}",
                details={
                    "case_code": case.case_code,
                    "status": case.status,
                    "invoice_status": case.invoice_status,
                    "output_status": case.output_status,
                    "due_date": case.due_date,
                },
            )
        )

    for document in repository.list_documents(limit=source_limit):
        items.append(
            _build_admin_activity_item(
                kind="document",
                entity_id=document.id,
                occurred_at=document.updated_at,
                title=document.filename,
                summary=document.source_type,
                resource_uri=f"oflow://documents/{document.id}",
                details={
                    "case_id": document.case_id,
                    "source_type": document.source_type,
                    "mime_type": document.mime_type,
                    "is_deleted": document.is_deleted,
                    "version": document.version,
                },
            )
        )

    for log in repository.list_operation_logs(limit=source_limit):
        items.append(
            _build_admin_activity_item(
                kind="operation_log",
                entity_id=log.id,
                occurred_at=log.created_at,
                title=log.event_type,
                summary=log.message,
                resource_uri="oflow://summary",
                details={
                    "entity_type": log.entity_type,
                    "case_id": log.case_id,
                    "document_id": log.document_id,
                    "metadata_json": _parse_optional_json(log.metadata_json, field_name="metadata_json", default={}),
                },
            )
        )

    for delivery in repository.list_notification_deliveries(limit=source_limit):
        items.append(
            _build_admin_activity_item(
                kind="notification_delivery",
                entity_id=delivery.id,
                occurred_at=delivery.created_at,
                title=delivery.deliver_to,
                summary=delivery.message,
                resource_uri=f"oflow://notification-deliveries/{delivery.id}",
                details={
                    "destination": delivery.destination,
                    "status": delivery.status,
                    "delivered_count": delivery.delivered_count,
                    "digest_as_of": delivery.digest_as_of,
                    "due_lookahead_days": delivery.due_lookahead_days,
                    "invoice_lookahead_days": delivery.invoice_lookahead_days,
                },
            )
        )

    return sorted(items, key=lambda item: (item["occurred_at"], item["entity_id"]), reverse=True)[:limit]


def _filter_admin_activity_items(
    items: list[dict[str, Any]],
    *,
    kind: str | None = None,
    case_id: int | None = None,
    document_id: int | None = None,
) -> list[dict[str, Any]]:
    filtered = items
    if kind:
        filtered = [item for item in filtered if item.get("kind") == kind]
    if case_id is not None:
        filtered = [
            item
            for item in filtered
            if item.get("entity_id") == case_id
            or item.get("details", {}).get("case_id") == case_id
        ]
    if document_id is not None:
        filtered = [
            item
            for item in filtered
            if item.get("entity_id") == document_id
            or item.get("details", {}).get("document_id") == document_id
        ]
    return filtered


def _ingest_payload(
    ingestion_service: IngestionService,
    *,
    case_code: str,
    title: str,
    filename: str,
    content: bytes,
    mime_type: str | None,
    source_type: str,
    source_path: str | None,
    client_name: str | None,
    due_date: str | None,
    invoice_status: str,
    output_status: str,
    extracted_text: str | None,
    structured_json: dict[str, Any] | None,
    rag_chunks: list[dict[str, Any]],
    output_html: str | None,
) -> dict[str, object]:
    result = ingestion_service.ingest(
        IngestionRequest(
            case_code=case_code,
            title=title,
            filename=filename,
            content=content,
            mime_type=mime_type,
            source_type=source_type,
            source_path=source_path,
            client_name=client_name,
            due_date=due_date,
            invoice_status=invoice_status,
            output_status=output_status,
            extracted_text=extracted_text,
            structured_json=structured_json,
            rag_chunks=rag_chunks,
            output_html=output_html,
        )
    )
    return _serialize(result)


def _ingest_chat_payload(
    app: FastAPI,
    *,
    platform: str,
    case_code: str,
    title: str,
    filename: str,
    content: bytes,
    mime_type: str | None,
    source_path: str | None,
    message_id: str | None,
    channel_id: str | None,
    author_name: str | None,
    message_text: str | None,
    client_name: str | None,
    due_date: str | None,
    invoice_status: str,
    output_status: str,
    extracted_text: str | None,
    structured_json: dict[str, Any] | None,
    rag_chunks: list[dict[str, Any]],
    output_html: str | None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, object]:
    resolved_structured_json = structured_json
    if resolved_structured_json is None and (
        source_path is not None
        or message_id is not None
        or channel_id is not None
        or author_name is not None
        or message_text is not None
        or extra_metadata is not None
    ):
        resolved_structured_json = build_chat_metadata_json(
            platform=platform,
            source_path=source_path,
            message_id=message_id,
            channel_id=channel_id,
            author_name=author_name,
            message_text=message_text,
            extra=extra_metadata,
        )
    return _ingest_payload(
        app.state.ingestion_service,
        case_code=case_code,
        title=title,
        filename=filename,
        content=content,
        mime_type=mime_type,
        source_type=platform,
        source_path=source_path,
        client_name=client_name,
        due_date=due_date,
        invoice_status=invoice_status,
        output_status=output_status,
        extracted_text=extracted_text,
        structured_json=resolved_structured_json,
        rag_chunks=rag_chunks,
        output_html=output_html,
    )


class IngestionCreateRequest(BaseModel):
    case_code: str
    title: str
    filename: str
    content_base64: str = Field(..., description="Base64 encoded file content.")
    mime_type: str | None = None
    source_type: str = "api"
    source_path: str | None = None
    client_name: str | None = None
    due_date: str | None = None
    invoice_status: str = "unbilled"
    output_status: str = "pending"
    extracted_text: str | None = None
    structured_json: dict[str, Any] | None = None
    rag_chunks: list[dict[str, Any]] = Field(default_factory=list)
    output_html: str | None = None

    @field_validator("content_base64")
    @classmethod
    def validate_content_base64(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content_base64 must not be empty")
        return value


class ChatIngestionCreateRequest(BaseModel):
    case_code: str
    title: str
    filename: str
    content_base64: str = Field(..., description="Base64 encoded file content.")
    mime_type: str | None = None
    platform: str = Field(default="discord", description="Chat platform such as discord or line.")
    source_path: str | None = None
    message_id: str | None = None
    channel_id: str | None = None
    author_name: str | None = None
    message_text: str | None = None
    client_name: str | None = None
    due_date: str | None = None
    invoice_status: str = "unbilled"
    output_status: str = "pending"
    extracted_text: str | None = None
    structured_json: dict[str, Any] | None = None
    rag_chunks: list[dict[str, Any]] = Field(default_factory=list)
    output_html: str | None = None

    @field_validator("content_base64")
    @classmethod
    def validate_content_base64(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content_base64 must not be empty")
        return value


class DiscordChatIngestionRequest(BaseModel):
    case_code: str
    title: str
    filename: str
    content_base64: str = Field(..., description="Base64 encoded file content.")
    mime_type: str | None = None
    source_path: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None
    message_id: str | None = None
    author_name: str | None = None
    message_text: str | None = None
    client_name: str | None = None
    due_date: str | None = None
    invoice_status: str = "unbilled"
    output_status: str = "pending"
    extracted_text: str | None = None
    structured_json: dict[str, Any] | None = None
    rag_chunks: list[dict[str, Any]] = Field(default_factory=list)
    output_html: str | None = None

    @field_validator("content_base64")
    @classmethod
    def validate_content_base64(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content_base64 must not be empty")
        return value


class LineChatIngestionRequest(BaseModel):
    case_code: str
    title: str
    filename: str
    content_base64: str = Field(..., description="Base64 encoded file content.")
    mime_type: str | None = None
    source_path: str | None = None
    room_id: str | None = None
    group_id: str | None = None
    user_id: str | None = None
    message_id: str | None = None
    author_name: str | None = None
    message_text: str | None = None
    client_name: str | None = None
    due_date: str | None = None
    invoice_status: str = "unbilled"
    output_status: str = "pending"
    extracted_text: str | None = None
    structured_json: dict[str, Any] | None = None
    rag_chunks: list[dict[str, Any]] = Field(default_factory=list)
    output_html: str | None = None

    @field_validator("content_base64")
    @classmethod
    def validate_content_base64(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content_base64 must not be empty")
        return value


class CaseUpdateRequest(BaseModel):
    title: str | None = None
    client_name: str | None = None
    status: str | None = None
    due_date: str | None = None
    invoice_status: str | None = None
    output_status: str | None = None
    last_processed_at: str | None = None


class CaseCreateRequest(BaseModel):
    case_code: str
    title: str
    client_name: str | None = None
    status: str = "new"
    due_date: str | None = None
    invoice_status: str = "unbilled"
    output_status: str = "pending"
    last_processed_at: str | None = None


class CaseBulkUpdateRequest(BaseModel):
    case_ids: list[int]
    title: str | None = None
    client_name: str | None = None
    status: str | None = None
    due_date: str | None = None
    invoice_status: str | None = None
    output_status: str | None = None
    last_processed_at: str | None = None


class DocumentReassignRequest(BaseModel):
    target_case_id: int


class DocumentBulkReprocessRequest(BaseModel):
    document_ids: list[int]


def create_app() -> FastAPI:
    settings = load_settings()
    repository = create_repository(settings)
    storage = create_storage(settings)
    ingestion_service = IngestionService(settings, repository, storage)
    document_service = DocumentService(settings, repository, storage)
    mcp_transport = MCPHttpTransport(repository)
    line_webhook_client = LineWebhookClient(settings, ingestion_service)

    app = FastAPI(title="O's flow V2", version="0.1.0")
    app.state.settings = settings
    app.state.repository = repository
    app.state.storage = storage
    app.state.ingestion_service = ingestion_service
    app.state.document_service = document_service
    app.state.mcp_transport = mcp_transport
    app.state.line_webhook_client = line_webhook_client

    def current_repository():
        return app.state.repository

    def notify_mcp_resource_changed(
        *resource_uris: str,
        payload: Any | None = None,
        event_type: str = "resource_changed",
    ) -> None:
        notifier = getattr(app.state.mcp_transport, "notify_resource_changed", None)
        if not callable(notifier):
            return
        for resource_uri in dict.fromkeys(resource_uris):
            notifier(resource_uri, payload=payload or {}, event_type=event_type)

    def record_line_webhook_log(
        *,
        event_type: str,
        message: str,
        case_id: int | None = None,
        document_id: int | None = None,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        app.state.repository.record_operation_log(
            event_type=event_type,
            entity_type="line_webhook",
            message=message,
            case_id=case_id,
            document_id=document_id,
            metadata_json=metadata_json or {},
        )

    def _line_message_type(item: Any) -> str | None:
        event_json = getattr(item, "event_json", None)
        if not isinstance(event_json, dict):
            return None
        message = event_json.get("message")
        if not isinstance(message, dict):
            return None
        message_type = message.get("type")
        return str(message_type) if message_type is not None else None

    def _line_event_summary(item: Any) -> str | None:
        event_json = getattr(item, "event_json", None)
        if not isinstance(event_json, dict):
            return None
        event_type = str(event_json.get("type") or "unknown")
        source = event_json.get("source") or {}
        source_type = str(source.get("type") or "")
        source_id = source.get("groupId") or source.get("roomId") or source.get("userId") or ""
        summary = f"LINE {event_type} event"
        if source_type or source_id:
            summary += f" from {source_type or 'unknown'}"
            if source_id:
                summary += f" {source_id}"
        if event_type == "unsend":
            unsend = event_json.get("unsend") or {}
            unsend_message_id = unsend.get("messageId") or unsend.get("message_id")
            if unsend_message_id:
                summary += f" for message {unsend_message_id}"
        return summary

    def _line_event_extra_metadata(item: Any) -> dict[str, Any]:
        event_json = getattr(item, "event_json", None)
        if not isinstance(event_json, dict):
            return {}
        event_type = str(event_json.get("type") or "unknown")
        extra_metadata: dict[str, Any] = {}
        if event_type == "unsend":
            unsend = event_json.get("unsend") or {}
            unsend_message_id = unsend.get("messageId") or unsend.get("message_id")
            if unsend_message_id:
                extra_metadata["unsend_message_id"] = unsend_message_id
        return extra_metadata

    def _line_webhook_event_types() -> tuple[str, ...]:
        return (
            "line_webhook_ingested",
            "line_webhook_pending",
            "line_webhook_skipped",
            "line_webhook_signature_invalid",
            "line_webhook_retry_ingested",
            "line_webhook_retry_pending",
            "line_webhook_retry_skipped",
        )

    def _list_line_webhook_logs(repository, limit_per_type: int = 200) -> list[Any]:  # noqa: ANN001
        logs: list[Any] = []
        for event_type in _line_webhook_event_types():
            logs.extend(repository.list_operation_logs(event_type=event_type, limit=limit_per_type))
        return sorted(logs, key=lambda item: (item.created_at, item.id), reverse=True)

    def _serialize_line_webhook_log(log: Any) -> dict[str, Any]:
        metadata_json = _parse_metadata_json(log.metadata_json)
        event_json = metadata_json.get("event_json")
        return {
            "log_id": log.id,
            "operation_event_type": log.event_type,
            "created_at": log.created_at,
            "message": log.message,
            "case_id": log.case_id,
            "document_id": log.document_id,
            "case_code": metadata_json.get("case_code"),
            "status": metadata_json.get("status") or (str(log.event_type).removeprefix("line_webhook_") if log.event_type else None),
            "line_event_type": metadata_json.get("event_type"),
            "message_type": metadata_json.get("message_type"),
            "event_summary": metadata_json.get("event_summary"),
            "reason": metadata_json.get("reason"),
            "retry_of_log_id": metadata_json.get("retry_of_log_id"),
            "event_json": event_json,
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "app": "O's flow V2"}

    @app.post("/mcp")
    async def mcp_post(request: Request):
        payload = await request.json()
        app.state.mcp_transport.repository = current_repository()
        return app.state.mcp_transport.handle_post(
            payload,
            accept=request.headers.get("Accept"),
            protocol_version=request.headers.get("MCP-Protocol-Version"),
            session_id=request.headers.get("Mcp-Session-Id"),
        )

    @app.get("/mcp")
    async def mcp_get(request: Request):
        return app.state.mcp_transport.handle_get(request)

    @app.delete("/mcp")
    def mcp_delete(request: Request):
        app.state.mcp_transport.repository = current_repository()
        return app.state.mcp_transport.handle_delete(request.headers.get("Mcp-Session-Id"))

    @app.get("/mcp/subscriptions")
    def mcp_subscriptions() -> dict[str, object]:
        return app.state.mcp_transport.list_subscriptions()

    @app.get("/mcp/events")
    def mcp_events(
        session_id: str | None = None,
        event_type: str | None = None,
        resource_uri: str | None = None,
    ) -> dict[str, object]:
        return app.state.mcp_transport.list_events(
            session_id=session_id,
            event_type=event_type,
            resource_uri=resource_uri,
        )

    @app.get("/mcp/overview")
    def mcp_overview(
        session_id: str | None = None,
        event_type: str | None = None,
        resource_uri: str | None = None,
    ) -> dict[str, object]:
        return app.state.mcp_transport.get_overview(
            session_id=session_id,
            event_type=event_type,
            resource_uri=resource_uri,
        )

    @app.get("/mcp/dashboard")
    def mcp_dashboard(
        session_id: str | None = None,
        event_type: str | None = None,
        resource_uri: str | None = None,
    ) -> dict[str, object]:
        return app.state.mcp_transport.get_dashboard(
            session_id=session_id,
            event_type=event_type,
            resource_uri=resource_uri,
        )

    @app.get("/meta")
    def meta() -> dict[str, str]:
        return {
            "environment": settings.app_env,
            "database_path": str(settings.database_path),
            "storage_root": str(settings.storage_root),
        }

    @app.get("/summary")
    def summary() -> dict[str, int]:
        repository = current_repository()
        return {
            "cases_total": repository.count_cases(),
            "documents_total": repository.count_documents(),
            "documents_active": repository.count_documents(is_deleted=False),
            "processing_jobs_total": repository.count_processing_jobs(),
            "operation_logs_total": repository.count_operation_logs(),
            "notification_deliveries_total": repository.count_notification_deliveries(),
            "rag_entries_total": repository.count_rag(query=""),
        }

    @app.get("/admin/overview")
    def admin_overview() -> dict[str, object]:
        repository = current_repository()
        case_statuses = ("new", "in_progress", "completed")
        invoice_statuses = ("unbilled", "pending")
        output_statuses = ("pending", "completed")
        source_types = ("discord", "line", "api")
        return {
            "settings": {
                "app_env": settings.app_env,
                "repository_backend": settings.repository_backend,
                "storage_backend": settings.storage_backend,
                "insforge": _build_insforge_backend_status(settings),
                "extraction": _build_extraction_backend_status(),
            },
            "summary": {
                "cases_total": repository.count_cases(),
                "documents_total": repository.count_documents(),
                "documents_active": repository.count_documents(is_deleted=False),
                "processing_jobs_total": repository.count_processing_jobs(),
                "operation_logs_total": repository.count_operation_logs(),
                "notification_deliveries_total": repository.count_notification_deliveries(),
                "rag_entries_total": repository.count_rag(query=""),
            },
            "breakdown": {
                "case_statuses": {status: repository.count_cases(status=status) for status in case_statuses},
                "invoice_statuses": {status: repository.count_cases(invoice_status=status) for status in invoice_statuses},
                "output_statuses": {status: repository.count_cases(output_status=status) for status in output_statuses},
                "document_source_types": {source_type: repository.count_documents(source_type=source_type) for source_type in source_types},
            },
        }

    @app.get("/admin/backends")
    def admin_backends() -> dict[str, object]:
        return {
            "app_env": settings.app_env,
            "repository_backend": settings.repository_backend,
            "storage_backend": settings.storage_backend,
            "insforge": _build_insforge_backend_status(settings),
            "extraction": _build_extraction_backend_status(),
        }

    @app.get("/admin/recent")
    def admin_recent(limit: int = 10) -> dict[str, object]:
        repository = current_repository()
        normalized_limit = max(1, min(limit, 50))
        return {
            "limit": normalized_limit,
            "cases": _serialize(repository.search_cases(limit=normalized_limit)),
            "documents": attach_document_extraction_snapshots(
                repository.list_documents(limit=normalized_limit),
                repository.get_case_detail,
                serialize_document=_serialize,
            ),
            "operation_logs": _serialize(repository.list_operation_logs(limit=normalized_limit)),
            "notification_deliveries": _serialize(repository.list_notification_deliveries(limit=normalized_limit)),
        }

    @app.get("/admin/activity")
    def admin_activity(
        limit: int = 20,
        kind: str | None = None,
        case_id: int | None = None,
        document_id: int | None = None,
    ) -> dict[str, object]:
        repository = current_repository()
        normalized_limit = max(1, min(limit, 50))
        items = _build_admin_activity_items(repository, limit=max(normalized_limit, 20))
        filtered_items = _filter_admin_activity_items(
            items,
            kind=kind,
            case_id=case_id,
            document_id=document_id,
        )
        return {
            "limit": normalized_limit,
            "items": filtered_items[:normalized_limit],
        }

    @app.get("/admin/dashboard")
    def admin_dashboard(
        recent_limit: int = 5,
        activity_limit: int = 20,
        kind: str | None = None,
        case_id: int | None = None,
        document_id: int | None = None,
        deliver_to: str | None = None,
    ) -> dict[str, object]:
        repository = current_repository()
        normalized_recent_limit = max(1, min(recent_limit, 50))
        normalized_activity_limit = max(1, min(activity_limit, 50))
        return {
            "overview": admin_overview(),
            "recent": admin_recent(limit=normalized_recent_limit),
            "activity": admin_activity(
                limit=normalized_activity_limit,
                kind=kind,
                case_id=case_id,
                document_id=document_id,
            ),
            "notifications": build_notification_delivery_summary(
                repository,
                deliver_to=deliver_to,
                recent_failures_limit=normalized_recent_limit,
            ),
        }

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page() -> str:
        dashboard = admin_dashboard(recent_limit=3, activity_limit=5)
        overview = dashboard["overview"]
        recent = dashboard["recent"]
        activity = dashboard["activity"]
        notifications = dashboard["notifications"]

        overview_summary = overview["summary"]
        overview_settings = overview["settings"]
        extraction_settings = overview_settings["extraction"]
        recent_cases = recent["cases"]
        recent_documents = recent["documents"]
        recent_logs = recent["operation_logs"]
        recent_deliveries = recent["notification_deliveries"]
        activity_items = activity["items"]

        recent_cases_html = "".join(
            f"<li><strong>{escape(item['case_code'])}</strong> - {escape(item['title'])}</li>"
            for item in recent_cases
        ) or "<li>No recent cases.</li>"
        recent_documents_html = "".join(
            f"<li><strong>{escape(item['filename'])}</strong> - {escape(item['source_type'])} - "
            f"{escape((item.get('extraction') or {}).get('extraction_source') or 'no extraction snapshot')}</li>"
            for item in recent_documents
        ) or "<li>No recent documents.</li>"
        recent_logs_html = "".join(
            f"<li><strong>{escape(item['event_type'])}</strong> - {escape(item['message'])}</li>"
            for item in recent_logs
        ) or "<li>No recent operation logs.</li>"
        recent_deliveries_html = "".join(
            f"<li><strong>{escape(item['deliver_to'])}</strong> - {escape(item['status'])}</li>"
            for item in recent_deliveries
        ) or "<li>No recent notification deliveries.</li>"
        activity_html = "".join(
            f"<li><strong>{escape(item['kind'])}</strong> - {escape(item['title'])} - {escape(item['summary'])}</li>"
            for item in activity_items
        ) or "<li>No recent activity.</li>"

        return f"""
        <html>
            <head>
                <title>O's flow Admin</title>
                <meta charset="utf-8" />
            </head>
            <body>
                <main>
                    <h1>O's flow Admin</h1>
                    <p>Operational landing page for the admin surface.</p>
                    <section>
                        <h2>Quick Links</h2>
                        <ul>
                            <li><a href="/admin/overview">/admin/overview</a></li>
                            <li><a href="/admin/recent">/admin/recent</a></li>
                            <li><a href="/admin/activity">/admin/activity</a></li>
                            <li><a href="/admin/dashboard">/admin/dashboard</a></li>
                            <li><a href="/notification-deliveries/report">/notification-deliveries/report</a></li>
                        </ul>
                    </section>
                    <section>
                        <h2>Snapshot</h2>
                        <ul>
                            <li>Environment: {escape(str(overview_settings["app_env"]))}</li>
                            <li>Repository: {escape(str(overview_settings["repository_backend"]))}</li>
                            <li>Storage: {escape(str(overview_settings["storage_backend"]))}</li>
                            <li>Extraction helpers: {escape(", ".join(name for name, enabled in extraction_settings.items() if enabled and name in {"pypdf", "pdfplumber", "pdf2image", "pillow", "pytesseract"}) or "none")}</li>
                            <li>PDF text parsing ready: {escape(str(extraction_settings["pdf_text_parsing_ready"]))}</li>
                            <li>Image OCR ready: {escape(str(extraction_settings["image_ocr_ready"]))}</li>
                            <li>Scanned PDF OCR ready: {escape(str(extraction_settings["scanned_pdf_ocr_ready"]))}</li>
                            <li>Cases: {overview_summary["cases_total"]}</li>
                            <li>Documents: {overview_summary["documents_total"]}</li>
                            <li>Operation logs: {overview_summary["operation_logs_total"]}</li>
                            <li>Notification deliveries: {overview_summary["notification_deliveries_total"]}</li>
                            <li>Notification deliveries with attention: {notifications["needs_attention"]}</li>
                        </ul>
                    </section>
                    <section>
                        <h2>Recent Cases</h2>
                        <ul>{recent_cases_html}</ul>
                    </section>
                    <section>
                        <h2>Recent Documents</h2>
                        <ul>{recent_documents_html}</ul>
                    </section>
                    <section>
                        <h2>Recent Operation Logs</h2>
                        <ul>{recent_logs_html}</ul>
                    </section>
                    <section>
                        <h2>Recent Notification Deliveries</h2>
                        <ul>{recent_deliveries_html}</ul>
                    </section>
                    <section>
                        <h2>Activity Timeline</h2>
                        <ul>{activity_html}</ul>
                    </section>
                </main>
            </body>
        </html>
        """

    @app.get("/admin/resources")
    def admin_resources() -> dict[str, object]:
        return {
            "resources": [
                {
                    "name": "cases",
                    "title": "Cases",
                    "id_field": "id",
                    "label_field": "case_code",
                    "default_sort": {"field": "updated_at", "order": "DESC"},
                    "supports": ["list", "show", "edit"],
                    "actions": ["edit", "activity"],
                    "collection_path": "/cases",
                    "search_path": "/cases/search",
                    "detail_key": "case_id",
                    "detail_path": "/cases/{case_id}",
                    "edit_path": "/cases/{case_id}",
                    "activity_path": "/cases/{case_id}/activity",
                    "list_fields": ["id", "case_code", "title", "client_name", "status", "due_date", "invoice_status", "output_status", "updated_at"],
                    "detail_fields": ["id", "case_code", "title", "client_name", "status", "due_date", "invoice_status", "output_status", "created_at", "updated_at", "last_processed_at"],
                    "form_fields": [
                        {"name": "title", "label": "title", "input_type": "text", "placeholder": "title"},
                        {"name": "client_name", "label": "client_name", "input_type": "text", "placeholder": "client_name"},
                        {"name": "status", "label": "status", "input_type": "text", "placeholder": "status"},
                        {"name": "due_date", "label": "due_date", "input_type": "date", "placeholder": "due_date"},
                        {"name": "invoice_status", "label": "invoice_status", "input_type": "text", "placeholder": "invoice_status"},
                        {"name": "output_status", "label": "output_status", "input_type": "text", "placeholder": "output_status"},
                        {"name": "last_processed_at", "label": "last_processed_at", "input_type": "text", "placeholder": "last_processed_at"},
                    ],
                    "editable_fields": ["title", "client_name", "status", "due_date", "invoice_status", "output_status", "last_processed_at"],
                    "filters": ["query", "status", "due_before", "invoice_status", "output_status"],
                },
                {
                    "name": "documents",
                    "title": "Documents",
                    "id_field": "id",
                    "label_field": "filename",
                    "default_sort": {"field": "updated_at", "order": "DESC"},
                    "supports": ["list", "show", "edit"],
                    "actions": ["manage", "activity", "reassign", "reprocess", "delete"],
                    "collection_path": "/documents",
                    "detail_key": "document_id",
                    "detail_path": "/documents/{document_id}",
                    "activity_path": "/documents/{document_id}/activity",
                    "list_fields": ["id", "case_id", "source_type", "filename", "mime_type", "version", "is_deleted", "extraction", "updated_at"],
                    "detail_fields": ["id", "case_id", "source_type", "source_path", "storage_key", "filename", "mime_type", "content_hash", "size_bytes", "version", "is_deleted", "deleted_at", "created_at", "updated_at", "extraction"],
                    "filters": ["case_id", "source_type", "is_deleted", "query"],
                },
                {
                    "name": "operation_logs",
                    "title": "Operation Logs",
                    "id_field": "id",
                    "label_field": "event_type",
                    "default_sort": {"field": "created_at", "order": "DESC"},
                    "supports": ["list", "show"],
                    "actions": ["view"],
                    "collection_path": "/operation-logs",
                    "detail_key": "operation_log_id",
                    "detail_path": "/operation-logs/{operation_log_id}",
                    "list_fields": ["id", "event_type", "entity_type", "entity_id", "case_id", "document_id", "message", "created_at"],
                    "detail_fields": ["id", "event_type", "entity_type", "entity_id", "case_id", "document_id", "message", "metadata_json", "created_at"],
                    "filters": ["case_id", "document_id", "event_type"],
                },
                {
                    "name": "notification_deliveries",
                    "title": "Notification Deliveries",
                    "id_field": "id",
                    "label_field": "deliver_to",
                    "default_sort": {"field": "created_at", "order": "DESC"},
                    "supports": ["list", "show"],
                    "actions": ["view", "summary", "trends", "alerts", "report"],
                    "collection_path": "/notification-deliveries",
                    "detail_key": "notification_delivery_id",
                    "detail_path": "/notification-deliveries/{notification_delivery_id}",
                    "summary_path": "/notification-deliveries/summary",
                    "trends_path": "/notification-deliveries/trends",
                    "alerts_path": "/notification-deliveries/alerts",
                    "report_path": "/notification-deliveries/report",
                    "list_fields": ["id", "deliver_to", "destination", "status", "delivered_count", "digest_as_of", "created_at"],
                    "detail_fields": ["id", "deliver_to", "destination", "delivered_count", "digest_as_of", "due_lookahead_days", "invoice_lookahead_days", "status", "message", "error_message", "metadata_json", "created_at"],
                    "filters": ["deliver_to", "status", "created_after", "created_before"],
                },
                {
                    "name": "admin",
                    "title": "Admin Dashboard",
                    "id_field": "name",
                    "label_field": "title",
                    "default_sort": {"field": "name", "order": "ASC"},
                    "supports": ["dashboard"],
                    "actions": ["dashboard"],
                    "collection_path": "/admin/dashboard",
                    "detail_key": "name",
                    "overview_path": "/admin/overview",
                    "recent_path": "/admin/recent",
                    "activity_path": "/admin/activity",
                    "list_fields": ["name", "title", "collection_path", "overview_path", "recent_path", "activity_path"],
                },
            ]
        }

    @app.get("/admin/react-admin")
    def admin_react_admin() -> dict[str, object]:
        resources = admin_resources()["resources"]
        return {
            "framework": "react-admin",
            "resources": [
                {
                    "name": resource["name"],
                    "label": resource["title"],
                    "idField": resource["id_field"],
                    "labelField": resource["label_field"],
                    "sort": resource["default_sort"],
                    "supports": resource.get("supports", []),
                    "actions": resource.get("actions", []),
                    "listPath": resource.get("collection_path"),
                    "showPath": resource.get("detail_path"),
                    "editPath": resource.get("edit_path"),
                    "activityPath": resource.get("activity_path"),
                    "summaryPath": resource.get("summary_path"),
                    "trendsPath": resource.get("trends_path"),
                    "alertsPath": resource.get("alerts_path"),
                    "reportPath": resource.get("report_path"),
                    "fields": resource.get("list_fields", []),
                    "detailFields": resource.get("detail_fields", []),
                    "formFields": resource.get("form_fields", []),
                    "filters": resource.get("filters", []),
                    "searchPath": resource.get("search_path"),
                }
                for resource in resources
            ],
        }

    @app.get("/admin/ui", response_class=HTMLResponse)
    def admin_ui() -> str:
        return """
        <html>
            <head>
                <title>O's flow Admin UI</title>
                <meta charset="utf-8" />
                <style>
                    body { font-family: Arial, sans-serif; margin: 0; background: #f6f7fb; color: #1f2937; }
                    main { max-width: 1200px; margin: 0 auto; padding: 24px; }
                    header { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 24px; }
                    h1, h2, h3 { margin: 0 0 12px; }
                    .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
                    .card { background: white; border-radius: 12px; padding: 16px; box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08); }
                    .toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
                    .toolbar input, .toolbar select, .toolbar button { padding: 10px 12px; border-radius: 8px; border: 1px solid #cbd5e1; }
                    .toolbar button { background: #0f172a; color: white; border: 0; cursor: pointer; }
                    .toolbar button.secondary { background: #e2e8f0; color: #0f172a; }
                    ul { padding-left: 20px; margin: 0; }
                    li { margin: 6px 0; }
                    code { background: #eef2ff; padding: 2px 6px; border-radius: 6px; }
                    .muted { color: #64748b; }
                    .status-good { color: #166534; }
                    .status-warn { color: #b45309; }
                    .status-bad { color: #b91c1c; }
                    pre { white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 8px; overflow-x: auto; }
                </style>
            </head>
            <body>
                <main>
                    <header>
                        <div>
                            <h1>O's flow Admin UI</h1>
                            <p class="muted">Lightweight operational view for the eventual React-admin surface.</p>
                        </div>
                        <div class="toolbar">
                            <button id="refreshButton" type="button">Refresh</button>
                            <button id="loadResourcesButton" type="button" class="secondary">Load resources</button>
                        </div>
                    </header>

                    <section class="card">
                        <h2>Filters</h2>
                        <div class="toolbar">
                            <select id="activityKind">
                                <option value="">All kinds</option>
                                <option value="case">case</option>
                                <option value="document">document</option>
                                <option value="operation_log">operation_log</option>
                                <option value="notification_delivery">notification_delivery</option>
                            </select>
                            <input id="activityCaseId" type="number" min="1" placeholder="Case ID" />
                            <input id="activityDocumentId" type="number" min="1" placeholder="Document ID" />
                            <input id="activityDeliverTo" type="text" placeholder="Deliver to" />
                        </div>
                    </section>

                    <div class="grid">
                        <section class="card">
                            <h2>Overview</h2>
                            <div id="overviewContent" class="muted">Loading overview...</div>
                        </section>
                        <section class="card">
                            <h2>Recent</h2>
                            <div id="recentContent" class="muted">Loading recent items...</div>
                        </section>
                        <section class="card">
                            <h2>Activity</h2>
                            <div id="activityContent" class="muted">Loading activity...</div>
                        </section>
                        <section class="card">
                            <h2>Notifications</h2>
                            <div id="notificationContent" class="muted">Loading notification summary...</div>
                        </section>
                    </div>

                    <section class="card" style="margin-top: 16px;">
                        <h2>Resource Manifest</h2>
                        <div id="resourceContent" class="muted">Loading resources...</div>
                    </section>

                    <section class="card" style="margin-top: 16px;">
                        <h2>Resource Browser</h2>
                        <div class="toolbar">
                            <select id="resourceSelect"></select>
                            <input id="resourceLimit" type="number" min="1" max="50" value="5" />
                            <input id="resourceQuery" type="text" placeholder="query" />
                            <input id="resourceCaseId" type="number" min="1" placeholder="case_id" />
                            <input id="resourceDocumentId" type="number" min="1" placeholder="document_id" />
                            <input id="resourceStatus" type="text" placeholder="status" />
                            <input id="resourceDueBefore" type="date" placeholder="due_before" />
                            <input id="resourceInvoiceStatus" type="text" placeholder="invoice_status" />
                            <input id="resourceOutputStatus" type="text" placeholder="output_status" />
                            <input id="resourceDeliverTo" type="text" placeholder="deliver_to" />
                            <input id="resourceSourceType" type="text" placeholder="source_type" />
                            <button id="loadResourceButton" type="button">Load selected resource</button>
                        </div>
                        <div id="resourceActionBar" class="toolbar muted">Resource actions appear here after loading a resource.</div>
                        <div id="resourceBrowserContent" class="muted">Select a resource to inspect.</div>
                    </section>

                    <section class="card" style="margin-top: 16px;">
                        <h2>Case Editor</h2>
                        <div class="toolbar">
                            <input id="caseEditorId" type="number" min="1" placeholder="case_id" />
                            <button id="caseEditorLoadButton" type="button">Load case</button>
                            <button id="caseEditorSaveButton" type="button" class="secondary">Save case</button>
                        </div>
                        <div id="caseEditorFields" class="toolbar"></div>
                        <p id="caseEditorMessage" class="muted">Load a case or click a case detail to edit it here.</p>
                    </section>

                    <section class="card" style="margin-top: 16px;">
                        <h2>Document Tools</h2>
                        <div class="toolbar">
                            <input id="documentToolId" type="number" min="1" placeholder="document_id" />
                            <input id="documentToolTargetCaseId" type="number" min="1" placeholder="target_case_id" />
                            <button id="documentToolLoadButton" type="button">Load document</button>
                            <button id="documentToolReassignButton" type="button" class="secondary">Reassign</button>
                            <button id="documentToolReprocessButton" type="button" class="secondary">Reprocess</button>
                            <button id="documentToolDeleteButton" type="button" class="secondary">Delete</button>
                        </div>
                        <p id="documentToolMessage" class="muted">Load a document or click a document detail to manage it here.</p>
                    </section>

                    <section class="card" style="margin-top: 16px;">
                        <h2>Notification Explorer</h2>
                        <div class="toolbar">
                            <input id="notificationExplorerCreatedAfter" type="date" placeholder="created_after" />
                            <input id="notificationExplorerCreatedBefore" type="date" placeholder="created_before" />
                            <input id="notificationExplorerDeliverTo" type="text" placeholder="deliver_to" />
                            <input id="notificationExplorerGranularity" type="text" placeholder="granularity" value="day" />
                            <button id="notificationExplorerSummaryButton" type="button">Load summary</button>
                            <button id="notificationExplorerTrendsButton" type="button" class="secondary">Load trends</button>
                            <button id="notificationExplorerAlertsButton" type="button" class="secondary">Load alerts</button>
                            <button id="notificationExplorerReportButton" type="button" class="secondary">Load report</button>
                        </div>
                        <div id="notificationExplorerContent" class="muted">Load notification summary, trends, alerts, or report here.</div>
                    </section>

                    <section class="card" style="margin-top: 16px;">
                        <h2>Raw Payload</h2>
                        <pre id="rawContent">Loading raw payload...</pre>
                    </section>
                </main>
                <script>
                    const overviewContent = document.getElementById("overviewContent");
                    const recentContent = document.getElementById("recentContent");
                    const activityContent = document.getElementById("activityContent");
                    const notificationContent = document.getElementById("notificationContent");
                    const resourceContent = document.getElementById("resourceContent");
                    const resourceSelect = document.getElementById("resourceSelect");
                    const resourceLimit = document.getElementById("resourceLimit");
                    const resourceQuery = document.getElementById("resourceQuery");
                    const resourceCaseId = document.getElementById("resourceCaseId");
                    const resourceDocumentId = document.getElementById("resourceDocumentId");
                    const resourceStatus = document.getElementById("resourceStatus");
                    const resourceDueBefore = document.getElementById("resourceDueBefore");
                    const resourceInvoiceStatus = document.getElementById("resourceInvoiceStatus");
                    const resourceOutputStatus = document.getElementById("resourceOutputStatus");
                    const resourceDeliverTo = document.getElementById("resourceDeliverTo");
                    const resourceSourceType = document.getElementById("resourceSourceType");
                    const resourceActionBar = document.getElementById("resourceActionBar");
                    const resourceBrowserContent = document.getElementById("resourceBrowserContent");
                    const caseEditorId = document.getElementById("caseEditorId");
                    const caseEditorFields = document.getElementById("caseEditorFields");
                    const caseEditorMessage = document.getElementById("caseEditorMessage");
                    const documentToolId = document.getElementById("documentToolId");
                    const documentToolTargetCaseId = document.getElementById("documentToolTargetCaseId");
                    const documentToolMessage = document.getElementById("documentToolMessage");
                    const notificationExplorerCreatedAfter = document.getElementById("notificationExplorerCreatedAfter");
                    const notificationExplorerCreatedBefore = document.getElementById("notificationExplorerCreatedBefore");
                    const notificationExplorerDeliverTo = document.getElementById("notificationExplorerDeliverTo");
                    const notificationExplorerGranularity = document.getElementById("notificationExplorerGranularity");
                    const notificationExplorerContent = document.getElementById("notificationExplorerContent");
                    const rawContent = document.getElementById("rawContent");
                    let availableResources = [];
                    let currentCaseId = null;
                    let currentDocumentId = null;
                    let caseEditorFormFields = [
                        { name: "title", label: "title", input_type: "text", placeholder: "title" },
                        { name: "client_name", label: "client_name", input_type: "text", placeholder: "client_name" },
                        { name: "status", label: "status", input_type: "text", placeholder: "status" },
                        { name: "due_date", label: "due_date", input_type: "date", placeholder: "due_date" },
                        { name: "invoice_status", label: "invoice_status", input_type: "text", placeholder: "invoice_status" },
                        { name: "output_status", label: "output_status", input_type: "text", placeholder: "output_status" },
                        { name: "last_processed_at", label: "last_processed_at", input_type: "text", placeholder: "last_processed_at" },
                    ];
                    let caseEditorFieldElements = {};

                    function readFilters() {
                        const params = new URLSearchParams();
                        params.set("recent_limit", "5");
                        params.set("activity_limit", "10");
                        const kind = document.getElementById("activityKind").value.trim();
                        const caseId = document.getElementById("activityCaseId").value.trim();
                        const documentId = document.getElementById("activityDocumentId").value.trim();
                        const deliverTo = document.getElementById("activityDeliverTo").value.trim();
                        if (kind) params.set("kind", kind);
                        if (caseId) params.set("case_id", caseId);
                        if (documentId) params.set("document_id", documentId);
                        if (deliverTo) params.set("deliver_to", deliverTo);
                        return params;
                    }

                    function renderList(items, renderItem) {
                        if (!items || !items.length) {
                            return '<p class="muted">No items.</p>';
                        }
                        return '<ul>' + items.map(renderItem).join('') + '</ul>';
                    }

                    function renderOverview(payload) {
                        const summary = payload.overview.summary;
                        const settings = payload.overview.settings;
                        const extractionEnabled = Object.entries(settings.extraction || {})
                            .filter(([name, enabled]) => enabled && ["pypdf", "pdfplumber", "pdf2image", "pillow", "pytesseract"].includes(name))
                            .map(([name]) => name)
                            .join(", ") || "none";
                        overviewContent.innerHTML = [
                            `<p><strong>Environment:</strong> ${settings.app_env}</p>`,
                            `<p><strong>Repository:</strong> ${settings.repository_backend}</p>`,
                            `<p><strong>Storage:</strong> ${settings.storage_backend}</p>`,
                            `<p><strong>Extraction helpers:</strong> ${extractionEnabled}</p>`,
                            `<p><strong>PDF text parsing ready:</strong> ${settings.extraction.pdf_text_parsing_ready}</p>`,
                            `<p><strong>Image OCR ready:</strong> ${settings.extraction.image_ocr_ready}</p>`,
                            `<p><strong>Scanned PDF OCR ready:</strong> ${settings.extraction.scanned_pdf_ocr_ready}</p>`,
                            `<p><strong>Cases:</strong> ${summary.cases_total}</p>`,
                            `<p><strong>Documents:</strong> ${summary.documents_total}</p>`,
                            `<p><strong>Operation logs:</strong> ${summary.operation_logs_total}</p>`,
                            `<p><strong>Notification deliveries:</strong> ${summary.notification_deliveries_total}</p>`,
                            `<p class="${payload.notifications.needs_attention ? "status-warn" : "status-good"}"><strong>Attention needed:</strong> ${payload.notifications.needs_attention}</p>`,
                        ].join('');
                    }

                    function renderRecent(payload) {
                        recentContent.innerHTML = [
                            '<h3>Cases</h3>',
                            renderList(payload.recent.cases, (item) => `<li><code>${item.case_code}</code> ${item.title}</li>`),
                            '<h3>Documents</h3>',
                            renderList(
                                payload.recent.documents,
                                (item) => {
                                    const extraction = item.extraction || {};
                                    const extractionSummary = extraction.available
                                        ? `${extraction.extraction_source || "unknown"} via ${extraction.extraction_engine || "unknown"}`
                                        : "no extraction snapshot";
                                    return `<li><code>${item.filename}</code> ${item.source_type} - ${extractionSummary}</li>`;
                                },
                            ),
                            '<h3>Operation Logs</h3>',
                            renderList(payload.recent.operation_logs, (item) => `<li><code>${item.event_type}</code> ${item.message}</li>`),
                            '<h3>Notification Deliveries</h3>',
                            renderList(payload.recent.notification_deliveries, (item) => `<li><code>${item.deliver_to}</code> ${item.status}</li>`),
                        ].join('');
                    }

                    function renderActivity(payload) {
                        activityContent.innerHTML = renderList(payload.activity.items, (item) => `<li><code>${item.kind}</code> ${item.title} - ${item.summary}</li>`);
                    }

                    function renderNotifications(payload) {
                        const body = payload.notifications;
                        notificationContent.innerHTML = [
                            `<p><strong>Total:</strong> ${body.total}</p>`,
                            `<p><strong>Success:</strong> ${body.success_total}</p>`,
                            `<p><strong>Failed:</strong> ${body.failed_total}</p>`,
                            `<p><strong>Failure rate:</strong> ${body.failure_rate}</p>`,
                            renderList(body.recent_failures, (item) => `<li><code>${item.deliver_to}</code> ${item.status} ${item.created_at}</li>`),
                        ].join('');
                    }

                    function setCaseEditorMessage(message, className = "muted") {
                        caseEditorMessage.className = className;
                        caseEditorMessage.textContent = message;
                    }

                    function setDocumentToolMessage(message, className = "muted") {
                        documentToolMessage.className = className;
                        documentToolMessage.textContent = message;
                    }

                    function setNotificationExplorerMessage(message, className = "muted") {
                        notificationExplorerContent.className = className;
                        notificationExplorerContent.textContent = message;
                    }

                    function populateCaseEditor(caseDetail) {
                        currentCaseId = caseDetail.id;
                        caseEditorId.value = caseDetail.id ?? "";
                        for (const field of caseEditorFormFields) {
                            const element = caseEditorFieldElements[field.name];
                            if (element) {
                                element.value = caseDetail[field.name] ?? "";
                            }
                        }
                        setCaseEditorMessage(`Loaded case ${caseDetail.case_code}.`, "status-good");
                    }

                    function getCaseEditorPayload() {
                        const payload = {};
                        for (const field of caseEditorFormFields) {
                            const element = caseEditorFieldElements[field.name];
                            if (!element) {
                                continue;
                            }
                            const trimmed = element.value.trim();
                            if (trimmed) {
                                payload[field.name] = trimmed;
                            }
                        }
                        return payload;
                    }

                    function clearCaseEditor(message) {
                        currentCaseId = null;
                        for (const field of caseEditorFormFields) {
                            const element = caseEditorFieldElements[field.name];
                            if (element) {
                                element.value = "";
                            }
                        }
                        setCaseEditorMessage(message, "muted");
                    }

                    function renderCaseEditorFields(resource) {
                        const fields = resource?.form_fields || caseEditorFormFields;
                        caseEditorFormFields = fields;
                        caseEditorFieldElements = {};
                        caseEditorFields.innerHTML = fields.map((field) => {
                            const inputId = `caseEditorField-${field.name}`;
                            const minWidth = field.input_type === "date" ? "150px" : (field.name === "last_processed_at" ? "240px" : "220px");
                            return `
                                <label style="display: grid; gap: 4px; min-width: ${minWidth};">
                                    <span class="muted">${field.label || field.name}</span>
                                    <input id="${inputId}" type="${field.input_type || "text"}" placeholder="${field.placeholder || field.name}" />
                                </label>
                            `;
                        }).join("");
                        for (const field of fields) {
                            caseEditorFieldElements[field.name] = document.getElementById(`caseEditorField-${field.name}`);
                        }
                    }

                    function populateDocumentTool(documentDetail) {
                        currentDocumentId = documentDetail.id;
                        documentToolId.value = documentDetail.id ?? "";
                        documentToolTargetCaseId.value = documentDetail.case_id ?? "";
                        const extraction = documentDetail.extraction || {};
                        const extractionSummary = extraction.available
                            ? `${extraction.extraction_source || "unknown"} via ${extraction.extraction_engine || "unknown"}`
                            : "no extraction snapshot";
                        setDocumentToolMessage(`Loaded document ${documentDetail.filename}. Extraction: ${extractionSummary}.`, "status-good");
                    }

                    function clearDocumentTool(message) {
                        currentDocumentId = null;
                        documentToolId.value = "";
                        documentToolTargetCaseId.value = "";
                        setDocumentToolMessage(message, "muted");
                    }

                    async function loadDocumentTool(documentId) {
                        const value = String(documentId ?? documentToolId.value).trim();
                        if (!value) {
                            clearDocumentTool("Enter a document ID to load its details.");
                            return;
                        }
                        const response = await fetch(`/documents/${encodeURIComponent(value)}`);
                        const detail = await response.json();
                        if (!response.ok || !detail) {
                            clearDocumentTool("Document not found.");
                            rawContent.textContent = JSON.stringify(detail, null, 2);
                            return;
                        }
                        populateDocumentTool(detail);
                        rawContent.textContent = JSON.stringify({ resource: "documents", detail }, null, 2);
                    }

                    async function reassignDocumentTool() {
                        const documentId = String(documentToolId.value || currentDocumentId || "").trim();
                        const targetCaseId = String(documentToolTargetCaseId.value).trim();
                        if (!documentId || !targetCaseId) {
                            setDocumentToolMessage("Enter both document_id and target_case_id.", "status-warn");
                            return;
                        }
                        const response = await fetch(`/documents/${encodeURIComponent(documentId)}/reassign`, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ target_case_id: Number(targetCaseId) }),
                        });
                        const detail = await response.json();
                        if (!response.ok) {
                            setDocumentToolMessage(detail.detail || "Failed to reassign document.", "status-bad");
                            rawContent.textContent = JSON.stringify(detail, null, 2);
                            return;
                        }
                        populateDocumentTool(detail);
                        rawContent.textContent = JSON.stringify({ resource: "documents", detail }, null, 2);
                    }

                    async function reprocessDocumentTool() {
                        const documentId = String(documentToolId.value || currentDocumentId || "").trim();
                        if (!documentId) {
                            setDocumentToolMessage("Enter a document ID before reprocessing.", "status-warn");
                            return;
                        }
                        const response = await fetch(`/documents/${encodeURIComponent(documentId)}/reprocess`, {
                            method: "POST",
                        });
                        const detail = await response.json();
                        if (!response.ok) {
                            setDocumentToolMessage(detail.detail || "Failed to reprocess document.", "status-bad");
                            rawContent.textContent = JSON.stringify(detail, null, 2);
                            return;
                        }
                        populateDocumentTool(detail);
                        rawContent.textContent = JSON.stringify({ resource: "documents", detail }, null, 2);
                    }

                    async function deleteDocumentTool() {
                        const documentId = String(documentToolId.value || currentDocumentId || "").trim();
                        if (!documentId) {
                            setDocumentToolMessage("Enter a document ID before deleting.", "status-warn");
                            return;
                        }
                        if (!window.confirm(`Delete document ${documentId}?`)) {
                            return;
                        }
                        const response = await fetch(`/documents/${encodeURIComponent(documentId)}`, {
                            method: "DELETE",
                        });
                        const detail = await response.json();
                        if (!response.ok) {
                            setDocumentToolMessage(detail.detail || "Failed to delete document.", "status-bad");
                            rawContent.textContent = JSON.stringify(detail, null, 2);
                            return;
                        }
                        clearDocumentTool(`Deleted document ${documentId}.`);
                        rawContent.textContent = JSON.stringify({ resource: "documents", detail }, null, 2);
                    }

                    function buildNotificationExplorerParams(basePath) {
                        const params = new URLSearchParams();
                        const createdAfter = notificationExplorerCreatedAfter.value.trim();
                        const createdBefore = notificationExplorerCreatedBefore.value.trim();
                        const deliverTo = notificationExplorerDeliverTo.value.trim();
                        const granularity = notificationExplorerGranularity.value.trim() || "day";
                        if (createdAfter) {
                            params.set("created_after", createdAfter);
                        }
                        if (createdBefore) {
                            params.set("created_before", createdBefore);
                        }
                        if (deliverTo) {
                            params.set("deliver_to", deliverTo);
                        }
                        if (basePath.includes("trends") || basePath.includes("alerts") || basePath.includes("report")) {
                            params.set("granularity", granularity);
                        }
                        return params;
                    }

                    async function loadNotificationExplorer(basePath, label) {
                        const params = buildNotificationExplorerParams(basePath);
                        const response = await fetch(`${basePath}?${params.toString()}`);
                        const payload = await response.json();
                        if (!response.ok) {
                            setNotificationExplorerMessage(payload.detail || `Failed to load ${label}.`, "status-bad");
                            rawContent.textContent = JSON.stringify(payload, null, 2);
                            return;
                        }
                        notificationExplorerContent.innerHTML = `
                            <p><strong>${label} loaded.</strong></p>
                            <pre>${JSON.stringify(payload, null, 2)}</pre>
                        `;
                        setNotificationExplorerMessage(`Loaded ${label}.`, "status-good");
                        rawContent.textContent = JSON.stringify({ resource: label, payload }, null, 2);
                    }

                    async function loadCaseEditor(caseId) {
                        const value = String(caseId ?? caseEditorId.value).trim();
                        if (!value) {
                            clearCaseEditor("Enter a case ID to load its details.");
                            return;
                        }
                        const response = await fetch(`/cases/${encodeURIComponent(value)}`);
                        const detail = await response.json();
                        if (!response.ok || !detail) {
                            clearCaseEditor("Case not found.");
                            rawContent.textContent = JSON.stringify(detail, null, 2);
                            return;
                        }
                        populateCaseEditor(detail);
                        rawContent.textContent = JSON.stringify({ resource: "cases", detail }, null, 2);
                    }

                    async function saveCaseEditor() {
                        const caseId = String(caseEditorId.value || currentCaseId || "").trim();
                        if (!caseId) {
                            setCaseEditorMessage("Enter a case ID before saving.", "status-warn");
                            return;
                        }
                        const payload = getCaseEditorPayload();
                        if (!Object.keys(payload).length) {
                            setCaseEditorMessage("Add at least one field before saving.", "status-warn");
                            return;
                        }
                        const response = await fetch(`/cases/${encodeURIComponent(caseId)}`, {
                            method: "PATCH",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify(payload),
                        });
                        const detail = await response.json();
                        if (!response.ok) {
                            setCaseEditorMessage(detail.detail || "Failed to save case.", "status-bad");
                            rawContent.textContent = JSON.stringify(detail, null, 2);
                            return;
                        }
                        populateCaseEditor(detail);
                        rawContent.textContent = JSON.stringify({ resource: "cases", detail }, null, 2);
                    }

                    function renderResources(payload) {
                        availableResources = payload.resources || [];
                        if (!resourceSelect.options.length || resourceSelect.options.length !== availableResources.length) {
                            resourceSelect.innerHTML = availableResources.map((resource) => `<option value="${resource.name}">${resource.title}</option>`).join("");
                        }
                        const caseResource = availableResources.find((resource) => resource.name === "cases");
                        if (caseResource) {
                            renderCaseEditorFields(caseResource);
                        }
                        if (availableResources.length && !resourceSelect.value) {
                            resourceSelect.value = availableResources[0].name;
                        }
                        resourceContent.innerHTML = renderList(payload.resources, (resource) => {
                            const supportTags = (resource.supports || []).map((support) => `<code>${support}</code>`).join(" ");
                            const actionTags = (resource.actions || []).map((action) => `<code>${action}</code>`).join(" ");
                            const path = resource.collection_path || resource.overview_path || resource.summary_path || "";
                            return `<li><strong>${resource.title}</strong> <span class="muted">(${resource.name})</span> - ${path}<br /><span class="muted">${supportTags} ${actionTags}</span></li>`;
                        });
                    }

                    function buildResourceQuery(resource) {
                        const params = new URLSearchParams();
                        const limit = resourceLimit.value.trim();
                        if (limit) {
                            params.set("limit", limit);
                        }
                        const filterMap = {
                            query: resourceQuery.value.trim(),
                            case_id: resourceCaseId.value.trim(),
                            document_id: resourceDocumentId.value.trim(),
                            status: resourceStatus.value.trim(),
                            due_before: resourceDueBefore.value.trim(),
                            invoice_status: resourceInvoiceStatus.value.trim(),
                            output_status: resourceOutputStatus.value.trim(),
                            deliver_to: resourceDeliverTo.value.trim(),
                            source_type: resourceSourceType.value.trim(),
                        };
                        for (const [key, value] of Object.entries(filterMap)) {
                            if (value && (resource.filters || []).includes(key)) {
                                params.set(key, value);
                            }
                        }
                        return params;
                    }

                    function renderResourceActions(resource) {
                        if (!resource) {
                            resourceActionBar.className = "toolbar muted";
                            resourceActionBar.textContent = "Resource actions appear here after loading a resource.";
                            return;
                        }
                        const buttons = [];
                        if (resource.name === "cases") {
                            buttons.push(`<button type="button" id="resourceActionCaseEditor">Open case editor</button>`);
                            buttons.push(`<button type="button" id="resourceActionCaseActivity" class="secondary">Open case activity</button>`);
                        } else if (resource.name === "documents") {
                            buttons.push(`<button type="button" id="resourceActionDocumentTool">Open document tools</button>`);
                            buttons.push(`<button type="button" id="resourceActionDocumentActivity" class="secondary">Open document activity</button>`);
                        } else if (resource.name === "notification_deliveries") {
                            buttons.push(`<button type="button" id="resourceActionNotificationSummary">Summary</button>`);
                            buttons.push(`<button type="button" id="resourceActionNotificationTrends" class="secondary">Trends</button>`);
                            buttons.push(`<button type="button" id="resourceActionNotificationAlerts" class="secondary">Alerts</button>`);
                            buttons.push(`<button type="button" id="resourceActionNotificationReport" class="secondary">Report</button>`);
                        } else if (resource.name === "operation_logs") {
                            buttons.push(`<span class="muted">Open details from the row buttons below.</span>`);
                        } else {
                            buttons.push(`<span class="muted">No special actions.</span>`);
                        }
                        resourceActionBar.className = "toolbar";
                        resourceActionBar.innerHTML = buttons.join("");
                    }

                    function renderResourceTable(resource, items) {
                        const fields = [...(resource.list_fields || [])];
                        if (!Array.isArray(items)) {
                            return `<pre>${JSON.stringify(items, null, 2)}</pre>`;
                        }
                        if (!items.length) {
                            return `<p class="muted">No rows for ${resource.title}.</p>`;
                        }
                        const hasDetail = Boolean(resource.detail_path);
                        const hasActionButtons = Boolean(resource.actions && resource.actions.length);
                        const header = [...fields, hasDetail ? "detail" : null, hasActionButtons ? "actions" : null].filter(Boolean).map((field) => `<th>${field}</th>`).join("");
                        const rows = items.map((item) => {
                            const cells = fields.map((field) => {
                                if (field === "extraction") {
                                    const extraction = item.extraction || {};
                                    const summary = extraction.available
                                        ? `${extraction.extraction_source || "unknown"} via ${extraction.extraction_engine || "unknown"}`
                                        : "no extraction snapshot";
                                    return `<td>${summary}</td>`;
                                }
                                return `<td>${String(item[field] ?? "")}</td>`;
                            }).join("");
                            const detailCell = hasDetail
                                ? `<td><button type="button" data-resource="${resource.name}" data-item-id="${item[resource.id_field || "id"]}">Load detail</button></td>`
                                : "";
                            const actionButtons = [];
                            if ((resource.actions || []).includes("edit") && resource.name === "cases") {
                                actionButtons.push(`<button type="button" data-case-editor-id="${item[resource.id_field || "id"]}">Edit case</button>`);
                            }
                            if ((resource.actions || []).includes("manage") && resource.name === "documents") {
                                actionButtons.push(`<button type="button" data-document-tool-id="${item[resource.id_field || "id"]}">Manage document</button>`);
                            }
                            if ((resource.actions || []).includes("view") && !hasDetail) {
                                actionButtons.push(`<span class="muted">view</span>`);
                            }
                            const actionCell = hasActionButtons ? `<td>${actionButtons.join(" ")}</td>` : "";
                            return `<tr>${cells}${detailCell}${actionCell}</tr>`;
                        }).join("");
                        return `
                            <table style="width: 100%; border-collapse: collapse;">
                                <thead><tr>${header}</tr></thead>
                                <tbody>${rows}</tbody>
                            </table>
                        `;
                    }

                    function buildResourceDetailPath(resource, itemId) {
                        return (resource.detail_path || "").replace(`{${resource.detail_key || resource.id_field || "id"}}`, encodeURIComponent(String(itemId)));
                    }

                    async function loadResourceDetail(resourceName, itemId) {
                        const resource = availableResources.find((item) => item.name === resourceName);
                        if (!resource || !resource.detail_path) {
                            return;
                        }
                        const response = await fetch(buildResourceDetailPath(resource, itemId));
                        const detail = await response.json();
                        resourceBrowserContent.innerHTML = `<pre>${JSON.stringify(detail, null, 2)}</pre>`;
                        rawContent.textContent = JSON.stringify({ resource, detail }, null, 2);
                        if (resource.name === "cases" && detail) {
                            populateCaseEditor(detail);
                        }
                        if (resource.name === "documents" && detail) {
                            populateDocumentTool(detail);
                        }
                    }

                    async function loadSelectedResource() {
                        const selectedName = resourceSelect.value;
                        const resource = availableResources.find((item) => item.name === selectedName);
                        if (!resource) {
                            resourceBrowserContent.innerHTML = '<p class="muted">Select a resource.</p>';
                            renderResourceActions(null);
                            return;
                        }
                        const params = buildResourceQuery(resource);
                        const response = await fetch(`${resource.collection_path}?${params.toString()}`);
                        const items = await response.json();
                        renderResourceActions(resource);
                        resourceBrowserContent.innerHTML = renderResourceTable(resource, items);
                        rawContent.textContent = JSON.stringify({ resource, items }, null, 2);
                    }

                    async function loadAdmin() {
                        const params = readFilters();
                        const response = await fetch(`/admin/dashboard?${params.toString()}`);
                        const payload = await response.json();
                        renderOverview(payload);
                        renderRecent(payload);
                        renderActivity(payload);
                        renderNotifications(payload);
                        rawContent.textContent = JSON.stringify(payload, null, 2);
                    }

                    async function loadResources() {
                        const response = await fetch('/admin/resources');
                        const payload = await response.json();
                        renderResources(payload);
                        if (availableResources.length) {
                            await loadSelectedResource();
                        }
                    }

                    document.getElementById("refreshButton").addEventListener("click", loadAdmin);
                    document.getElementById("loadResourcesButton").addEventListener("click", loadResources);
                    document.getElementById("loadResourceButton").addEventListener("click", loadSelectedResource);
                    document.getElementById("caseEditorLoadButton").addEventListener("click", () => loadCaseEditor());
                    document.getElementById("caseEditorSaveButton").addEventListener("click", saveCaseEditor);
                    document.getElementById("documentToolLoadButton").addEventListener("click", () => loadDocumentTool());
                    document.getElementById("documentToolReassignButton").addEventListener("click", reassignDocumentTool);
                    document.getElementById("documentToolReprocessButton").addEventListener("click", reprocessDocumentTool);
                    document.getElementById("documentToolDeleteButton").addEventListener("click", deleteDocumentTool);
                    document.getElementById("notificationExplorerSummaryButton").addEventListener("click", () => loadNotificationExplorer("/notification-deliveries/summary", "notification summary"));
                    document.getElementById("notificationExplorerTrendsButton").addEventListener("click", () => loadNotificationExplorer("/notification-deliveries/trends", "notification trends"));
                    document.getElementById("notificationExplorerAlertsButton").addEventListener("click", () => loadNotificationExplorer("/notification-deliveries/alerts", "notification alerts"));
                    document.getElementById("notificationExplorerReportButton").addEventListener("click", () => loadNotificationExplorer("/notification-deliveries/report", "notification report"));
                    document.getElementById("activityKind").addEventListener("change", loadAdmin);
                    document.getElementById("activityCaseId").addEventListener("change", loadAdmin);
                    document.getElementById("activityDocumentId").addEventListener("change", loadAdmin);
                    document.getElementById("activityDeliverTo").addEventListener("change", loadAdmin);
                    resourceSelect.addEventListener("change", loadSelectedResource);
                    resourceActionBar.addEventListener("click", async (event) => {
                        if (event.target.id === "resourceActionCaseEditor") {
                            const firstCase = resourceSelect.value === "cases" ? caseEditorId.value : null;
                            await loadCaseEditor(firstCase);
                        }
                        if (event.target.id === "resourceActionCaseActivity") {
                            const caseId = caseEditorId.value || currentCaseId;
                            if (caseId) {
                                document.getElementById("activityKind").value = "case";
                                document.getElementById("activityCaseId").value = caseId;
                                await loadAdmin();
                            }
                        }
                        if (event.target.id === "resourceActionDocumentTool") {
                            const firstDocument = documentToolId.value || currentDocumentId;
                            await loadDocumentTool(firstDocument);
                        }
                        if (event.target.id === "resourceActionDocumentActivity") {
                            const documentId = documentToolId.value || currentDocumentId;
                            if (documentId) {
                                document.getElementById("activityKind").value = "document";
                                document.getElementById("activityDocumentId").value = documentId;
                                await loadAdmin();
                            }
                        }
                        if (event.target.id === "resourceActionNotificationSummary") {
                            await loadNotificationExplorer("/notification-deliveries/summary", "notification summary");
                        }
                        if (event.target.id === "resourceActionNotificationTrends") {
                            await loadNotificationExplorer("/notification-deliveries/trends", "notification trends");
                        }
                        if (event.target.id === "resourceActionNotificationAlerts") {
                            await loadNotificationExplorer("/notification-deliveries/alerts", "notification alerts");
                        }
                        if (event.target.id === "resourceActionNotificationReport") {
                            await loadNotificationExplorer("/notification-deliveries/report", "notification report");
                        }
                    });
                    resourceBrowserContent.addEventListener("click", async (event) => {
                        const button = event.target.closest("button[data-resource][data-item-id]");
                        if (!button) {
                            return;
                        }
                        await loadResourceDetail(button.dataset.resource, button.dataset.itemId);
                    });
                    resourceBrowserContent.addEventListener("click", async (event) => {
                        const caseButton = event.target.closest("button[data-case-editor-id]");
                        if (caseButton) {
                            caseEditorId.value = caseButton.dataset.caseEditorId;
                            await loadCaseEditor(caseButton.dataset.caseEditorId);
                            return;
                        }
                        const documentButton = event.target.closest("button[data-document-tool-id]");
                        if (documentButton) {
                            documentToolId.value = documentButton.dataset.documentToolId;
                            await loadDocumentTool(documentButton.dataset.documentToolId);
                        }
                    });

                    loadAdmin();
                    loadResources();
                </script>
            </body>
        </html>
        """

    @app.get("/notifications/due")
    def preview_notifications(
        as_of: str | None = None,
        due_lookahead_days: int = 1,
        invoice_lookahead_days: int = 7,
        case_status: str | None = "in_progress",
        invoice_status: str | None = "pending",
    ) -> dict[str, object]:
        try:
            as_of_date = date.fromisoformat(as_of) if as_of else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid as_of date: {as_of}") from exc
        service = NotificationService(current_repository())
        return _serialize(
            service.build_daily_digest(
                as_of=as_of_date,
                due_lookahead_days=due_lookahead_days,
                invoice_lookahead_days=invoice_lookahead_days,
                case_status=case_status,
                invoice_status=invoice_status,
            )
        )

    @app.patch("/cases/bulk")
    def bulk_update_cases(payload: CaseBulkUpdateRequest) -> list[dict[str, object]]:
        from fastapi import HTTPException

        data = payload.model_dump(exclude={"case_ids"}, exclude_unset=True)
        if not data:
            raise HTTPException(status_code=400, detail="At least one field must be provided.")
        if not payload.case_ids:
            raise HTTPException(status_code=400, detail="case_ids must not be empty.")

        repository = current_repository()
        missing_case_ids = [case_id for case_id in payload.case_ids if repository.get_case(case_id) is None]
        if missing_case_ids:
            raise HTTPException(status_code=404, detail=f"Cases not found: {missing_case_ids}")

        updated_cases = []
        for case_id in payload.case_ids:
            case = repository.update_case(case_id, **data, record_log=False)
            repository.record_operation_log(
                event_type="case_bulk_updated",
                entity_type="case",
                entity_id=case.id,
                case_id=case.id,
                message=f"Case bulk updated: {case.case_code}",
                metadata_json={
                    "case_ids": payload.case_ids,
                    "changed_fields": list(data.keys()),
                },
            )
            updated_cases.append(case)
        notify_mcp_resource_changed(
            *[f"oflow://cases/{case.id}" for case in updated_cases],
            "oflow://summary",
            payload={"case_ids": [case.id for case in updated_cases], "changed_fields": list(data.keys())},
            event_type="case_bulk_updated",
        )
        return _serialize(updated_cases)

    @app.get("/cases/search")
    def search_cases(
        response: Response,
        query: str = "",
        status: str | None = None,
        due_before: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        _set_total_count_header(response, current_repository().count_cases(query=query, status=status, due_before=due_before))
        return _serialize(
            current_repository().search_cases(
                query=query,
                status=status,
                due_before=due_before,
                limit=limit,
                offset=offset,
            )
        )

    @app.get("/cases")
    def list_cases(
        response: Response,
        query: str = "",
        status: str | None = None,
        due_before: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        return search_cases(
            response,
            query=query,
            status=status,
            due_before=due_before,
            limit=limit,
            offset=offset,
        )

    @app.get("/cases/{case_id}")
    def get_case_detail(case_id: int) -> dict[str, object] | None:
        detail = current_repository().get_case_detail(case_id)
        if detail is None:
            return None
        serialized_detail = _serialize(detail)
        serialized_detail["documents"] = attach_document_extraction_snapshots(
            getattr(detail, "documents", []),
            lambda case_id: detail,
            serialize_document=_serialize,
        )
        return serialized_detail

    @app.get("/cases/{case_id}/activity")
    def list_case_activity(
        response: Response,
        case_id: int,
        document_id: int | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        _set_total_count_header(
            response,
            current_repository().count_operation_logs(case_id=case_id, document_id=document_id, event_type=event_type),
        )
        return _serialize(
            current_repository().list_operation_logs(
                case_id=case_id,
                document_id=document_id,
                event_type=event_type,
                limit=limit,
                offset=offset,
            )
        )

    @app.get("/operation-logs")
    def list_operation_logs(
        response: Response,
        case_id: int | None = None,
        document_id: int | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        _set_total_count_header(
            response,
            current_repository().count_operation_logs(case_id=case_id, document_id=document_id, event_type=event_type),
        )
        return _serialize(
            current_repository().list_operation_logs(
                case_id=case_id,
                document_id=document_id,
                event_type=event_type,
                limit=limit,
                offset=offset,
            )
        )

    @app.get("/operation-logs/{operation_log_id}")
    def get_operation_log(operation_log_id: int) -> dict[str, object] | None:
        log = current_repository().get_operation_log(operation_log_id)
        return _serialize(log) if log is not None else None

    @app.get("/line-webhooks/report")
    def line_webhook_report(
        limit: int = 20,
        pending_backlog_threshold: int = 5,
    ) -> dict[str, object]:
        return build_line_webhook_report_payload(
            current_repository(),
            limit=limit,
            pending_backlog_threshold=pending_backlog_threshold,
        )

    @app.get("/line-webhooks/alerts")
    def line_webhook_alerts(
        limit: int = 20,
        pending_backlog_threshold: int = 5,
    ) -> dict[str, object]:
        repository = current_repository()
        pending_backlog_count = repository.count_operation_logs(event_type="line_webhook_pending")
        pending_backlog_latest = repository.list_operation_logs(event_type="line_webhook_pending", limit=1)
        needs_attention = pending_backlog_count >= max(1, pending_backlog_threshold)
        alerts: list[dict[str, object]] = []
        if needs_attention:
            latest = pending_backlog_latest[0] if pending_backlog_latest else None
            alerts.append(
                {
                    "alert_type": "pending_backlog",
                    "severity": "warning" if pending_backlog_count < pending_backlog_threshold * 2 else "urgent",
                    "pending_backlog_count": pending_backlog_count,
                    "threshold": max(1, pending_backlog_threshold),
                    "message": (
                        f"Pending LINE webhook backlog is {pending_backlog_count}, "
                        f"which meets or exceeds the threshold of {max(1, pending_backlog_threshold)}."
                    ),
                    "latest_pending": _serialize(latest) if latest else None,
                }
            )
        return {
            "alert_total": len(alerts),
            "needs_attention": needs_attention,
            "pending_backlog_count": pending_backlog_count,
            "alerts": alerts[:limit],
        }

    @app.get("/line-webhooks/alerts.md", response_class=PlainTextResponse)
    def line_webhook_alerts_markdown(
        limit: int = 20,
        pending_backlog_threshold: int = 5,
    ) -> str:
        alerts = line_webhook_alerts(limit=limit, pending_backlog_threshold=pending_backlog_threshold)
        lines = [
            "# O's flow LINE Webhook Alerts",
            f"- alert total: {alerts.get('alert_total', 0)}",
            f"- pending backlog count: {alerts.get('pending_backlog_count', 0)}",
            f"- needs attention: {alerts.get('needs_attention', False)}",
        ]
        if alerts.get("alerts"):
            for item in alerts["alerts"]:
                lines.extend(
                    [
                        "",
                        f"## {item.get('alert_type', 'alert')}",
                        f"- severity: {item.get('severity', 'unknown')}",
                        f"- threshold: {item.get('threshold', 'unknown')}",
                        f"- message: {item.get('message', '')}",
                    ]
                )
                latest_pending = item.get("latest_pending")
                if latest_pending:
                    lines.extend(
                        [
                            f"- latest pending id: {latest_pending.get('id')}",
                            f"- latest pending event type: {latest_pending.get('event_type')}",
                            f"- latest pending created at: {latest_pending.get('created_at')}",
                        ]
                    )
        else:
            lines.extend(["", "## Alerts", "- No alerts."])
        return "\n".join(lines).strip() + "\n"

    @app.get("/line-webhooks/report.md")
    def line_webhook_report_markdown(
        limit: int = 20,
        pending_backlog_threshold: int = 5,
    ) -> PlainTextResponse:
        report = build_line_webhook_report_payload(
            current_repository(),
            limit=limit,
            pending_backlog_threshold=pending_backlog_threshold,
        )
        summary = report["summary"]
        latest_pending = report.get("pending_backlog_latest")
        recent_events = report.get("recent_events", [])
        lines = [
            "# O's flow LINE Webhook Report",
            f"- pending backlog count: {summary.get('pending_backlog_count', 0)}",
            f"- needs attention: {summary.get('needs_attention', False)}",
        ]
        if summary.get("attention_reason"):
            lines.append(f"- attention reason: {summary['attention_reason']}")
        if latest_pending:
            lines.extend(
                [
                    "",
                    "## Latest Pending",
                    f"- log_id: {latest_pending['id']}",
                    f"- event_type: {latest_pending['event_type']}",
                    f"- created_at: {latest_pending['created_at']}",
                ]
            )
        if recent_events:
            lines.extend(["", "## Recent Events"])
            for item in recent_events:
                lines.append(
                    f"- {item.get('created_at')} | {item.get('operation_event_type')} | "
                    f"{item.get('line_event_type')} | {item.get('message_type') or '-'}"
                )
        else:
            lines.extend(["", "## Recent Events", "- No recent events."])
        return PlainTextResponse("\n".join(lines).strip() + "\n")

    @app.get("/line-webhooks/activity")
    def list_line_webhook_activity(
        response: Response,
        limit: int = 50,
        offset: int = 0,
        line_event_type: str | None = None,
        operation_event_type: str | None = None,
        case_code: str | None = None,
        message_type: str | None = None,
    ) -> list[dict[str, object]]:
        logs = _list_line_webhook_logs(current_repository(), limit_per_type=max(limit + offset, 50))
        items = [_serialize_line_webhook_log(log) for log in logs]
        if line_event_type is not None:
            items = [item for item in items if item.get("line_event_type") == line_event_type]
        if operation_event_type is not None:
            items = [item for item in items if item.get("operation_event_type") == operation_event_type]
        if case_code is not None:
            items = [item for item in items if item.get("case_code") == case_code]
        if message_type is not None:
            items = [item for item in items if item.get("message_type") == message_type]
        total = len(items)
        _set_total_count_header(response, total)
        return _serialize(items[offset : offset + limit])

    @app.get("/line-webhooks/pending")
    def list_pending_line_webhooks(
        response: Response,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, object]:
        repository = current_repository()
        pending_logs = repository.list_operation_logs(
            event_type="line_webhook_pending",
            limit=limit,
            offset=offset,
        )
        _set_total_count_header(response, repository.count_operation_logs(event_type="line_webhook_pending"))
        items: list[dict[str, object]] = []
        for log in pending_logs:
            metadata_json = _parse_metadata_json(log.metadata_json)
            items.append(
                {
                    "log_id": log.id,
                    "created_at": log.created_at,
                    "message": log.message,
                    "case_id": log.case_id,
                    "document_id": log.document_id,
                    "case_code": metadata_json.get("case_code"),
                    "reason": metadata_json.get("reason"),
                    "event_type": metadata_json.get("event_type"),
                    "message_type": metadata_json.get("message_type"),
                    "event_json": metadata_json.get("event_json"),
                }
            )
        return {
            "total": repository.count_operation_logs(event_type="line_webhook_pending"),
            "items": items,
        }

    @app.post("/line-webhooks/retry-pending")
    async def retry_pending_line_webhooks(limit: int = 20) -> dict[str, object]:
        pending_logs = current_repository().list_operation_logs(
            event_type="line_webhook_pending",
            limit=limit,
        )
        items: list[dict[str, object]] = []
        ingested_count = 0
        pending_count = 0
        skipped_count = 0
        retried_count = 0

        for log in pending_logs:
            metadata_json = _parse_metadata_json(log.metadata_json)
            event_json = metadata_json.get("event_json")
            if not isinstance(event_json, dict):
                skipped_count += 1
                record_line_webhook_log(
                    event_type="line_webhook_retry_skipped",
                    message="Skipped LINE webhook retry because the original event JSON was unavailable.",
                    case_id=log.case_id,
                    document_id=log.document_id,
                    metadata_json={
                        "retry_of_log_id": log.id,
                        "reason": "event_json_missing",
                    },
                )
                items.append(
                    {
                        "source_log_id": log.id,
                        "event_type": "message",
                        "status": "skipped",
                        "case_code": metadata_json.get("case_code"),
                        "case_id": log.case_id,
                        "document_id": log.document_id,
                        "reason": "event_json_missing",
                    }
                )
                continue

            retried_count += 1
            item = await app.state.line_webhook_client.process_event(event_json)
            event_type = f"line_webhook_retry_{item.status}"
            metadata = {
                "retry_of_log_id": log.id,
                "retry_of_event_type": log.event_type,
                "retry": True,
                "event_json": event_json,
                "reason": item.reason,
                "case_code": item.case_code,
                "message_type": _line_message_type(item),
                "event_summary": _line_event_summary(item),
            }
            if item.status == "ingested":
                ingested_count += 1
                record_line_webhook_log(
                    event_type=event_type,
                    message="Retried LINE webhook event ingested into the ledger.",
                    case_id=item.case_id,
                    document_id=item.document_id,
                    metadata_json=metadata,
                )
                notify_mcp_resource_changed(
                    f"oflow://cases/{item.case_id}",
                    f"oflow://documents/{item.document_id}",
                    "oflow://summary",
                    payload={"case_code": item.case_code, "document_id": item.document_id, "retry_of_log_id": log.id},
                    event_type="ingestion_completed",
                )
            elif item.status == "pending":
                pending_count += 1
                record_line_webhook_log(
                    event_type=event_type,
                    message="Retried LINE webhook event remains pending.",
                    metadata_json=metadata,
                )
            else:
                skipped_count += 1
                record_line_webhook_log(
                    event_type=event_type,
                    message="Retried LINE webhook event was skipped.",
                    case_id=item.case_id,
                    document_id=item.document_id,
                    metadata_json=metadata,
                )
            items.append(
                {
                    "source_log_id": log.id,
                    "event_type": item.event_type,
                    "status": item.status,
                    "case_code": item.case_code,
                    "case_id": item.case_id,
                    "document_id": item.document_id,
                    "reason": item.reason,
                }
            )

        return {
            "processed_count": len(pending_logs),
            "retried_count": retried_count,
            "ingested_count": ingested_count,
            "pending_count": pending_count,
            "skipped_count": skipped_count,
            "items": items,
        }

    @app.get("/notification-deliveries")
    def list_notification_deliveries(
        response: Response,
        deliver_to: str | None = None,
        status: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        _set_total_count_header(
            response,
            current_repository().count_notification_deliveries(
                deliver_to=deliver_to,
                status=status,
                created_after=created_after,
                created_before=created_before,
            ),
        )
        return _serialize(
            current_repository().list_notification_deliveries(
                deliver_to=deliver_to,
                status=status,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                offset=offset,
            )
        )

    @app.get("/notification-deliveries/summary")
    def notification_delivery_summary(
        created_after: str | None = None,
        created_before: str | None = None,
        deliver_to: str | None = None,
        recent_failures_limit: int = 5,
        recent_failures_offset: int = 0,
        failure_rate_threshold: float = 0.25,
        minimum_total_for_attention: int = 5,
    ) -> dict[str, object]:
        return build_notification_delivery_summary(
            current_repository(),
            created_after=created_after,
            created_before=created_before,
            deliver_to=deliver_to,
            recent_failures_limit=recent_failures_limit,
            recent_failures_offset=recent_failures_offset,
            failure_rate_threshold=failure_rate_threshold,
            minimum_total_for_attention=minimum_total_for_attention,
        )

    @app.get("/notification-deliveries/trends")
    def notification_delivery_trends(
        created_after: str | None = None,
        created_before: str | None = None,
        deliver_to: str | None = None,
        granularity: str = "day",
        limit_days: int = 30,
        failure_rate_threshold: float = 0.25,
        minimum_total_for_attention: int = 5,
    ) -> dict[str, object]:
        repository = current_repository()
        if granularity not in {"day", "week", "month"}:
            raise HTTPException(status_code=400, detail="granularity must be one of: day, week, month")
        return {
            "granularity": granularity,
            "trends": _serialize(
                repository.list_notification_delivery_trends(
                    deliver_to=deliver_to,
                    created_after=created_after,
                    created_before=created_before,
                    granularity=granularity,
                    limit_days=limit_days,
                    failure_rate_threshold=failure_rate_threshold,
                    minimum_total_for_attention=minimum_total_for_attention,
                )
            ),
        }

    @app.get("/notification-deliveries/alerts")
    def notification_delivery_alerts(
        created_after: str | None = None,
        created_before: str | None = None,
        deliver_to: str | None = None,
        granularity: str = "day",
        limit_days: int = 30,
        failure_rate_threshold: float = 0.25,
        minimum_total_for_attention: int = 5,
    ) -> dict[str, object]:
        if granularity not in {"day", "week", "month"}:
            raise HTTPException(status_code=400, detail="granularity must be one of: day, week, month")
        return build_notification_delivery_alerts(
            current_repository(),
            created_after=created_after,
            created_before=created_before,
            deliver_to=deliver_to,
            granularity=granularity,
            limit_days=limit_days,
            failure_rate_threshold=failure_rate_threshold,
            minimum_total_for_attention=minimum_total_for_attention,
        )

    @app.get("/notification-deliveries/report")
    def notification_delivery_report(
        created_after: str | None = None,
        created_before: str | None = None,
        deliver_to: str | None = None,
        granularity: str = "day",
        recent_failures_limit: int = 5,
        recent_failures_offset: int = 0,
        limit_days: int = 30,
        failure_rate_threshold: float = 0.25,
        minimum_total_for_attention: int = 5,
    ) -> dict[str, object]:
        if granularity not in {"day", "week", "month"}:
            raise HTTPException(status_code=400, detail="granularity must be one of: day, week, month")
        return build_notification_delivery_report(
            current_repository(),
            created_after=created_after,
            created_before=created_before,
            deliver_to=deliver_to,
            granularity=granularity,
            recent_failures_limit=recent_failures_limit,
            recent_failures_offset=recent_failures_offset,
            limit_days=limit_days,
            failure_rate_threshold=failure_rate_threshold,
            minimum_total_for_attention=minimum_total_for_attention,
        )

    @app.get("/notification-deliveries/report.md", response_class=PlainTextResponse)
    def notification_delivery_report_markdown(
        created_after: str | None = None,
        created_before: str | None = None,
        deliver_to: str | None = None,
        granularity: str = "day",
        recent_failures_limit: int = 5,
        recent_failures_offset: int = 0,
        limit_days: int = 30,
        failure_rate_threshold: float = 0.25,
        minimum_total_for_attention: int = 5,
    ) -> str:
        if granularity not in {"day", "week", "month"}:
            raise HTTPException(status_code=400, detail="granularity must be one of: day, week, month")
        report = build_notification_delivery_report(
            current_repository(),
            created_after=created_after,
            created_before=created_before,
            deliver_to=deliver_to,
            granularity=granularity,
            recent_failures_limit=recent_failures_limit,
            recent_failures_offset=recent_failures_offset,
            limit_days=limit_days,
            failure_rate_threshold=failure_rate_threshold,
            minimum_total_for_attention=minimum_total_for_attention,
        )
        return render_notification_delivery_report_markdown(report)

    @app.get("/notification-deliveries/{notification_delivery_id}")
    def get_notification_delivery(notification_delivery_id: int) -> dict[str, object] | None:
        delivery = current_repository().get_notification_delivery(notification_delivery_id)
        return _serialize(delivery) if delivery is not None else None

    @app.patch("/cases/{case_id}")
    def update_case(case_id: int, payload: CaseUpdateRequest) -> dict[str, object]:
        from fastapi import HTTPException

        data = payload.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(status_code=400, detail="At least one field must be provided.")
        try:
            case = app.state.repository.update_case(case_id, **data)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        app.state.repository.record_operation_log(
            event_type="case_updated",
            entity_type="case",
            entity_id=case.id,
            case_id=case.id,
            message=f"Case updated: {case.case_code}",
            metadata_json={"changed_fields": list(data.keys())},
        )
        notify_mcp_resource_changed(
            f"oflow://cases/{case.id}",
            "oflow://summary",
            payload={"case_id": case.id, "case_code": case.case_code, "changed_fields": list(data.keys())},
            event_type="case_updated",
        )
        return _serialize(case)

    @app.post("/cases")
    def create_case(payload: CaseCreateRequest) -> dict[str, object]:
        existing_case_id = None
        for item in current_repository().search_cases(query=payload.case_code, limit=20):
            if item.case_code == payload.case_code:
                existing_case_id = item.id
                break
        case = app.state.repository.upsert_case(
            case_code=payload.case_code,
            title=payload.title,
            client_name=payload.client_name,
            status=payload.status,
            due_date=payload.due_date,
            invoice_status=payload.invoice_status,
            output_status=payload.output_status,
            last_processed_at=payload.last_processed_at,
        )
        event_type = "case_updated" if existing_case_id is not None else "case_created"
        app.state.repository.record_operation_log(
            event_type=event_type,
            entity_type="case",
            entity_id=case.id,
            case_id=case.id,
            message=f"Case {event_type.replace('_', ' ')}: {case.case_code}",
            metadata_json={"case_code": case.case_code, "existing_case_id": existing_case_id},
        )
        notify_mcp_resource_changed(
            f"oflow://cases/{case.id}",
            "oflow://summary",
            payload={"case_id": case.id, "case_code": case.case_code, "existing_case_id": existing_case_id},
            event_type=event_type,
        )
        return _serialize(case)

    @app.get("/documents")
    def list_documents(
        response: Response,
        case_id: int | None = None,
        source_type: str | None = None,
        is_deleted: bool | None = None,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        repository = current_repository()
        documents = repository.list_documents(
            case_id=case_id,
            source_type=source_type,
            is_deleted=is_deleted,
            query=query,
            limit=limit,
            offset=offset,
        )
        _set_total_count_header(
            response,
            repository.count_documents(
                case_id=case_id,
                source_type=source_type,
                is_deleted=is_deleted,
                query=query,
            ),
        )
        return attach_document_extraction_snapshots(
            documents,
            repository.get_case_detail,
            serialize_document=_serialize,
        )

    @app.post("/documents/bulk-reprocess")
    def bulk_reprocess_documents(payload: DocumentBulkReprocessRequest) -> dict[str, object]:
        from fastapi import HTTPException

        if not payload.document_ids:
            raise HTTPException(status_code=400, detail="document_ids must not be empty.")
        try:
            result = app.state.document_service.reprocess_documents_by_ids(payload.document_ids)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        notify_mcp_resource_changed(
            *[f"oflow://cases/{item.case_id}" for item in result.items if item.case_id is not None],
            "oflow://summary",
            payload={"document_ids": payload.document_ids, "successful_documents": result.successful_documents},
            event_type="document_batch_reprocessed",
        )
        return _serialize(result)

    @app.get("/documents/{document_id}")
    def get_document(document_id: int) -> dict[str, object] | None:
        document = current_repository().get_document(document_id)
        if document is None:
            return None
        payload = _serialize(document)
        extraction = build_document_extraction_snapshot_for_document(current_repository(), document_id)
        if extraction is not None:
            payload = dict(payload)
            payload["extraction"] = extraction
        return payload

    @app.get("/documents/{document_id}/activity")
    def list_document_activity(
        response: Response,
        document_id: int,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        _set_total_count_header(
            response,
            current_repository().count_operation_logs(document_id=document_id, event_type=event_type),
        )
        return _serialize(
            current_repository().list_operation_logs(
                document_id=document_id,
                event_type=event_type,
                limit=limit,
                offset=offset,
            )
        )

    @app.post("/documents/{document_id}/reassign")
    def reassign_document(document_id: int, payload: DocumentReassignRequest) -> dict[str, object]:
        try:
            result = app.state.document_service.reassign_document(document_id, payload.target_case_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        notify_mcp_resource_changed(
            f"oflow://documents/{result.document_id}",
            f"oflow://cases/{result.previous_case_id}",
            f"oflow://cases/{result.new_case_id}",
            "oflow://summary",
            payload={
                "document_id": result.document_id,
                "previous_case_id": result.previous_case_id,
                "new_case_id": result.new_case_id,
            },
            event_type="document_reassigned",
        )
        return _serialize(result)

    @app.get("/tasks/due")
    def list_due_tasks(
        response: Response,
        until_date: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        _set_total_count_header(response, current_repository().count_due_tasks(until_date=until_date, status=status))
        return _serialize(
            current_repository().list_due_tasks(until_date=until_date, status=status, limit=limit, offset=offset)
        )

    @app.get("/invoices")
    def list_invoices(
        response: Response,
        invoice_status: str | None = None,
        due_before: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        _set_total_count_header(
            response,
            current_repository().count_invoices(invoice_status=invoice_status, due_before=due_before),
        )
        return _serialize(
            current_repository().list_invoices(
                invoice_status=invoice_status,
                due_before=due_before,
                limit=limit,
                offset=offset,
            )
        )

    @app.get("/rag/search")
    def search_rag(
        response: Response,
        query: str,
        case_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        _set_total_count_header(response, current_repository().count_rag(query=query, case_id=case_id))
        return _serialize(current_repository().search_rag(query=query, case_id=case_id, limit=limit, offset=offset))

    @app.get("/processing-jobs")
    def list_processing_jobs(
        response: Response,
        case_id: int | None = None,
        document_id: int | None = None,
        job_type: str | None = None,
        job_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        _set_total_count_header(
            response,
            current_repository().count_processing_jobs(
                case_id=case_id,
                document_id=document_id,
                job_type=job_type,
                job_status=job_status,
            ),
        )
        return _serialize(
            current_repository().list_processing_jobs(
                case_id=case_id,
                document_id=document_id,
                job_type=job_type,
                job_status=job_status,
                limit=limit,
                offset=offset,
            )
        )

    @app.get("/processing-jobs/{job_id}")
    def get_processing_job(job_id: int) -> dict[str, object] | None:
        job = current_repository().get_processing_job(job_id)
        return _serialize(job) if job is not None else None

    @app.delete("/documents/{document_id}")
    def delete_document(document_id: int) -> dict[str, object]:
        from fastapi import HTTPException

        try:
            result = app.state.document_service.delete_document(document_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        notify_mcp_resource_changed(
            f"oflow://documents/{result.document_id}",
            f"oflow://cases/{result.case_id}",
            "oflow://summary",
            payload={"document_id": result.document_id, "case_id": result.case_id},
            event_type="document_deleted",
        )
        return _serialize(result)

    @app.post("/documents/{document_id}/reprocess")
    def reprocess_document(document_id: int) -> dict[str, object]:
        from fastapi import HTTPException

        try:
            result = app.state.document_service.reprocess_document(document_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        notify_mcp_resource_changed(
            f"oflow://documents/{result.document_id}",
            f"oflow://cases/{result.case_id}",
            "oflow://summary",
            payload={
                "document_id": result.document_id,
                "case_id": result.case_id,
                "extracted_text_length": result.extracted_text_length,
            },
            event_type="document_reprocessed",
        )
        return _serialize(result)

    @app.post("/cases/{case_id}/reprocess-documents")
    def reprocess_documents_for_case(case_id: int, limit: int = 50) -> dict[str, object]:
        from fastapi import HTTPException

        try:
            result = app.state.document_service.reprocess_documents_for_case(case_id, limit=limit)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        notify_mcp_resource_changed(
            f"oflow://cases/{case_id}",
            "oflow://summary",
            *[
                f"oflow://documents/{item.document_id}"
                for item in result.items
                if item.status == "completed"
            ],
            payload={
                "case_id": case_id,
                "successful_documents": result.successful_documents,
                "failed_documents": result.failed_documents,
            },
            event_type="case_reprocessed",
        )
        return _serialize(result)

    @app.post("/ingestions", status_code=200)
    def create_ingestion(payload: IngestionCreateRequest) -> dict[str, object]:
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base64 content: {exc}") from exc

        result = _ingest_payload(
            app.state.ingestion_service,
            case_code=payload.case_code,
            title=payload.title,
            filename=payload.filename,
            content=content,
            mime_type=payload.mime_type,
            source_type=payload.source_type,
            source_path=payload.source_path,
            client_name=payload.client_name,
            due_date=payload.due_date,
            invoice_status=payload.invoice_status,
            output_status=payload.output_status,
            extracted_text=payload.extracted_text,
            structured_json=payload.structured_json,
            rag_chunks=payload.rag_chunks,
            output_html=payload.output_html,
        )
        notify_mcp_resource_changed(
            f"oflow://cases/{result['case_id']}",
            f"oflow://documents/{result['document_id']}",
            "oflow://summary",
            payload={"case_code": payload.case_code, "document_id": result["document_id"]},
            event_type="ingestion_completed",
        )
        return result

    @app.post("/ingestions/upload", status_code=200)
    async def create_ingestion_upload(
        file: UploadFile = File(...),
        case_code: str = Form(...),
        title: str = Form(...),
        mime_type: str | None = Form(None),
        source_type: str = Form("api"),
        source_path: str | None = Form(None),
        client_name: str | None = Form(None),
        due_date: str | None = Form(None),
        invoice_status: str = Form("unbilled"),
        output_status: str = Form("pending"),
        extracted_text: str | None = Form(None),
        structured_json: str | None = Form(None),
        rag_chunks: str | None = Form(None),
        output_html: str | None = Form(None),
    ) -> dict[str, object]:
        raw_content = await file.read()
        result = _ingest_payload(
            app.state.ingestion_service,
            case_code=case_code,
            title=title,
            filename=file.filename or "upload.bin",
            content=raw_content,
            mime_type=mime_type or file.content_type,
            source_type=source_type,
            source_path=source_path,
            client_name=client_name,
            due_date=due_date,
            invoice_status=invoice_status,
            output_status=output_status,
            extracted_text=extracted_text,
            structured_json=_parse_optional_json(structured_json, field_name="structured_json"),
            rag_chunks=_parse_optional_json(rag_chunks, field_name="rag_chunks", default=[]),
            output_html=output_html,
        )
        notify_mcp_resource_changed(
            f"oflow://cases/{result['case_id']}",
            f"oflow://documents/{result['document_id']}",
            "oflow://summary",
            payload={"case_code": case_code, "document_id": result["document_id"]},
            event_type="ingestion_completed",
        )
        return result

    @app.post("/chat-ingestions", status_code=200)
    def create_chat_ingestion(payload: ChatIngestionCreateRequest) -> dict[str, object]:
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base64 content: {exc}") from exc
        result = _ingest_chat_payload(
            app,
            platform=payload.platform,
            case_code=payload.case_code,
            title=payload.title,
            filename=payload.filename,
            content=content,
            mime_type=payload.mime_type,
            source_path=payload.source_path,
            message_id=payload.message_id,
            channel_id=payload.channel_id,
            author_name=payload.author_name,
            message_text=payload.message_text,
            client_name=payload.client_name,
            due_date=payload.due_date,
            invoice_status=payload.invoice_status,
            output_status=payload.output_status,
            extracted_text=payload.extracted_text,
            structured_json=payload.structured_json,
            rag_chunks=payload.rag_chunks,
            output_html=payload.output_html,
        )
        notify_mcp_resource_changed(
            f"oflow://cases/{result['case_id']}",
            f"oflow://documents/{result['document_id']}",
            "oflow://summary",
            payload={"case_code": payload.case_code, "document_id": result["document_id"]},
            event_type="ingestion_completed",
        )
        return result

    @app.post("/connectors/discord/chat-ingestions", status_code=200)
    def create_discord_chat_ingestion(payload: DiscordChatIngestionRequest) -> dict[str, object]:
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base64 content: {exc}") from exc

        source_path = build_discord_source_path(
            guild_id=payload.guild_id,
            channel_id=payload.channel_id,
            message_id=payload.message_id,
            source_path=payload.source_path,
        )
        result = _ingest_chat_payload(
            app,
            platform="discord",
            case_code=payload.case_code,
            title=payload.title,
            filename=payload.filename,
            content=content,
            mime_type=payload.mime_type,
            source_path=source_path,
            message_id=payload.message_id,
            channel_id=payload.channel_id,
            author_name=payload.author_name,
            message_text=payload.message_text,
            client_name=payload.client_name,
            due_date=payload.due_date,
            invoice_status=payload.invoice_status,
            output_status=payload.output_status,
            extracted_text=payload.extracted_text,
            structured_json=payload.structured_json,
            rag_chunks=payload.rag_chunks,
            output_html=payload.output_html,
            extra_metadata={"guild_id": payload.guild_id},
        )
        notify_mcp_resource_changed(
            f"oflow://cases/{result['case_id']}",
            f"oflow://documents/{result['document_id']}",
            "oflow://summary",
            payload={"case_code": payload.case_code, "document_id": result["document_id"]},
            event_type="ingestion_completed",
        )
        return result

    @app.post("/connectors/line/chat-ingestions", status_code=200)
    def create_line_chat_ingestion(payload: LineChatIngestionRequest) -> dict[str, object]:
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base64 content: {exc}") from exc

        source_path = build_line_source_path(
            room_id=payload.room_id,
            group_id=payload.group_id,
            user_id=payload.user_id,
            message_id=payload.message_id,
            source_path=payload.source_path,
        )
        result = _ingest_chat_payload(
            app,
            platform="line",
            case_code=payload.case_code,
            title=payload.title,
            filename=payload.filename,
            content=content,
            mime_type=payload.mime_type,
            source_path=source_path,
            message_id=payload.message_id,
            channel_id=payload.room_id or payload.group_id,
            author_name=payload.author_name,
            message_text=payload.message_text,
            client_name=payload.client_name,
            due_date=payload.due_date,
            invoice_status=payload.invoice_status,
            output_status=payload.output_status,
            extracted_text=payload.extracted_text,
            structured_json=payload.structured_json,
            rag_chunks=payload.rag_chunks,
            output_html=payload.output_html,
            extra_metadata={
                "room_id": payload.room_id,
                "group_id": payload.group_id,
                "user_id": payload.user_id,
            },
        )
        notify_mcp_resource_changed(
            f"oflow://cases/{result['case_id']}",
            f"oflow://documents/{result['document_id']}",
            "oflow://summary",
            payload={"case_code": payload.case_code, "document_id": result["document_id"]},
            event_type="ingestion_completed",
        )
        return result

    @app.post("/connectors/line/webhook", status_code=200)
    async def line_webhook(request: Request) -> dict[str, object]:
        raw_body = await request.body()
        signature = request.headers.get("X-Line-Signature")
        try:
            webhook_result = await app.state.line_webhook_client.process_webhook(raw_body, signature)
        except ValueError as exc:
            record_line_webhook_log(
                event_type="line_webhook_signature_invalid",
                message="Rejected LINE webhook with invalid signature.",
                metadata_json={"error": str(exc)},
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        for item in webhook_result.items:
            if item.status != "ingested" or item.case_id is None or item.document_id is None:
                record_line_webhook_log(
                    event_type=f"line_webhook_{item.status}",
                    message=f"LINE webhook event {item.status}: {item.reason or item.status}.",
                    metadata_json={
                        "reason": item.reason,
                        "case_code": item.case_code,
                        "event_type": item.event_type,
                        "message_type": _line_message_type(item),
                        "event_summary": _line_event_summary(item),
                        "event_json": item.event_json,
                        **_line_event_extra_metadata(item),
                    },
                )
                continue
            record_line_webhook_log(
                event_type="line_webhook_ingested",
                message="LINE webhook event ingested into the ledger.",
                case_id=item.case_id,
                document_id=item.document_id,
                metadata_json={
                    "case_code": item.case_code,
                    "event_type": item.event_type,
                    "message_type": _line_message_type(item),
                    "event_summary": _line_event_summary(item),
                    "status": item.status,
                    "event_json": item.event_json,
                    **_line_event_extra_metadata(item),
                },
            )
            notify_mcp_resource_changed(
                f"oflow://cases/{item.case_id}",
                f"oflow://documents/{item.document_id}",
                "oflow://summary",
                payload={"case_code": item.case_code, "document_id": item.document_id},
                event_type="ingestion_completed",
            )
        return {
            "processed_count": webhook_result.processed_count,
            "ingested_count": webhook_result.ingested_count,
            "pending_count": webhook_result.pending_count,
            "skipped_count": webhook_result.skipped_count,
            "items": [
                {
                    "event_type": item.event_type,
                    "status": item.status,
                    "case_code": item.case_code,
                    "case_id": item.case_id,
                    "document_id": item.document_id,
                    "reason": item.reason,
                }
                for item in webhook_result.items
            ],
        }

    return app


app = create_app()
