from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.config import Settings
from app.domain.models import IngestionRequest
from app.repositories.base import Repository
from app.services.ingestion import IngestionService


def _demo_case_specs(settings: Settings) -> list[dict[str, Any]]:
    today = date.today()
    inbox_case_code = (settings.line_inbox_case_code or "").strip() or "LINE-INBOX"
    return [
        {
            "case_code": inbox_case_code,
            "title": "LINE現場整理 / 受付箱",
            "client_name": "LINE現場整理デモ",
            "status": "in_progress",
            "due_date": (today + timedelta(days=1)).isoformat(),
            "invoice_status": "unbilled",
            "output_status": "pending",
            "documents": [
                {
                    "filename": "line-inbox-note.txt",
                    "mime_type": "text/plain",
                    "source_path": f"line/demo/{inbox_case_code}/message-001",
                    "content": (
                        f"[{inbox_case_code}] 受付箱のデモです。\n"
                        "LINEから届いたメッセージを案件に振り分ける前提で、\n"
                        "依頼内容・締切・未提出の有無を管理画面で確認できる最小サンプルです。\n"
                        "実データを流し込む前に、ケース・書類・請求・提出漏れの見え方を確認できます。\n"
                    ),
                },
            ],
        },
        {
            "case_code": "LINE-DEMO-001",
            "title": "LINE現場整理 / 取引先A",
            "client_name": "サンプル工務店",
            "status": "in_progress",
            "due_date": (today + timedelta(days=2)).isoformat(),
            "invoice_status": "pending",
            "output_status": "pending",
            "documents": [
                {
                    "filename": "site-checklist.md",
                    "mime_type": "text/markdown",
                    "source_path": "line/demo/LINE-DEMO-001/checklist",
                    "content": (
                        "# 取引先A チェックリスト\n"
                        "- 現場メモの確認\n"
                        "- 写真の整理\n"
                        "- 請求前確認\n"
                        "- 提出状況の確認\n"
                    ),
                },
            ],
        },
        {
            "case_code": "LINE-DEMO-002",
            "title": "LINE現場整理 / 取引先B",
            "client_name": "みなと設計事務所",
            "status": "in_progress",
            "due_date": (today - timedelta(days=1)).isoformat(),
            "invoice_status": "unbilled",
            "output_status": "pending",
            "documents": [
                {
                    "filename": "invoice-followup.txt",
                    "mime_type": "text/plain",
                    "source_path": "line/demo/LINE-DEMO-002/followup",
                    "content": (
                        "取引先Bは請求が未了です。\n"
                        "締切を過ぎているため、管理画面上では未提出・未請求の両方が見える想定です。\n"
                    ),
                },
            ],
        },
        {
            "case_code": "LINE-DEMO-003",
            "title": "LINE現場整理 / 完了サンプル",
            "client_name": "東京リフォーム",
            "status": "completed",
            "due_date": (today + timedelta(days=5)).isoformat(),
            "invoice_status": "billed",
            "output_status": "completed",
            "documents": [
                {
                    "filename": "handoff-summary.txt",
                    "mime_type": "text/plain",
                    "source_path": "line/demo/LINE-DEMO-003/handoff",
                    "content": (
                        "取引先Cは完了済みのサンプルです。\n"
                        "提出・請求が完了しているケースと、未完了のケースを並べて比較できます。\n"
                    ),
                    "output_html": "<html><body><h1>取引先C 完了サンプル</h1><p>提出・請求が完了しているデモデータです。</p></body></html>",
                },
            ],
        },
    ]


def _find_case(repository: Repository, case_code: str):
    for case in repository.search_cases(query=case_code, limit=20):
        if case.case_code == case_code:
            return case
    return None


def build_missing_submissions_payload(
    repository: Repository,
    *,
    query: str = "",
    status: str | None = None,
    due_before: str | None = None,
    invoice_status: str | None = None,
    output_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    threshold = due_before or date.today().isoformat()
    cases = repository.search_cases(
        query=query,
        status=status,
        due_before=threshold,
        invoice_status=invoice_status,
        output_status=output_status,
        limit=1000,
        offset=0,
    )
    items: list[dict[str, object]] = []
    for case in cases:
        reasons: list[str] = []
        if case.output_status != "completed":
            reasons.append("output pending")
        if case.invoice_status != "billed":
            reasons.append("invoice pending")
        if not reasons:
            continue
        items.append(
            {
                "id": case.id,
                "case_id": case.id,
                "case_code": case.case_code,
                "title": case.title,
                "client_name": case.client_name,
                "status": case.status,
                "due_date": case.due_date,
                "invoice_status": case.invoice_status,
                "output_status": case.output_status,
                "missing_submission_reason": " / ".join(reasons),
                "created_at": case.created_at,
                "updated_at": case.updated_at,
                "last_processed_at": case.last_processed_at,
            }
        )
    total = len(items)
    return {
        "title": "Missing Submissions",
        "collection_path": "/admin/missing-submissions",
        "detail_path": "/cases/{case_id}",
        "total": total,
        "items": items[offset : offset + limit],
        "filters": {
            "query": query,
            "status": status,
            "due_before": threshold,
            "invoice_status": invoice_status,
            "output_status": output_status,
        },
    }


def build_demo_pack_guide(repository: Repository, settings: Settings) -> dict[str, object]:
    case_specs = _demo_case_specs(settings)
    current_cases = []
    seeded_case_codes: list[str] = []
    for spec in case_specs:
        case = _find_case(repository, spec["case_code"])
        document_count = repository.count_documents(case_id=case.id) if case is not None else 0
        present = case is not None and document_count > 0
        if present:
            seeded_case_codes.append(spec["case_code"])
        current_cases.append(
            {
                "case_code": spec["case_code"],
                "title": spec["title"],
                "due_date": spec["due_date"],
                "invoice_status": spec["invoice_status"],
                "output_status": spec["output_status"],
                "present": present,
                "case_id": case.id if case is not None else None,
                "document_count": document_count,
            }
        )

    missing_submissions = build_missing_submissions_payload(repository, due_before=date.today().isoformat())
    invoice_count = repository.count_invoices()
    demo_document_count = sum(case["document_count"] for case in current_cases)
    demo_case_count = sum(1 for case in current_cases if case["present"])

    return {
        "title": "LINE現場整理パック",
        "scenario": "LINE経由の依頼を、案件・書類・請求・提出漏れの4観点で見渡すための最小デモです。",
        "seed_command": ".\\.venv\\Scripts\\python.exe -m app.cli.demo_pack seed",
        "seed_script": "scripts/seed_demo_pack.ps1",
        "seeded": demo_case_count == len(case_specs) and demo_document_count >= len(case_specs),
        "current_counts": {
            "cases": demo_case_count,
            "documents": demo_document_count,
            "invoices": invoice_count,
            "missing_submissions": missing_submissions["total"],
        },
        "steps": [
            {
                "step": 1,
                "title": "デモデータを投入する",
                "kind": "seed",
                "command": ".\\.venv\\Scripts\\python.exe -m app.cli.demo_pack seed",
                "script": "scripts/seed_demo_pack.ps1",
            },
            {
                "step": 2,
                "title": "/admin/ui を開く",
                "kind": "review",
                "path": "/admin/ui",
            },
            {
                "step": 3,
                "title": "案件と書類を確認する",
                "kind": "cases-documents",
                "paths": ["/cases", "/documents", "/admin/resources"],
            },
            {
                "step": 4,
                "title": "請求と提出漏れを確認する",
                "kind": "billing-review",
                "paths": ["/invoices", "/admin/missing-submissions"],
            },
            {
                "step": 5,
                "title": "実データ投入に進む",
                "kind": "real-data",
                "paths": ["/ingestions/upload", "/connectors/line/chat-ingestions"],
            },
        ],
        "resources": [
            {"label": "Admin UI", "path": "/admin/ui"},
            {"label": "Admin manifest", "path": "/admin/react-admin"},
            {"label": "Cases", "path": "/cases"},
            {"label": "Documents", "path": "/documents"},
            {"label": "Invoices", "path": "/invoices"},
            {"label": "Missing submissions", "path": "/admin/missing-submissions"},
            {"label": "Demo pack JSON", "path": "/admin/demo-pack"},
        ],
        "cases": current_cases,
        "sample_case_codes": [spec["case_code"] for spec in case_specs],
        "missing_submissions_preview": missing_submissions["items"][:5],
    }


def seed_line_field_organization_pack(
    settings: Settings,
    repository: Repository,
    ingestion_service: IngestionService,
) -> dict[str, object]:
    case_specs = _demo_case_specs(settings)
    created_cases: list[dict[str, object]] = []
    reused_cases: list[dict[str, object]] = []
    created_document_ids: list[int] = []
    created_case_codes: list[str] = []
    for spec in case_specs:
        case = _find_case(repository, spec["case_code"])
        has_documents = case is not None and repository.count_documents(case_id=case.id) > 0
        if has_documents:
            reused_cases.append(
                {
                    "case_code": spec["case_code"],
                    "case_id": case.id if case is not None else None,
                    "document_count": repository.count_documents(case_id=case.id) if case is not None else 0,
                }
            )
            continue

        seed_case_document_ids: list[int] = []
        for document_spec in spec["documents"]:
            result = ingestion_service.ingest(
                IngestionRequest(
                    case_code=spec["case_code"],
                    title=spec["title"],
                    filename=document_spec["filename"],
                    content=document_spec["content"].encode("utf-8"),
                    mime_type=document_spec["mime_type"],
                    source_type="line",
                    source_path=document_spec["source_path"],
                    client_name=spec["client_name"],
                    due_date=spec["due_date"],
                    invoice_status=spec["invoice_status"],
                    output_status=spec["output_status"],
                    extracted_text=document_spec["content"],
                    output_html=document_spec.get("output_html"),
                )
            )
            seed_case_document_ids.append(result.document_id)
            created_document_ids.append(result.document_id)
        created_case = _find_case(repository, spec["case_code"])
        created_cases.append(
            {
                "case_code": spec["case_code"],
                "case_id": created_case.id if created_case is not None else None,
                "document_ids": seed_case_document_ids,
            }
        )
        created_case_codes.append(spec["case_code"])

    if created_case_codes:
        repository.record_operation_log(
            event_type="demo_pack_seeded",
            entity_type="system",
            message="LINE現場整理パックのデモデータを投入しました。",
            metadata_json={
                "case_codes": created_case_codes,
                "created_document_ids": created_document_ids,
            },
        )
        repository.record_notification_delivery(
            deliver_to="auto",
            destination="line://demo-pack",
            delivered_count=max(1, len(created_document_ids)),
            digest_as_of=date.today().isoformat(),
            due_lookahead_days=2,
            invoice_lookahead_days=7,
            status="success",
            message="LINE現場整理パックの通知履歴を投入しました。",
            metadata_json={"pack": "LINE現場整理パック", "kind": "seed", "result": "success"},
        )
        repository.record_notification_delivery(
            deliver_to="slack",
            destination="slack://demo-pack",
            delivered_count=max(1, len(created_document_ids)),
            digest_as_of=date.today().isoformat(),
            due_lookahead_days=2,
            invoice_lookahead_days=7,
            status="failed",
            message="LINE現場整理パックの通知履歴を投入しました（失敗サンプル）。",
            error_message="Simulated delivery failure for demo pack visibility.",
            metadata_json={"pack": "LINE現場整理パック", "kind": "seed", "result": "failed"},
        )

    return {
        "seeded": bool(created_case_codes),
        "seeded_case_codes": created_case_codes,
        "created_cases": created_cases,
        "reused_cases": reused_cases,
        "created_document_ids": created_document_ids,
        "demo_pack": build_demo_pack_guide(repository, settings),
    }
