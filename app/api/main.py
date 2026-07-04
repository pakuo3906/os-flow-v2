from __future__ import annotations

import base64
import json
from datetime import date
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import PlainTextResponse
from fastapi import Request
from pydantic import BaseModel, Field, field_validator

from app.config import load_settings
from app.domain.models import IngestionRequest
from app.services.chat_connectors import (
    build_chat_metadata_json,
    build_discord_source_path,
    build_line_source_path,
)
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
                "insforge": {
                    "base_url_configured": bool((settings.insforge_base_url or "").strip()),
                    "api_key_configured": bool((settings.insforge_api_key or "").strip()),
                    "database_url_configured": bool((settings.insforge_database_url or "").strip()),
                    "project_id_configured": bool((settings.insforge_project_id or "").strip()),
                    "storage_bucket_configured": bool((settings.insforge_storage_bucket or "").strip()),
                    "storage_namespace_configured": bool((settings.insforge_storage_namespace or "").strip()),
                    "auth_jwks_url_configured": bool((settings.insforge_auth_jwks_url or "").strip()),
                    "mcp_base_url_configured": bool((settings.insforge_mcp_base_url or "").strip()),
                },
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

    @app.get("/admin/recent")
    def admin_recent(limit: int = 10) -> dict[str, object]:
        repository = current_repository()
        normalized_limit = max(1, min(limit, 50))
        return {
            "limit": normalized_limit,
            "cases": _serialize(repository.search_cases(limit=normalized_limit)),
            "documents": _serialize(repository.list_documents(limit=normalized_limit)),
            "operation_logs": _serialize(repository.list_operation_logs(limit=normalized_limit)),
            "notification_deliveries": _serialize(repository.list_notification_deliveries(limit=normalized_limit)),
        }

    @app.get("/admin/activity")
    def admin_activity(limit: int = 20) -> dict[str, object]:
        repository = current_repository()
        normalized_limit = max(1, min(limit, 50))
        return {
            "limit": normalized_limit,
            "items": _build_admin_activity_items(repository, limit=normalized_limit),
        }

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

    @app.get("/cases/{case_id}")
    def get_case_detail(case_id: int) -> dict[str, object] | None:
        detail = current_repository().get_case_detail(case_id)
        return _serialize(detail) if detail is not None else None

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
        _set_total_count_header(
            response,
            current_repository().count_documents(
                case_id=case_id,
                source_type=source_type,
                is_deleted=is_deleted,
                query=query,
            ),
        )
        return _serialize(
            current_repository().list_documents(
                case_id=case_id,
                source_type=source_type,
                is_deleted=is_deleted,
                query=query,
                limit=limit,
                offset=offset,
            )
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
        return _serialize(document) if document is not None else None

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
