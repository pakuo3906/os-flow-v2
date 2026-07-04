from __future__ import annotations

import re
from typing import Any

from app.domain.models import IngestionRequest
from app.services.ingestion import IngestionService


_CASE_CODE_PATTERN = re.compile(r"\bCASE-[A-Z0-9-]+\b", re.IGNORECASE)


def build_chat_metadata_json(
    *,
    platform: str,
    source_path: str | None,
    message_id: str | None,
    channel_id: str | None,
    author_name: str | None,
    message_text: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "chat",
        "platform": platform,
    }
    if source_path is not None:
        metadata["source_path"] = source_path
    if message_id is not None:
        metadata["message_id"] = message_id
    if channel_id is not None:
        metadata["channel_id"] = channel_id
    if author_name is not None:
        metadata["author_name"] = author_name
    if message_text is not None:
        metadata["message_text"] = message_text
    if extra:
        for key, value in extra.items():
            if value is not None:
                metadata[key] = value
    return metadata


def build_discord_source_path(
    *,
    guild_id: str | None = None,
    channel_id: str | None = None,
    message_id: str | None = None,
    source_path: str | None = None,
) -> str | None:
    if source_path is not None and source_path.strip():
        return source_path
    parts = ["discord"]
    if guild_id is not None:
        parts.extend(["guild", guild_id])
    if channel_id is not None:
        parts.extend(["channel", channel_id])
    if message_id is not None:
        parts.extend(["message", message_id])
    return "/".join(parts) if len(parts) > 1 else None


def build_line_source_path(
    *,
    room_id: str | None = None,
    group_id: str | None = None,
    user_id: str | None = None,
    message_id: str | None = None,
    source_path: str | None = None,
) -> str | None:
    if source_path is not None and source_path.strip():
        return source_path
    parts = ["line"]
    if group_id is not None:
        parts.extend(["group", group_id])
    elif room_id is not None:
        parts.extend(["room", room_id])
    if user_id is not None:
        parts.extend(["user", user_id])
    if message_id is not None:
        parts.extend(["message", message_id])
    return "/".join(parts) if len(parts) > 1 else None


def extract_case_code(text: str) -> str | None:
    match = _CASE_CODE_PATTERN.search(text)
    return match.group(0).upper() if match else None


def ingest_chat_payload(
    ingestion_service: IngestionService,
    *,
    platform: str,
    case_code: str,
    title: str,
    filename: str,
    content: bytes,
    mime_type: str | None,
    source_path: str | None,
    client_name: str | None,
    due_date: str | None,
    invoice_status: str,
    output_status: str,
    extracted_text: str | None,
    structured_json: dict[str, Any] | None,
    rag_chunks: list[dict[str, Any]],
    output_html: str | None,
) -> object:
    return ingestion_service.ingest(
        IngestionRequest(
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
            structured_json=structured_json,
            rag_chunks=rag_chunks,
            output_html=output_html,
        )
    )
