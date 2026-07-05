from __future__ import annotations

import json
import unittest

from app.domain.models import Case, CaseDetail, Document, RagEntry
from app.services.document_snapshots import (
    attach_document_extraction_snapshots,
    build_document_extraction_snapshot,
)


class DocumentSnapshotTests(unittest.TestCase):
    def test_build_document_extraction_snapshot_returns_available_snapshot(self) -> None:
        case = Case(
            id=1,
            case_code="CASE-001",
            title="Case 1",
            client_name="Client A",
            status="open",
            due_date=None,
            invoice_status="pending",
            output_status="pending",
            created_at="2026-07-05T00:00:00+00:00",
            updated_at="2026-07-05T00:00:00+00:00",
            last_processed_at=None,
        )
        rag_entry = RagEntry(
            id=10,
            document_id=2,
            artifact_id=3,
            chunk_id="chunk-1",
            title="doc.txt",
            body_text="body",
            metadata_json=json.dumps(
                {
                    "extraction_source": "pypdf",
                    "extraction_engine": "pdfplumber",
                    "extraction_mode": "text",
                    "reprocess": True,
                }
            ),
            content_hash="hash-1",
            is_active=True,
            created_at="2026-07-05T00:00:00+00:00",
            updated_at="2026-07-05T00:00:00+00:00",
        )
        detail = CaseDetail(case=case, rag_entries=[rag_entry])

        snapshot = build_document_extraction_snapshot(detail, 2, case_id=case.id)

        self.assertIsNotNone(snapshot)
        self.assertTrue(snapshot["available"])
        self.assertEqual(case.id, snapshot["case_id"])
        self.assertEqual("pypdf", snapshot["extraction_source"])
        self.assertEqual("pdfplumber", snapshot["extraction_engine"])
        self.assertEqual("text", snapshot["extraction_mode"])
        self.assertTrue(snapshot["reprocess"])
        self.assertEqual("doc.txt", snapshot["title"])

    def test_build_document_extraction_snapshot_returns_placeholder_when_missing_entry(self) -> None:
        case = Case(
            id=1,
            case_code="CASE-002",
            title="Case 2",
            client_name="Client B",
            status="open",
            due_date=None,
            invoice_status="pending",
            output_status="pending",
            created_at="2026-07-05T00:00:00+00:00",
            updated_at="2026-07-05T00:00:00+00:00",
            last_processed_at=None,
        )
        detail = CaseDetail(case=case, rag_entries=[])

        snapshot = build_document_extraction_snapshot(detail, 99, case_id=case.id)

        self.assertIsNotNone(snapshot)
        self.assertFalse(snapshot["available"])
        self.assertEqual("no_rag_entry", snapshot["reason"])
        self.assertEqual(99, snapshot["document_id"])
        self.assertEqual(case.id, snapshot["case_id"])

    def test_attach_document_extraction_snapshots_uses_case_cache(self) -> None:
        case = Case(
            id=7,
            case_code="CASE-007",
            title="Case 7",
            client_name="Client G",
            status="open",
            due_date=None,
            invoice_status="pending",
            output_status="pending",
            created_at="2026-07-05T00:00:00+00:00",
            updated_at="2026-07-05T00:00:00+00:00",
            last_processed_at=None,
        )
        rag_entry = RagEntry(
            id=17,
            document_id=70,
            artifact_id=71,
            chunk_id="chunk-7",
            title="shared.txt",
            body_text="body",
            metadata_json=json.dumps({"extraction_source": "pdfplumber"}),
            content_hash="hash-7",
            is_active=True,
            created_at="2026-07-05T00:00:00+00:00",
            updated_at="2026-07-05T00:00:00+00:00",
        )
        detail = CaseDetail(case=case, rag_entries=[rag_entry])
        documents = [
            Document(
                id=70,
                case_id=case.id,
                source_type="api",
                source_path=None,
                storage_key="storage/70",
                filename="shared.txt",
                mime_type="text/plain",
                content_hash="hash-7",
                size_bytes=10,
                version=1,
                is_deleted=False,
                deleted_at=None,
                created_at="2026-07-05T00:00:00+00:00",
                updated_at="2026-07-05T00:00:00+00:00",
            ),
            Document(
                id=71,
                case_id=case.id,
                source_type="api",
                source_path=None,
                storage_key="storage/71",
                filename="another.txt",
                mime_type="text/plain",
                content_hash="hash-8",
                size_bytes=12,
                version=1,
                is_deleted=False,
                deleted_at=None,
                created_at="2026-07-05T00:00:00+00:00",
                updated_at="2026-07-05T00:00:00+00:00",
            ),
        ]
        calls: list[int] = []

        def case_detail_for_case_id(case_id: int):
            calls.append(case_id)
            return detail

        snapshots = attach_document_extraction_snapshots(
            documents,
            case_detail_for_case_id,
            serialize_document=lambda document: {"id": document.id, "case_id": document.case_id},
        )

        self.assertEqual([case.id], calls)
        self.assertEqual(2, len(snapshots))
        self.assertEqual("pdfplumber", snapshots[0]["extraction"]["extraction_source"])
        self.assertFalse(snapshots[1]["extraction"]["available"])
        self.assertEqual("no_rag_entry", snapshots[1]["extraction"]["reason"])


if __name__ == "__main__":
    unittest.main()
