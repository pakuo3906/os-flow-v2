from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from app.repositories.base import Repository

_MAX_LIMIT = 100


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


def _normalize_limit(limit: int) -> int:
    return max(1, min(limit, _MAX_LIMIT))


def _normalize_offset(offset: int) -> int:
    return max(0, offset)


def _build_page(
    *,
    items: list[Any],
    total: int,
    limit: int,
    offset: int,
) -> dict[str, object]:
    return {
        "items": _serialize(items),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def search_cases_tool(
    repository: Repository,
    *,
    query: str = "",
    status: str | None = None,
    due_before: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, object]:
    limit = _normalize_limit(limit)
    offset = _normalize_offset(offset)
    return _build_page(
        items=repository.search_cases(query=query, status=status, due_before=due_before, limit=limit, offset=offset),
        total=repository.count_cases(query=query, status=status, due_before=due_before),
        limit=limit,
        offset=offset,
    )


def get_case_detail_tool(repository: Repository, case_id: int) -> dict[str, object] | None:
    detail = repository.get_case_detail(case_id)
    return _serialize(detail) if detail is not None else None


def list_documents_tool(
    repository: Repository,
    *,
    case_id: int | None = None,
    source_type: str | None = None,
    is_deleted: bool | None = None,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    limit = _normalize_limit(limit)
    offset = _normalize_offset(offset)
    return _build_page(
        items=repository.list_documents(
            case_id=case_id,
            source_type=source_type,
            is_deleted=is_deleted,
            query=query,
            limit=limit,
            offset=offset,
        ),
        total=repository.count_documents(case_id=case_id, source_type=source_type, is_deleted=is_deleted, query=query),
        limit=limit,
        offset=offset,
    )


def list_due_tasks_tool(
    repository: Repository,
    *,
    until_date: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    limit = _normalize_limit(limit)
    offset = _normalize_offset(offset)
    return _build_page(
        items=repository.list_due_tasks(until_date=until_date, status=status, limit=limit, offset=offset),
        total=repository.count_due_tasks(until_date=until_date, status=status),
        limit=limit,
        offset=offset,
    )


def list_invoices_tool(
    repository: Repository,
    *,
    invoice_status: str | None = None,
    due_before: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    limit = _normalize_limit(limit)
    offset = _normalize_offset(offset)
    return _build_page(
        items=repository.list_invoices(
            invoice_status=invoice_status,
            due_before=due_before,
            limit=limit,
            offset=offset,
        ),
        total=repository.count_invoices(invoice_status=invoice_status, due_before=due_before),
        limit=limit,
        offset=offset,
    )


def search_rag_tool(
    repository: Repository,
    *,
    query: str,
    case_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, object]:
    limit = _normalize_limit(limit)
    offset = _normalize_offset(offset)
    return _build_page(
        items=repository.search_rag(query=query, case_id=case_id, limit=limit, offset=offset),
        total=repository.count_rag(query=query, case_id=case_id),
        limit=limit,
        offset=offset,
    )
