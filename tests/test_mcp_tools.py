from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.mcp.tools import (
    get_case_detail_tool,
    list_documents_tool,
    list_due_tasks_tool,
    list_invoices_tool,
    search_cases_tool,
    search_rag_tool,
)
from app.repositories.sqlite import SQLiteRepository


class McpToolTests(unittest.TestCase):
    def test_search_and_detail_tools_return_paginated_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with SQLiteRepository(root / "app.db") as repo:
                case = repo.upsert_case(
                    case_code="CASE-MCP-1",
                    title="MCP visible case",
                    client_name="Client MCP",
                    status="in_progress",
                    due_date="2026-07-10",
                    invoice_status="pending",
                    output_status="pending",
                )
                document = repo.register_document(
                    case_id=case.id,
                    source_type="api",
                    storage_key="originals/CASE-MCP-1/doc.txt",
                    filename="doc.txt",
                    mime_type="text/plain",
                    content_hash="hash-mcp-1",
                    size_bytes=12,
                )
                artifact = repo.register_artifact(
                    document_id=document.id,
                    artifact_type="raw_text",
                    storage_key="extracted_text/CASE-MCP-1/doc.txt",
                    content_hash="artifact-mcp-1",
                    generator="test",
                )
                repo.replace_rag_entries_for_document(
                    document.id,
                    [
                        {
                            "artifact_id": artifact.id,
                            "chunk_id": "chunk-1",
                            "title": "MCP visible case",
                            "body_text": "Reusable business data for MCP tools",
                            "metadata_json": {"case_code": "CASE-MCP-1"},
                            "content_hash": "rag-mcp-1",
                        }
                    ],
                )

                cases = search_cases_tool(repo, query="CASE-MCP-1", limit=1, offset=-5)
                self.assertEqual(1, cases["total"])
                self.assertEqual(1, len(cases["items"]))
                self.assertEqual(0, cases["offset"])

                detail = get_case_detail_tool(repo, case.id)
                self.assertIsNotNone(detail)
                self.assertEqual(case.id, detail["case"]["id"])
                self.assertEqual(1, len(detail["documents"]))
                self.assertEqual(1, len(detail["rag_entries"]))

                docs = list_documents_tool(repo, case_id=case.id, limit=1)
                self.assertEqual(1, docs["total"])
                self.assertEqual(1, len(docs["items"]))

                due = list_due_tasks_tool(repo, until_date="2026-07-31", status="in_progress")
                self.assertEqual(1, due["total"])
                self.assertEqual(1, len(due["items"]))

                invoices = list_invoices_tool(repo, invoice_status="pending")
                self.assertEqual(1, invoices["total"])
                self.assertEqual(1, len(invoices["items"]))

                rag = search_rag_tool(repo, query="Reusable business data", case_id=case.id)
                self.assertEqual(1, rag["total"])
                self.assertEqual(1, len(rag["items"]))


if __name__ == "__main__":
    unittest.main()
