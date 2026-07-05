from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from app.repositories.base import Repository


def build_document_extraction_snapshot(
    case_detail,
    document_id: int,
    *,
    case_id: int | None = None,
) -> dict[str, object] | None:  # noqa: ANN001
    if case_detail is None:
        return None
    rag_entry = next((entry for entry in case_detail.rag_entries if entry.document_id == document_id), None)
    if rag_entry is None:
        return {
            "document_id": document_id,
            "case_id": case_id,
            "available": False,
            "reason": "no_rag_entry",
        }
    try:
        metadata_json = json.loads(rag_entry.metadata_json)
    except json.JSONDecodeError:
        metadata_json = {}
    return {
        "document_id": document_id,
        "case_id": case_id,
        "available": True,
        "extraction_source": metadata_json.get("extraction_source"),
        "extraction_engine": metadata_json.get("extraction_engine"),
        "extraction_mode": metadata_json.get("extraction_mode"),
        "reprocess": bool(metadata_json.get("reprocess")),
        "title": rag_entry.title,
        "content_hash": rag_entry.content_hash,
        "metadata_json": metadata_json,
    }


def build_document_extraction_snapshot_for_document(
    repository: Repository,
    document_id: int,
) -> dict[str, object] | None:
    document = repository.get_document(document_id)
    if document is None:
        return None
    case_detail = repository.get_case_detail(document.case_id)
    return build_document_extraction_snapshot(case_detail, document.id, case_id=document.case_id)


def attach_document_extraction_snapshots(
    documents: Iterable[Any],
    case_detail_for_case_id: Callable[[int], Any],
    *,
    serialize_document: Callable[[Any], dict[str, object]],
) -> list[dict[str, object]]:
    case_detail_cache: dict[int, object | None] = {}
    serialized_documents: list[dict[str, object]] = []
    for document in documents:
        item = serialize_document(document)
        case_detail = case_detail_cache.get(document.case_id)
        if document.case_id not in case_detail_cache:
            case_detail = case_detail_for_case_id(document.case_id)
            case_detail_cache[document.case_id] = case_detail
        extraction = build_document_extraction_snapshot(case_detail, document.id, case_id=document.case_id)
        if extraction is not None:
            item = dict(item)
            item["extraction"] = extraction
        serialized_documents.append(item)
    return serialized_documents
