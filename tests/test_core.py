from __future__ import annotations

import base64
import hashlib
import io
import hmac
import json
import os
import sys
import types
import tempfile
import unittest
import warnings
from pathlib import Path
import zipfile
from unittest.mock import patch

import httpx

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.",
)

from fastapi.testclient import TestClient

from app.config import Settings
from app.api.main import create_app
from app.domain.models import IngestionRequest
from app.repositories.sqlite import SQLiteRepository
from app.services.documents import DocumentService
from app.services.ingestion import IngestionService
from app.services.extraction import extract_text
from app.storage.local import LocalFileStorageAdapter


class SQLiteRepositoryTests(unittest.TestCase):
    def test_case_document_and_rag_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            with SQLiteRepository(db_path) as repo:
                case = repo.upsert_case(case_code="CASE-001", title="Sample case", client_name="Client A")
                self.assertEqual("CASE-001", case.case_code)

                same_case = repo.upsert_case(case_code="CASE-001", title="Sample case v2", client_name="Client A")
                self.assertEqual(case.id, same_case.id)
                self.assertEqual("Sample case v2", same_case.title)

                document = repo.register_document(
                    case_id=case.id,
                    source_type="discord",
                    storage_key="originals/CASE-001/sample.pdf",
                    filename="sample.pdf",
                    mime_type="application/pdf",
                    content_hash="hash-1",
                    size_bytes=128,
                )
                self.assertEqual(1, document.version)

                same_document = repo.register_document(
                    case_id=case.id,
                    source_type="discord",
                    storage_key="originals/CASE-001/sample.pdf",
                    filename="sample.pdf",
                    mime_type="application/pdf",
                    content_hash="hash-1",
                    size_bytes=128,
                )
                self.assertEqual(document.id, same_document.id)

                next_document = repo.register_document(
                    case_id=case.id,
                    source_type="discord",
                    storage_key="originals/CASE-001/sample-v2.pdf",
                    filename="sample.pdf",
                    mime_type="application/pdf",
                    content_hash="hash-2",
                    size_bytes=256,
                )
                self.assertEqual(2, next_document.version)

                artifact = repo.register_artifact(
                    document_id=document.id,
                    artifact_type="raw_text",
                    storage_key="extracted_text/CASE-001/sample.txt",
                    content_hash="text-hash",
                    generator="test",
                )
                self.assertEqual(document.id, artifact.document_id)

                rag_entries = repo.replace_rag_entries_for_document(
                    document.id,
                    [
                        {
                            "artifact_id": artifact.id,
                            "chunk_id": "chunk-1",
                            "title": "Sample case",
                            "body_text": "Body text",
                            "metadata_json": {"case_code": "CASE-001"},
                            "content_hash": "rag-hash",
                        }
                    ],
                )
                self.assertEqual(1, len(rag_entries))
                self.assertTrue(rag_entries[0].is_active)

                job = repo.create_processing_job(job_type="ingestion", case_id=case.id)
                self.assertEqual("running", job.job_status)
                completed_job = repo.update_processing_job(
                    job.id,
                    document_id=document.id,
                    job_status="completed",
                    finished_at="2026-07-03T00:00:00+00:00",
                )
                self.assertEqual(document.id, completed_job.document_id)
                self.assertEqual("completed", completed_job.job_status)
                second_job = repo.create_processing_job(job_type="ingestion", case_id=case.id)
                repo.update_processing_job(
                    second_job.id,
                    document_id=document.id,
                    job_status="completed",
                    finished_at="2026-07-03T01:00:00+00:00",
                )
                completed_jobs = repo.list_processing_jobs(case_id=case.id, job_status="completed")
                self.assertEqual(2, len(completed_jobs))
                self.assertEqual(1, len(repo.list_processing_jobs(case_id=case.id, job_status="completed", limit=1)))
                self.assertEqual(
                    1,
                    len(repo.list_processing_jobs(case_id=case.id, job_status="completed", limit=1, offset=1)),
                )
                self.assertNotEqual(
                    repo.list_processing_jobs(case_id=case.id, job_status="completed", limit=1)[0].id,
                    repo.list_processing_jobs(case_id=case.id, job_status="completed", limit=1, offset=1)[0].id,
                )

                repo.mark_document_deleted(document.id)
                deleted = repo.get_document(document.id)
                self.assertTrue(deleted.is_deleted)

    def test_business_queries_and_rag_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            with SQLiteRepository(db_path) as repo:
                case_one = repo.upsert_case(
                    case_code="CASE-100",
                    title="Invoice pending case",
                    client_name="Client A",
                    status="in_progress",
                    due_date="2026-07-10",
                    invoice_status="pending",
                    output_status="pending",
                )
                case_two = repo.upsert_case(
                    case_code="CASE-200",
                    title="Completed case",
                    client_name="Client B",
                    status="done",
                    due_date="2026-08-01",
                    invoice_status="sent",
                    output_status="completed",
                )
                case_three = repo.upsert_case(
                    case_code="CASE-300",
                    title="Invoice pending follow-up",
                    client_name="Client C",
                    status="in_progress",
                    due_date="2026-07-15",
                    invoice_status="pending",
                    output_status="pending",
                )

                document = repo.register_document(
                    case_id=case_one.id,
                    source_type="discord",
                    storage_key="originals/CASE-100/invoice.pdf",
                    filename="invoice.pdf",
                    mime_type="application/pdf",
                    content_hash="doc-hash",
                    size_bytes=64,
                )
                artifact = repo.register_artifact(
                    document_id=document.id,
                    artifact_type="raw_text",
                    storage_key="extracted_text/CASE-100/invoice.txt",
                    content_hash="artifact-hash",
                    generator="test",
                )
                repo.replace_rag_entries_for_document(
                    document.id,
                    [
                        {
                            "artifact_id": artifact.id,
                            "chunk_id": "chunk-1",
                            "title": "Invoice pending case",
                            "body_text": "Deadline and billing details",
                            "metadata_json": {"case_code": "CASE-100"},
                            "content_hash": "rag-hash",
                        }
                    ],
                )
                second_document = repo.register_document(
                    case_id=case_three.id,
                    source_type="discord",
                    storage_key="originals/CASE-300/followup.pdf",
                    filename="followup.pdf",
                    mime_type="application/pdf",
                    content_hash="doc-hash-2",
                    size_bytes=96,
                )
                second_artifact = repo.register_artifact(
                    document_id=second_document.id,
                    artifact_type="raw_text",
                    storage_key="extracted_text/CASE-300/followup.txt",
                    content_hash="artifact-hash-2",
                    generator="test",
                )
                repo.replace_rag_entries_for_document(
                    second_document.id,
                    [
                        {
                            "artifact_id": second_artifact.id,
                            "chunk_id": "chunk-1",
                            "title": "Invoice pending follow-up",
                            "body_text": "Deadline and billing details for the next cycle",
                            "metadata_json": {"case_code": "CASE-300"},
                            "content_hash": "rag-hash-2",
                        }
                    ],
                )

                search_results = repo.search_cases(query="CASE-100")
                self.assertEqual(1, len(search_results))
                self.assertEqual(case_one.id, search_results[0].id)

                page_one_cases = repo.search_cases(limit=1)
                page_two_cases = repo.search_cases(limit=1, offset=1)
                self.assertEqual(1, len(page_one_cases))
                self.assertEqual(1, len(page_two_cases))
                self.assertNotEqual(page_one_cases[0].id, page_two_cases[0].id)

                due_results = repo.list_due_tasks(until_date="2026-07-31", status="in_progress")
                self.assertEqual([case_one.id, case_three.id], [item.id for item in due_results])
                due_page_one = repo.list_due_tasks(until_date="2026-07-31", status="in_progress", limit=1)
                due_page_two = repo.list_due_tasks(until_date="2026-07-31", status="in_progress", limit=1, offset=1)
                self.assertEqual(1, len(due_page_one))
                self.assertEqual(1, len(due_page_two))
                self.assertNotEqual(due_page_one[0].id, due_page_two[0].id)

                invoice_results = repo.list_invoices(invoice_status="pending")
                self.assertCountEqual([case_one.id, case_three.id], [item.id for item in invoice_results])
                invoice_page_one = repo.list_invoices(invoice_status="pending", limit=1)
                invoice_page_two = repo.list_invoices(invoice_status="pending", limit=1, offset=1)
                self.assertEqual(1, len(invoice_page_one))
                self.assertEqual(1, len(invoice_page_two))
                self.assertNotEqual(invoice_page_one[0].id, invoice_page_two[0].id)

                rag_results = repo.search_rag(query="billing")
                self.assertEqual(2, len(rag_results))
                self.assertCountEqual(
                    {"Invoice pending case", "Invoice pending follow-up"},
                    {item.title for item in rag_results},
                )
                rag_page_one = repo.search_rag(query="billing", limit=1)
                rag_page_two = repo.search_rag(query="billing", limit=1, offset=1)
                self.assertEqual(1, len(rag_page_one))
                self.assertEqual(1, len(rag_page_two))
                self.assertNotEqual(rag_page_one[0].id, rag_page_two[0].id)

                detail = repo.get_case_detail(case_one.id)
                self.assertIsNotNone(detail)
                self.assertEqual(1, len(detail.documents if detail else []))
                self.assertEqual(1, len(detail.rag_entries if detail else []))
                self.assertEqual(case_two.id, repo.get_case(case_two.id).id)


class IngestionServiceTests(unittest.TestCase):
    def test_ingest_auto_extracts_text_for_plain_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )
            with SQLiteRepository(settings.database_path) as repo:
                storage = LocalFileStorageAdapter(settings.storage_root)
                service = IngestionService(settings, repo, storage)

                result = service.ingest(
                    IngestionRequest(
                        case_code="CASE-002",
                        title="Import test",
                        filename="document.txt",
                        content=b"Extracted text",
                        mime_type="text/plain",
                    )
                )

                self.assertTrue((settings.storage_root / result.original_storage_key).exists())
                self.assertTrue((settings.storage_root / "rag" / "CASE-002" / f"{result.document_id}.json").exists())
                jobs = repo.list_processing_jobs(case_id=result.case_id)
                self.assertEqual(1, len(jobs))
                self.assertEqual("completed", jobs[0].job_status)
                self.assertEqual(result.document_id, jobs[0].document_id)


class ApiTests(unittest.TestCase):
    def test_api_exposes_business_search_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "data" / "app.db"
            with SQLiteRepository(database_path) as repo:
                case = repo.upsert_case(
                    case_code="CASE-API-1",
                    title="API visible case",
                    client_name="Client API",
                    status="in_progress",
                    due_date="2026-07-20",
                    invoice_status="pending",
                    output_status="pending",
                )
                document = repo.register_document(
                    case_id=case.id,
                    source_type="discord",
                    storage_key="originals/CASE-API-1/photo.jpg",
                    filename="photo.jpg",
                    mime_type="image/jpeg",
                    content_hash="hash-api",
                    size_bytes=128,
                )
                artifact = repo.register_artifact(
                    document_id=document.id,
                    artifact_type="raw_text",
                    storage_key="extracted_text/CASE-API-1/photo.txt",
                    content_hash="artifact-api",
                    generator="test",
                )
                repo.replace_rag_entries_for_document(
                    document.id,
                    [
                        {
                            "artifact_id": artifact.id,
                            "chunk_id": "chunk-1",
                            "title": "API visible case",
                            "body_text": "Reusable business data",
                            "metadata_json": {"case_code": "CASE-API-1"},
                            "content_hash": "rag-api",
                        }
                    ],
                )
                case_two = repo.upsert_case(
                    case_code="CASE-API-2",
                    title="API supporting case",
                    client_name="Client API 2",
                    status="new",
                    due_date="2026-07-25",
                    invoice_status="pending",
                    output_status="completed",
                )
                document_two = repo.register_document(
                    case_id=case_two.id,
                    source_type="discord",
                    storage_key="originals/CASE-API-2/appendix.jpg",
                    filename="appendix.jpg",
                    mime_type="image/jpeg",
                    content_hash="hash-api-2",
                    size_bytes=64,
                )
                artifact_two = repo.register_artifact(
                    document_id=document_two.id,
                    artifact_type="raw_text",
                    storage_key="extracted_text/CASE-API-2/appendix.txt",
                    content_hash="artifact-api-2",
                    generator="test",
                )
                repo.replace_rag_entries_for_document(
                    document_two.id,
                    [
                        {
                            "artifact_id": artifact_two.id,
                            "chunk_id": "chunk-1",
                            "title": "API supporting case",
                            "body_text": "Reusable business data appendix",
                            "metadata_json": {"case_code": "CASE-API-2"},
                            "content_hash": "rag-api-2",
                        }
                    ],
                )
                job = repo.create_processing_job(job_type="ingestion", case_id=case.id, document_id=document.id, job_status="completed")
                second_job = repo.create_processing_job(job_type="ingestion", case_id=case.id, document_id=document.id, job_status="completed")
                repo.update_processing_job(
                    second_job.id,
                    finished_at="2026-07-03T01:30:00+00:00",
                )
                operation_log = repo.record_operation_log(
                    event_type="case_updated",
                    entity_type="case",
                    entity_id=case_two.id,
                    case_id=case_two.id,
                    message="Case updated for admin recent test.",
                    metadata_json={"case_code": case_two.case_code},
                )
                notification_delivery = repo.record_notification_delivery(
                    deliver_to="auto",
                    destination="discord://admin-recent",
                    delivered_count=1,
                    digest_as_of="2026-07-04",
                    due_lookahead_days=1,
                    invoice_lookahead_days=7,
                    message="Admin recent notification delivery.",
                    metadata_json={"case_code": case_two.case_code},
                )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(database_path)
            try:
                with TestClient(app) as client:
                    response = client.get("/cases/search", params={"query": "CASE-API-1"})
                    self.assertEqual(200, response.status_code)
                    self.assertEqual("CASE-API-1", response.json()[0]["case_code"])

                    summary_response = client.get("/summary")
                    self.assertEqual(200, summary_response.status_code)
                    self.assertEqual(2, summary_response.json()["cases_total"])
                    self.assertEqual(2, summary_response.json()["documents_total"])
                    self.assertEqual(2, summary_response.json()["documents_active"])
                    self.assertEqual(2, summary_response.json()["processing_jobs_total"])
                    self.assertEqual(1, summary_response.json()["operation_logs_total"])
                    self.assertEqual(2, summary_response.json()["rag_entries_total"])

                    admin_overview = client.get("/admin/overview")
                    self.assertEqual(200, admin_overview.status_code)
                    self.assertEqual("development", admin_overview.json()["settings"]["app_env"])
                    self.assertEqual("sqlite", admin_overview.json()["settings"]["repository_backend"])
                    self.assertEqual("local", admin_overview.json()["settings"]["storage_backend"])
                    self.assertFalse(admin_overview.json()["settings"]["insforge"]["base_url_configured"])
                    self.assertFalse(admin_overview.json()["settings"]["insforge"]["repository_ready"])
                    self.assertFalse(admin_overview.json()["settings"]["insforge"]["storage_ready"])
                    self.assertEqual(
                        {
                            "pypdf": False,
                            "pdfplumber": False,
                            "pdf2image": False,
                            "pillow": False,
                            "pytesseract": False,
                            "xlrd": False,
                            "extract_msg": False,
                            "pdf_text_parsing_ready": False,
                            "image_ocr_ready": False,
                            "scanned_pdf_ocr_ready": False,
                            "legacy_xls_ready": False,
                            "legacy_outlook_msg_ready": False,
                        },
                        admin_overview.json()["settings"]["extraction"],
                    )
                    self.assertEqual(2, admin_overview.json()["summary"]["cases_total"])
                    self.assertEqual(2, admin_overview.json()["summary"]["documents_total"])
                    self.assertEqual(1, admin_overview.json()["breakdown"]["case_statuses"]["in_progress"])
                    self.assertEqual(1, admin_overview.json()["breakdown"]["case_statuses"]["new"])
                    self.assertEqual(2, admin_overview.json()["breakdown"]["invoice_statuses"]["pending"])
                    self.assertEqual(0, admin_overview.json()["breakdown"]["invoice_statuses"]["unbilled"])
                    self.assertEqual(1, admin_overview.json()["breakdown"]["output_statuses"]["pending"])
                    self.assertEqual(1, admin_overview.json()["breakdown"]["output_statuses"]["completed"])
                    self.assertEqual(2, admin_overview.json()["breakdown"]["document_source_types"]["discord"])

                    admin_backends = client.get("/admin/backends")
                    self.assertEqual(200, admin_backends.status_code)
                    backends_body = admin_backends.json()
                    self.assertEqual("development", backends_body["app_env"])
                    self.assertEqual("sqlite", backends_body["repository_backend"])
                    self.assertEqual("local", backends_body["storage_backend"])
                    self.assertFalse(backends_body["insforge"]["repository_ready"])
                    self.assertFalse(backends_body["insforge"]["storage_ready"])
                    self.assertIn("INSFORGE_BASE_URL", backends_body["insforge"]["repository_missing"])
                    self.assertIn("INSFORGE_STORAGE_BUCKET", backends_body["insforge"]["storage_missing"])
                    self.assertEqual(
                        {
                            "pypdf": False,
                            "pdfplumber": False,
                            "pdf2image": False,
                            "pillow": False,
                            "pytesseract": False,
                            "xlrd": False,
                            "extract_msg": False,
                            "pdf_text_parsing_ready": False,
                            "image_ocr_ready": False,
                            "scanned_pdf_ocr_ready": False,
                            "legacy_xls_ready": False,
                            "legacy_outlook_msg_ready": False,
                        },
                        backends_body["extraction"],
                    )

                    admin_recent = client.get("/admin/recent", params={"limit": 1})
                    self.assertEqual(200, admin_recent.status_code)
                    recent_body = admin_recent.json()
                    self.assertEqual(1, recent_body["limit"])
                    self.assertEqual("CASE-API-2", recent_body["cases"][0]["case_code"])
                    self.assertEqual("appendix.jpg", recent_body["documents"][0]["filename"])
                    self.assertIn("extraction", recent_body["documents"][0])
                    self.assertEqual("case_updated", recent_body["operation_logs"][0]["event_type"])
                    self.assertEqual("auto", recent_body["notification_deliveries"][0]["deliver_to"])

                    admin_activity = client.get("/admin/activity", params={"limit": 10})
                    self.assertEqual(200, admin_activity.status_code)
                    activity_body = admin_activity.json()
                    self.assertEqual(10, activity_body["limit"])
                    self.assertGreaterEqual(len(activity_body["items"]), 4)
                    self.assertIn("case", {item["kind"] for item in activity_body["items"]})
                    self.assertIn("document", {item["kind"] for item in activity_body["items"]})
                    self.assertIn("operation_log", {item["kind"] for item in activity_body["items"]})
                    self.assertIn("notification_delivery", {item["kind"] for item in activity_body["items"]})
                    self.assertIn(
                        "Admin recent notification delivery.",
                        {item["summary"] for item in activity_body["items"]},
                    )

                    case_activity = client.get("/admin/activity", params={"kind": "case", "case_id": case_two.id})
                    self.assertEqual(200, case_activity.status_code)
                    case_activity_body = case_activity.json()
                    self.assertEqual(20, case_activity_body["limit"])
                    self.assertGreaterEqual(len(case_activity_body["items"]), 1)
                    self.assertTrue(all(item["kind"] == "case" for item in case_activity_body["items"]))
                    self.assertTrue(all(item["entity_id"] == case_two.id for item in case_activity_body["items"]))

                    document_activity = client.get(
                        "/admin/activity",
                        params={"kind": "document", "document_id": document_two.id},
                    )
                    self.assertEqual(200, document_activity.status_code)
                    document_activity_body = document_activity.json()
                    self.assertGreaterEqual(len(document_activity_body["items"]), 1)
                    self.assertTrue(all(item["kind"] == "document" for item in document_activity_body["items"]))
                    self.assertTrue(
                        all(item["entity_id"] == document_two.id for item in document_activity_body["items"])
                    )

                    admin_dashboard = client.get(
                        "/admin/dashboard",
                        params={
                            "recent_limit": 1,
                            "activity_limit": 5,
                            "kind": "case",
                            "case_id": case_two.id,
                        },
                    )
                    self.assertEqual(200, admin_dashboard.status_code)
                    dashboard_body = admin_dashboard.json()
                    self.assertEqual("development", dashboard_body["overview"]["settings"]["app_env"])
                    self.assertEqual(2, dashboard_body["overview"]["summary"]["cases_total"])
                    self.assertEqual(1, dashboard_body["recent"]["limit"])
                    self.assertEqual("CASE-API-2", dashboard_body["recent"]["cases"][0]["case_code"])
                    self.assertIn("extraction", dashboard_body["recent"]["documents"][0])
                    self.assertEqual(5, dashboard_body["activity"]["limit"])
                    self.assertTrue(all(item["kind"] == "case" for item in dashboard_body["activity"]["items"]))
                    self.assertEqual(1, dashboard_body["notifications"]["total"])
                    self.assertEqual(0, len(dashboard_body["notifications"]["recent_failures"]))
                    self.assertEqual(
                        {
                            "pypdf": False,
                            "pdfplumber": False,
                            "pdf2image": False,
                            "pillow": False,
                            "pytesseract": False,
                            "xlrd": False,
                            "extract_msg": False,
                            "pdf_text_parsing_ready": False,
                            "image_ocr_ready": False,
                            "scanned_pdf_ocr_ready": False,
                            "legacy_xls_ready": False,
                            "legacy_outlook_msg_ready": False,
                        },
                        dashboard_body["overview"]["settings"]["extraction"],
                    )

                    admin_page = client.get("/admin")
                    self.assertEqual(200, admin_page.status_code)
                    self.assertIn("O's flow Admin", admin_page.text)
                    self.assertIn("/admin/dashboard", admin_page.text)
                    self.assertIn("CASE-API-2", admin_page.text)
                    self.assertIn("appendix.jpg", admin_page.text)
                    self.assertIn("no extraction snapshot", admin_page.text)
                    self.assertIn("Extraction helpers:", admin_page.text)

                    admin_resources = client.get("/admin/resources")
                    self.assertEqual(200, admin_resources.status_code)
                    resources_body = admin_resources.json()
                    self.assertEqual(
                        ["cases", "documents", "operation_logs", "notification_deliveries", "admin"],
                        [resource["name"] for resource in resources_body["resources"]],
                    )
                    self.assertEqual("/cases", resources_body["resources"][0]["collection_path"])
                    self.assertEqual("/cases/search", resources_body["resources"][0]["search_path"])
                    self.assertIn("filters", resources_body["resources"][1])
                    self.assertIn("supports", resources_body["resources"][0])
                    self.assertIn("label_field", resources_body["resources"][1])
                    self.assertEqual("case_id", resources_body["resources"][0]["detail_key"])
                    self.assertIn("default_sort", resources_body["resources"][2])
                    self.assertIn("detail_fields", resources_body["resources"][3])
                    self.assertEqual("notification_delivery_id", resources_body["resources"][3]["detail_key"])
                    self.assertEqual("/cases/{case_id}", resources_body["resources"][0]["edit_path"])
                    self.assertEqual(
                        ["title", "client_name", "status", "due_date", "invoice_status", "output_status", "last_processed_at"],
                        [field["name"] for field in resources_body["resources"][0]["form_fields"]],
                    )
                    self.assertEqual(
                        ["title", "client_name", "status", "due_date", "invoice_status", "output_status", "last_processed_at"],
                        resources_body["resources"][0]["editable_fields"],
                    )
                    self.assertEqual(["edit", "activity"], resources_body["resources"][0]["actions"])
                    self.assertEqual(["manage", "activity", "reassign", "reprocess", "delete"], resources_body["resources"][1]["actions"])
                    self.assertEqual(["view", "summary", "trends", "alerts", "report"], resources_body["resources"][3]["actions"])

                    admin_react_admin = client.get("/admin/react-admin")
                    self.assertEqual(200, admin_react_admin.status_code)
                    react_admin_body = admin_react_admin.json()
                    self.assertEqual("react-admin", react_admin_body["framework"])
                    self.assertEqual(
                        ["cases", "documents", "operation_logs", "notification_deliveries", "admin"],
                        [resource["name"] for resource in react_admin_body["resources"]],
                    )
                    self.assertEqual("/cases", react_admin_body["resources"][0]["listPath"])
                    self.assertEqual("/cases/{case_id}", react_admin_body["resources"][0]["showPath"])
                    self.assertEqual("/cases/{case_id}", react_admin_body["resources"][0]["editPath"])
                    self.assertEqual(
                        ["title", "client_name", "status", "due_date", "invoice_status", "output_status", "last_processed_at"],
                        [field["name"] for field in react_admin_body["resources"][0]["formFields"]],
                    )
                    self.assertEqual(["edit", "activity"], react_admin_body["resources"][0]["actions"])
                    self.assertIn("extraction", react_admin_body["resources"][1]["fields"])
                    self.assertIn("extraction", react_admin_body["resources"][1]["detailFields"])

                    operation_log_detail = client.get(f"/operation-logs/{operation_log.id}")
                    self.assertEqual(200, operation_log_detail.status_code)
                    self.assertEqual(operation_log.id, operation_log_detail.json()["id"])
                    self.assertEqual("case_updated", operation_log_detail.json()["event_type"])

                    notification_detail = client.get(f"/notification-deliveries/{notification_delivery.id}")
                    self.assertEqual(200, notification_detail.status_code)
                    self.assertEqual(notification_delivery.id, notification_detail.json()["id"])
                    self.assertEqual("auto", notification_detail.json()["deliver_to"])

                    cases_alias = client.get("/cases", params={"limit": 1})
                    self.assertEqual(200, cases_alias.status_code)
                    self.assertEqual("2", cases_alias.headers.get("X-Total-Count"))
                    self.assertEqual(1, len(cases_alias.json()))
                    self.assertEqual("CASE-API-2", cases_alias.json()[0]["case_code"])

                    admin_ui = client.get("/admin/ui")
                    self.assertEqual(200, admin_ui.status_code)
                    self.assertIn("O's flow Admin UI", admin_ui.text)
                    self.assertIn("/admin/dashboard", admin_ui.text)
                    self.assertIn("/admin/resources", admin_ui.text)
                    self.assertIn("activityKind", admin_ui.text)
                    self.assertIn("supports", admin_ui.text)
                    self.assertIn("resourceSelect", admin_ui.text)
                    self.assertIn("resourceBrowserContent", admin_ui.text)
                    self.assertIn("loadResourceButton", admin_ui.text)
                    self.assertIn("Load detail", admin_ui.text)
                    self.assertIn("resourceActionBar", admin_ui.text)
                    self.assertIn("resourceDueBefore", admin_ui.text)
                    self.assertIn("resourceInvoiceStatus", admin_ui.text)
                    self.assertIn("resourceOutputStatus", admin_ui.text)
                    self.assertIn("Case Editor", admin_ui.text)
                    self.assertIn("caseEditorFields", admin_ui.text)
                    self.assertIn("caseEditorLoadButton", admin_ui.text)
                    self.assertIn("caseEditorSaveButton", admin_ui.text)
                    self.assertIn("Document Tools", admin_ui.text)
                    self.assertIn("documentToolLoadButton", admin_ui.text)
                    self.assertIn("documentToolReassignButton", admin_ui.text)
                    self.assertIn("documentToolReprocessButton", admin_ui.text)
                    self.assertIn("documentToolDeleteButton", admin_ui.text)
                    self.assertIn("Edit case", admin_ui.text)
                    self.assertIn("Manage document", admin_ui.text)
                    self.assertIn("Extraction:", admin_ui.text)
                    self.assertIn("Notification Explorer", admin_ui.text)
                    self.assertIn("notificationExplorerSummaryButton", admin_ui.text)
                    self.assertIn("notificationExplorerTrendsButton", admin_ui.text)
                    self.assertIn("notificationExplorerAlertsButton", admin_ui.text)
                    self.assertIn("notificationExplorerReportButton", admin_ui.text)
                    self.assertIn("resourceActionCaseEditor", admin_ui.text)
                    self.assertIn("resourceActionDocumentTool", admin_ui.text)
                    self.assertIn("resourceActionNotificationSummary", admin_ui.text)
                    self.assertIn("Extraction helpers:", admin_ui.text)
                    self.assertIn("extraction", admin_ui.text)
                    self.assertIn("PDF text parsing ready:", admin_ui.text)
                    self.assertIn("Image OCR ready:", admin_ui.text)
                    self.assertIn("Scanned PDF OCR ready:", admin_ui.text)

                    cases_page_one = client.get("/cases/search", params={"limit": 1})
                    self.assertEqual(200, cases_page_one.status_code)
                    self.assertEqual("2", cases_page_one.headers.get("X-Total-Count"))
                    cases_page_two = client.get("/cases/search", params={"limit": 1, "offset": 1})
                    self.assertEqual(200, cases_page_two.status_code)
                    self.assertEqual(1, len(cases_page_one.json()))
                    self.assertEqual(1, len(cases_page_two.json()))
                    self.assertNotEqual(cases_page_one.json()[0]["id"], cases_page_two.json()[0]["id"])
                    cases_negative_limit = client.get("/cases/search", params={"limit": -1})
                    self.assertEqual(200, cases_negative_limit.status_code)
                    self.assertEqual(1, len(cases_negative_limit.json()))

                    detail_response = client.get(f"/cases/{case.id}")
                    self.assertEqual(200, detail_response.status_code)
                    self.assertEqual(1, len(detail_response.json()["documents"]))
                    case_document_item = detail_response.json()["documents"][0]
                    self.assertIn("extraction", case_document_item)
                    self.assertTrue(case_document_item["extraction"]["available"])
                    self.assertIn("extraction_source", case_document_item["extraction"])
                    self.assertIn("extraction_engine", case_document_item["extraction"])

                    documents_response = client.get("/documents", params={"case_id": case.id})
                    self.assertEqual(200, documents_response.status_code)
                    self.assertEqual(1, len(documents_response.json()))
                    self.assertEqual("1", documents_response.headers.get("X-Total-Count"))
                    document_item = documents_response.json()[0]
                    self.assertIn("extraction", document_item)
                    self.assertTrue(document_item["extraction"]["available"])
                    self.assertIn("extraction_source", document_item["extraction"])
                    self.assertIn("extraction_engine", document_item["extraction"])
                    self.assertIn("extraction_mode", document_item["extraction"])

                    document_response = client.get(f"/documents/{document.id}")
                    self.assertEqual(200, document_response.status_code)
                    self.assertEqual(document.id, document_response.json()["id"])

                    due_response = client.get("/tasks/due", params={"until_date": "2026-07-31"})
                    self.assertEqual(200, due_response.status_code)
                    self.assertEqual("2", due_response.headers.get("X-Total-Count"))
                    self.assertCountEqual(
                        {"CASE-API-1", "CASE-API-2"},
                        {item["case_code"] for item in due_response.json()},
                    )
                    due_page_one = client.get("/tasks/due", params={"until_date": "2026-07-31", "limit": 1})
                    self.assertEqual(200, due_page_one.status_code)
                    due_page_two = client.get("/tasks/due", params={"until_date": "2026-07-31", "limit": 1, "offset": 1})
                    self.assertEqual(200, due_page_two.status_code)
                    self.assertEqual(1, len(due_page_one.json()))
                    self.assertEqual(1, len(due_page_two.json()))
                    self.assertNotEqual(due_page_one.json()[0]["id"], due_page_two.json()[0]["id"])

                    invoice_response = client.get("/invoices", params={"invoice_status": "pending"})
                    self.assertEqual(200, invoice_response.status_code)
                    self.assertEqual("2", invoice_response.headers.get("X-Total-Count"))
                    self.assertCountEqual(
                        {"CASE-API-1", "CASE-API-2"},
                        {item["case_code"] for item in invoice_response.json()},
                    )
                    invoice_page_one = client.get("/invoices", params={"invoice_status": "pending", "limit": 1})
                    self.assertEqual(200, invoice_page_one.status_code)
                    invoice_page_two = client.get("/invoices", params={"invoice_status": "pending", "limit": 1, "offset": 1})
                    self.assertEqual(200, invoice_page_two.status_code)
                    self.assertEqual(1, len(invoice_page_one.json()))
                    self.assertEqual(1, len(invoice_page_two.json()))
                    self.assertNotEqual(invoice_page_one.json()[0]["id"], invoice_page_two.json()[0]["id"])

                    rag_response = client.get("/rag/search", params={"query": "Reusable"})
                    self.assertEqual(200, rag_response.status_code)
                    self.assertEqual("2", rag_response.headers.get("X-Total-Count"))
                    self.assertCountEqual(
                        {"API visible case", "API supporting case"},
                        {item["title"] for item in rag_response.json()},
                    )
                    rag_page_one = client.get("/rag/search", params={"query": "Reusable", "limit": 1})
                    self.assertEqual(200, rag_page_one.status_code)
                    rag_page_two = client.get("/rag/search", params={"query": "Reusable", "limit": 1, "offset": 1})
                    self.assertEqual(200, rag_page_two.status_code)
                    self.assertEqual(1, len(rag_page_one.json()))
                    self.assertEqual(1, len(rag_page_two.json()))
                    self.assertNotEqual(rag_page_one.json()[0]["id"], rag_page_two.json()[0]["id"])

                    jobs_response = client.get("/processing-jobs", params={"case_id": case.id})
                    self.assertEqual(200, jobs_response.status_code)
                    self.assertEqual("2", jobs_response.headers.get("X-Total-Count"))
                    self.assertEqual(2, len(jobs_response.json()))
                    self.assertEqual("completed", jobs_response.json()[0]["job_status"])

                    jobs_page_one = client.get("/processing-jobs", params={"case_id": case.id, "limit": 1})
                    self.assertEqual(200, jobs_page_one.status_code)
                    jobs_page_two = client.get("/processing-jobs", params={"case_id": case.id, "limit": 1, "offset": 1})
                    self.assertEqual(200, jobs_page_two.status_code)
                    self.assertEqual(1, len(jobs_page_one.json()))
                    self.assertEqual(1, len(jobs_page_two.json()))
                    self.assertNotEqual(jobs_page_one.json()[0]["id"], jobs_page_two.json()[0]["id"])
                    jobs_negative_offset = client.get(
                        "/processing-jobs",
                        params={"case_id": case.id, "limit": 1, "offset": -9},
                    )
                    self.assertEqual(200, jobs_negative_offset.status_code)
                    self.assertEqual(jobs_page_one.json()[0]["id"], jobs_negative_offset.json()[0]["id"])

                    job_response = client.get(f"/processing-jobs/{job.id}")
                    self.assertEqual(200, job_response.status_code)
                    self.assertEqual(job.id, job_response.json()["id"])
            finally:
                app.state.repository.close()

    def test_api_can_create_ingestion_from_base64_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)

            payload = {
                "case_code": "CASE-JSON-1",
                "title": "API ingestion",
                "filename": "input.txt",
                "content_base64": base64.b64encode(b"api-bytes").decode("ascii"),
                "mime_type": "text/plain",
                "source_type": "api",
            }

            try:
                with TestClient(app) as client:
                    response = client.post("/ingestions", json=payload)
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual("CASE-JSON-1", body["case_code"])
                    self.assertTrue((settings.storage_root / body["original_storage_key"]).exists())

                    case_detail = client.get(f"/cases/{body['case_id']}")
                    self.assertEqual(200, case_detail.status_code)
                    self.assertEqual(
                        "builtin",
                        json.loads(case_detail.json()["rag_entries"][0]["metadata_json"])["extraction_engine"],
                    )
                    self.assertEqual(
                        "text",
                        json.loads(case_detail.json()["rag_entries"][0]["metadata_json"])["extraction_source"],
                    )

                    document_detail = client.get(f"/documents/{body['document_id']}")
                    self.assertEqual(200, document_detail.status_code)
                    self.assertTrue(document_detail.json()["extraction"]["available"])
                    self.assertEqual("text", document_detail.json()["extraction"]["extraction_source"])
                    self.assertEqual("builtin", document_detail.json()["extraction"]["extraction_engine"])
                    self.assertEqual("auto", document_detail.json()["extraction"]["extraction_mode"])

                    jobs_response = client.get("/processing-jobs", params={"case_id": body["case_id"]})
                    self.assertEqual(200, jobs_response.status_code)
                    self.assertEqual(1, len(jobs_response.json()))
                    self.assertEqual("completed", jobs_response.json()[0]["job_status"])
            finally:
                app.state.repository.close()

    def test_api_can_ingest_chat_message_with_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)

            payload = {
                "case_code": "CASE-CHAT-1",
                "title": "Chat intake",
                "filename": "photo.png",
                "content_base64": base64.b64encode(b"chat-image-bytes").decode("ascii"),
                "mime_type": "image/png",
                "platform": "discord",
                "source_path": "guild-1/channel-2/message-3",
                "message_id": "message-3",
                "channel_id": "channel-2",
                "author_name": "Pakku",
                "message_text": "Please process this attachment.",
            }

            try:
                with TestClient(app) as client:
                    response = client.post("/chat-ingestions", json=payload)
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual("CASE-CHAT-1", body["case_code"])

                    document = app.state.repository.get_document(body["document_id"])
                    self.assertIsNotNone(document)
                    assert document is not None
                    self.assertEqual("discord", document.source_type)
                    self.assertEqual("guild-1/channel-2/message-3", document.source_path)

                    detail = client.get(f"/cases/{body['case_id']}")
                    self.assertEqual(200, detail.status_code)
                    artifact_types = {artifact["artifact_type"] for artifact in detail.json()["artifacts"]}
                    self.assertIn("structured_json", artifact_types)
            finally:
                app.state.repository.close()

    def test_api_can_ingest_chat_message_via_connector_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)

            discord_payload = {
                "case_code": "CASE-DISCORD-1",
                "title": "Discord connector intake",
                "filename": "discord.png",
                "content_base64": base64.b64encode(b"discord-bytes").decode("ascii"),
                "mime_type": "image/png",
                "guild_id": "guild-1",
                "channel_id": "channel-2",
                "message_id": "message-3",
                "author_name": "Pakku",
                "message_text": "Discord attachment received.",
            }
            line_payload = {
                "case_code": "CASE-LINE-1",
                "title": "LINE connector intake",
                "filename": "line.pdf",
                "content_base64": base64.b64encode(b"line-bytes").decode("ascii"),
                "mime_type": "application/pdf",
                "group_id": "group-7",
                "user_id": "user-8",
                "message_id": "message-9",
                "author_name": "Mika",
                "message_text": "LINE attachment received.",
            }

            try:
                with TestClient(app) as client:
                    discord_response = client.post("/connectors/discord/chat-ingestions", json=discord_payload)
                    self.assertEqual(200, discord_response.status_code)
                    discord_body = discord_response.json()
                    self.assertEqual("CASE-DISCORD-1", discord_body["case_code"])

                    discord_document = app.state.repository.get_document(discord_body["document_id"])
                    self.assertIsNotNone(discord_document)
                    assert discord_document is not None
                    self.assertEqual("discord", discord_document.source_type)
                    self.assertEqual("discord/guild/guild-1/channel/channel-2/message/message-3", discord_document.source_path)

                    discord_detail = client.get(f"/cases/{discord_body['case_id']}")
                    self.assertEqual(200, discord_detail.status_code)
                    discord_artifact_types = {artifact["artifact_type"] for artifact in discord_detail.json()["artifacts"]}
                    self.assertIn("structured_json", discord_artifact_types)

                    line_response = client.post("/connectors/line/chat-ingestions", json=line_payload)
                    self.assertEqual(200, line_response.status_code)
                    line_body = line_response.json()
                    self.assertEqual("CASE-LINE-1", line_body["case_code"])

                    line_document = app.state.repository.get_document(line_body["document_id"])
                    self.assertIsNotNone(line_document)
                    assert line_document is not None
                    self.assertEqual("line", line_document.source_type)
                    self.assertEqual("line/group/group-7/user/user-8/message/message-9", line_document.source_path)

                    line_detail = client.get(f"/cases/{line_body['case_id']}")
                    self.assertEqual(200, line_detail.status_code)
                    line_artifact_types = {artifact["artifact_type"] for artifact in line_detail.json()["artifacts"]}
                    self.assertIn("structured_json", line_artifact_types)
            finally:
                app.state.repository.close()

    def test_api_can_ingest_line_webhook_text_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "01HZY",
                        "source": {
                            "type": "user",
                            "userId": "U-line-user-1",
                        },
                        "replyToken": "reply-token-1",
                        "deliveryContext": {"isRedelivery": True},
                        "message": {
                            "id": "message-1",
                            "type": "text",
                            "quotedMessageId": "quoted-message-1",
                            "text": "CASE-LINE-WEBHOOK Please process this document.",
                        },
                    }
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(1, body["processed_count"])
                    self.assertEqual(1, body["ingested_count"])
                    self.assertEqual("ingested", body["items"][0]["status"])
                    self.assertEqual("CASE-LINE-WEBHOOK", body["items"][0]["case_code"])

                    document = app.state.repository.get_document(body["items"][0]["document_id"])
                    self.assertIsNotNone(document)
                    assert document is not None
                    self.assertEqual("line", document.source_type)
                    self.assertEqual("line/user/U-line-user-1/message/message-1", document.source_path)

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_ingested")
                    self.assertEqual(1, len(logs))
                    self.assertEqual(body["items"][0]["document_id"], logs[0].document_id)
                    metadata = json.loads(logs[0].metadata_json)
                    self.assertEqual("reply-token-1", metadata["reply_token"])
                    self.assertTrue(metadata["is_redelivery"])
                    self.assertEqual("quoted-message-1", metadata["quoted_message_id"])
                    self.assertIn("quotedMessageId quoted-message-1", metadata["event_summary"])
            finally:
                app.state.repository.close()

    def test_api_can_ingest_line_webhook_file_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                notification_line_channel_access_token="line-token",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "01HZZ",
                        "source": {
                            "type": "group",
                            "groupId": "G-line-group-1",
                            "userId": "U-line-user-2",
                        },
                        "message": {
                            "id": "file-1",
                            "type": "file",
                            "fileName": "CASE-LINE-FILE-1.pdf",
                        },
                    }
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual("/v2/bot/message/file-1/content", request.url.path)
                self.assertEqual("Bearer line-token", request.headers.get("Authorization"))
                return httpx.Response(200, content=b"file-bytes", headers={"Content-Type": "application/pdf"})

            app.state.line_webhook_client.transport = httpx.MockTransport(handler)

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(1, body["processed_count"])
                    self.assertEqual(1, body["ingested_count"])
                    self.assertEqual("ingested", body["items"][0]["status"])
                    self.assertEqual("CASE-LINE-FILE-1", body["items"][0]["case_code"])

                    document = app.state.repository.get_document(body["items"][0]["document_id"])
                    self.assertIsNotNone(document)
                    assert document is not None
                    self.assertEqual("line", document.source_type)
                    self.assertEqual("line/group/G-line-group-1/user/U-line-user-2/message/file-1", document.source_path)
                    self.assertEqual("CASE-LINE-FILE-1.pdf", document.filename)
                    self.assertTrue((settings.storage_root / document.storage_key).exists())

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_ingested")
                    self.assertEqual(1, len(logs))
                    self.assertEqual(body["items"][0]["document_id"], logs[0].document_id)
            finally:
                app.state.repository.close()

    def test_api_can_ingest_line_webhook_image_event_into_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                notification_line_channel_access_token="line-token",
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "01HZZ",
                        "source": {
                            "type": "user",
                            "userId": "U-line-user-3",
                        },
                        "message": {
                            "id": "image-1",
                            "type": "image",
                        },
                    }
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual("/v2/bot/message/image-1/content", request.url.path)
                return httpx.Response(200, content=b"image-bytes", headers={"Content-Type": "image/png"})

            app.state.line_webhook_client.transport = httpx.MockTransport(handler)

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(1, body["processed_count"])
                    self.assertEqual(1, body["ingested_count"])
                    self.assertEqual("ingested", body["items"][0]["status"])
                    self.assertEqual("LINE-INBOX", body["items"][0]["case_code"])

                    document = app.state.repository.get_document(body["items"][0]["document_id"])
                    self.assertIsNotNone(document)
                    assert document is not None
                    self.assertEqual("line", document.source_type)
                    self.assertEqual("line/user/U-line-user-3/message/image-1", document.source_path)

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_ingested")
                    self.assertEqual(1, len(logs))
                    self.assertEqual(body["items"][0]["document_id"], logs[0].document_id)
            finally:
                app.state.repository.close()

    def test_api_can_ingest_line_webhook_sticker_and_location_events_into_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "01HZZ-STICKER",
                        "source": {"type": "user", "userId": "U-line-user-4"},
                        "message": {
                            "id": "sticker-1",
                            "type": "sticker",
                            "stickerId": "1",
                            "packageId": "1",
                            "stickerResourceType": "STATIC",
                            "keywords": ["hello", "thanks"],
                        },
                    },
                    {
                        "type": "message",
                        "webhookEventId": "01HZZ-LOCATION",
                        "source": {"type": "user", "userId": "U-line-user-5"},
                        "message": {
                            "id": "location-1",
                            "type": "location",
                            "title": "Client office",
                            "address": "Tokyo",
                            "latitude": 35.681236,
                            "longitude": 139.767125,
                        },
                    },
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(2, body["processed_count"])
                    self.assertEqual(2, body["ingested_count"])
                    self.assertEqual(0, body["pending_count"])
                    self.assertEqual(0, body["skipped_count"])
                    self.assertCountEqual(["LINE-INBOX", "LINE-INBOX"], [item["case_code"] for item in body["items"]])

                    documents = app.state.repository.list_documents(source_type="line")
                    self.assertEqual(2, len(documents))
                    self.assertCountEqual(["line-sticker.txt", "line-location.txt"], [document.filename for document in documents])

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_ingested")
                    self.assertEqual(2, len(logs))
                    metadata = [json.loads(log.metadata_json) for log in logs]
                    self.assertCountEqual(["sticker", "location"], [item["message_type"] for item in metadata])
            finally:
                app.state.repository.close()

    def test_api_can_ingest_line_follow_event_into_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "follow",
                        "webhookEventId": "01HZZ-FOLLOW",
                        "source": {"type": "user", "userId": "U-line-user-7"},
                    }
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(1, body["processed_count"])
                    self.assertEqual(1, body["ingested_count"])
                    self.assertEqual("ingested", body["items"][0]["status"])
                    self.assertEqual("follow", body["items"][0]["event_type"])
                    self.assertEqual("LINE-INBOX", body["items"][0]["case_code"])

                    documents = app.state.repository.list_documents(source_type="line")
                    self.assertEqual(1, len(documents))
                    self.assertEqual("line-follow.json", documents[0].filename)
                    self.assertEqual("line/event/follow/01HZZ-FOLLOW", documents[0].source_path)

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_ingested")
                    self.assertEqual(1, len(logs))
                    metadata = json.loads(logs[0].metadata_json)
                    self.assertEqual("follow", metadata["event_type"])
            finally:
                app.state.repository.close()

    def test_api_can_ingest_line_join_and_leave_events_into_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "join",
                        "webhookEventId": "01HZZ-JOIN",
                        "source": {"type": "group", "groupId": "G-line-group-1"},
                    },
                    {
                        "type": "leave",
                        "webhookEventId": "01HZZ-LEAVE",
                        "source": {"type": "group", "groupId": "G-line-group-1"},
                    },
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(2, body["processed_count"])
                    self.assertEqual(2, body["ingested_count"])
                    self.assertEqual(0, body["pending_count"])
                    self.assertEqual(0, body["skipped_count"])
                    self.assertCountEqual(["join", "leave"], [item["event_type"] for item in body["items"]])

                    documents = app.state.repository.list_documents(source_type="line")
                    self.assertEqual(2, len(documents))
                    self.assertCountEqual(["line-join.json", "line-leave.json"], [document.filename for document in documents])

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_ingested")
                    self.assertEqual(2, len(logs))
                    metadata = [json.loads(log.metadata_json) for log in logs]
                    self.assertCountEqual(["join", "leave"], [item["event_type"] for item in metadata])
            finally:
                app.state.repository.close()

    def test_api_can_ingest_line_member_joined_and_left_events_into_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "memberJoined",
                        "webhookEventId": "01HZZ-MEMBER-JOINED",
                        "source": {"type": "group", "groupId": "G-line-group-2"},
                        "joined": {
                            "members": [
                                {"type": "user", "userId": "U-line-user-8"},
                            ]
                        },
                    },
                    {
                        "type": "memberLeft",
                        "webhookEventId": "01HZZ-MEMBER-LEFT",
                        "source": {"type": "group", "groupId": "G-line-group-2"},
                        "left": {
                            "members": [
                                {"type": "user", "userId": "U-line-user-9"},
                            ]
                        },
                    },
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(2, body["processed_count"])
                    self.assertEqual(2, body["ingested_count"])
                    self.assertEqual(0, body["pending_count"])
                    self.assertEqual(0, body["skipped_count"])
                    self.assertCountEqual(["memberJoined", "memberLeft"], [item["event_type"] for item in body["items"]])

                    documents = app.state.repository.list_documents(source_type="line")
                    self.assertEqual(2, len(documents))
                    self.assertCountEqual(
                        ["line-memberJoined.json", "line-memberLeft.json"],
                        [document.filename for document in documents],
                    )

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_ingested")
                    self.assertEqual(2, len(logs))
                    metadata = [json.loads(log.metadata_json) for log in logs]
                    self.assertCountEqual(["memberJoined", "memberLeft"], [item["event_type"] for item in metadata])
                    self.assertEqual(
                        {
                            "LINE memberJoined event from group G-line-group-2",
                            "LINE memberLeft event from group G-line-group-2",
                        },
                        {item["event_summary"] for item in metadata},
                    )
            finally:
                app.state.repository.close()

    def test_api_can_ingest_line_postback_and_beacon_events_into_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "postback",
                        "webhookEventId": "01HZZ-POSTBACK",
                        "source": {"type": "user", "userId": "U-line-user-10"},
                        "postback": {
                            "data": "CASE-LINE-POSTBACK status=ready",
                            "params": {"datetime": "2026-07-04T13:00:00+09:00"},
                        },
                    },
                    {
                        "type": "beacon",
                        "webhookEventId": "01HZZ-BEACON",
                        "source": {"type": "user", "userId": "U-line-user-11"},
                        "beacon": {
                            "hwid": "beacon-hwid-1",
                            "dm": "device-message",
                        },
                    },
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(2, body["processed_count"])
                    self.assertEqual(2, body["ingested_count"])
                    self.assertEqual(0, body["pending_count"])
                    self.assertEqual(0, body["skipped_count"])
                    self.assertCountEqual(["postback", "beacon"], [item["event_type"] for item in body["items"]])

                    documents = app.state.repository.list_documents(source_type="line")
                    self.assertEqual(2, len(documents))
                    self.assertCountEqual(["line-postback.json", "line-beacon.json"], [document.filename for document in documents])

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_ingested")
                    self.assertEqual(2, len(logs))
                    metadata = [json.loads(log.metadata_json) for log in logs]
                    self.assertCountEqual(["postback", "beacon"], [item["event_type"] for item in metadata])
                    self.assertIn(
                        "LINE postback event from user U-line-user-10 data CASE-LINE-POSTBACK status=ready params (datetime=2026-07-04T13:00:00+09:00)",
                        {item["event_summary"] for item in metadata},
                    )
                    self.assertIn(
                        "LINE beacon event from user U-line-user-11 hwid beacon-hwid-1 dm device-message",
                        {item["event_summary"] for item in metadata},
                    )
            finally:
                app.state.repository.close()

    def test_api_can_ingest_line_account_link_and_video_play_complete_events_into_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "accountLink",
                        "webhookEventId": "01HZZ-ACCOUNT-LINK",
                        "source": {"type": "user", "userId": "U-line-user-12"},
                        "link": {
                            "result": "ok",
                            "nonce": "nonce-1",
                        },
                    },
                    {
                        "type": "videoPlayComplete",
                        "webhookEventId": "01HZZ-VIDEO-PLAY-COMPLETE",
                        "source": {"type": "user", "userId": "U-line-user-13"},
                        "videoPlayComplete": {
                            "trackingId": "tracking-1",
                        },
                    },
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(2, body["processed_count"])
                    self.assertEqual(2, body["ingested_count"])
                    self.assertEqual(0, body["pending_count"])
                    self.assertEqual(0, body["skipped_count"])
                    self.assertCountEqual(["accountLink", "videoPlayComplete"], [item["event_type"] for item in body["items"]])

                    documents = app.state.repository.list_documents(source_type="line")
                    self.assertEqual(2, len(documents))
                    self.assertCountEqual(
                        ["line-accountLink.json", "line-videoPlayComplete.json"],
                        [document.filename for document in documents],
                    )

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_ingested")
                    self.assertEqual(2, len(logs))
                    metadata = [json.loads(log.metadata_json) for log in logs]
                    self.assertCountEqual(["accountLink", "videoPlayComplete"], [item["event_type"] for item in metadata])
                    self.assertEqual(
                        {
                            "LINE accountLink event from user U-line-user-12 result ok nonce nonce-1",
                            "LINE videoPlayComplete event from user U-line-user-13 trackingId tracking-1",
                        },
                        {item["event_summary"] for item in metadata},
                    )
            finally:
                app.state.repository.close()

    def test_api_can_ingest_line_unsend_event_into_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "unsend",
                        "webhookEventId": "01HZZ-UNSEND",
                        "source": {"type": "user", "userId": "U-line-user-14"},
                        "unsend": {
                            "messageId": "message-removed-1",
                        },
                    }
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(1, body["processed_count"])
                    self.assertEqual(1, body["ingested_count"])
                    self.assertEqual(0, body["pending_count"])
                    self.assertEqual(0, body["skipped_count"])
                    self.assertEqual("unsend", body["items"][0]["event_type"])
                    self.assertEqual("LINE-INBOX", body["items"][0]["case_code"])

                    documents = app.state.repository.list_documents(source_type="line")
                    self.assertEqual(1, len(documents))
                    self.assertEqual("line-unsend.json", documents[0].filename)
                    self.assertEqual("line/event/unsend/01HZZ-UNSEND", documents[0].source_path)

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_ingested")
                    self.assertEqual(1, len(logs))
                    metadata = json.loads(logs[0].metadata_json)
                    self.assertEqual("unsend", metadata["event_type"])
                    self.assertEqual("message-removed-1", metadata["unsend_message_id"])
                    self.assertIn("message message-removed-1", metadata["event_summary"])
            finally:
                app.state.repository.close()

    def test_api_rejects_invalid_line_webhook_signature_with_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret="line-secret",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": "invalid-signature",
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(400, response.status_code)
                    self.assertIn("Invalid LINE webhook signature", response.text)

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_signature_invalid")
                    self.assertEqual(1, len(logs))
                    self.assertIn("invalid signature", logs[0].message.lower())
            finally:
                app.state.repository.close()

    def test_api_marks_line_video_as_pending_while_transcoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                notification_line_channel_access_token="line-token",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "01HZZ",
                        "contentProvider": {"type": "line"},
                        "source": {"type": "user", "userId": "U-line-user-9"},
                        "message": {
                            "id": "video-1",
                            "type": "video",
                            "fileName": "CASE-LINE-VIDEO-1.mp4",
                        },
                    }
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            async def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/content/transcoding"):
                    return httpx.Response(200, json={"status": "processing"})
                raise AssertionError(f"Unexpected request path: {request.url.path}")

            app.state.line_webhook_client.transport = httpx.MockTransport(handler)

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(1, body["processed_count"])
                    self.assertEqual(0, body["ingested_count"])
                    self.assertEqual(1, body["pending_count"])
                    self.assertEqual(0, body["skipped_count"])
                    self.assertEqual("pending", body["items"][0]["status"])
                    self.assertEqual("content_processing", body["items"][0]["reason"])
                    self.assertEqual("CASE-LINE-VIDEO-1", body["items"][0]["case_code"])

                    logs = app.state.repository.list_operation_logs(event_type="line_webhook_pending")
                    self.assertEqual(1, len(logs))
                    self.assertEqual("content_processing", logs[0].metadata_json and json.loads(logs[0].metadata_json)["reason"])
                    self.assertEqual("video-1", json.loads(logs[0].metadata_json)["event_json"]["message"]["id"])
            finally:
                app.state.repository.close()

    def test_api_can_retry_pending_line_webhook_after_transcoding_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                notification_line_channel_access_token="line-token",
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "01HZZ",
                        "contentProvider": {"type": "line"},
                        "source": {"type": "user", "userId": "U-line-user-9"},
                        "message": {
                            "id": "video-1",
                            "type": "video",
                            "fileName": "CASE-LINE-VIDEO-RETRY-1.mp4",
                        },
                    }
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            state = {"transcoding_status": "processing"}

            async def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/content/transcoding"):
                    return httpx.Response(200, json={"status": state["transcoding_status"]})
                if request.url.path.endswith("/content"):
                    self.assertEqual("/v2/bot/message/video-1/content", request.url.path)
                    return httpx.Response(200, content=b"video-bytes", headers={"Content-Type": "video/mp4"})
                raise AssertionError(f"Unexpected request path: {request.url.path}")

            app.state.line_webhook_client.transport = httpx.MockTransport(handler)

            try:
                with TestClient(app) as client:
                    pending_response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, pending_response.status_code)
                    pending_body = pending_response.json()
                    self.assertEqual(1, pending_body["pending_count"])
                    self.assertEqual("pending", pending_body["items"][0]["status"])

                    pending_logs = app.state.repository.list_operation_logs(event_type="line_webhook_pending")
                    self.assertEqual(1, len(pending_logs))
                    pending_log_id = pending_logs[0].id
                    pending_metadata = json.loads(pending_logs[0].metadata_json)
                    self.assertEqual("video-1", pending_metadata["event_json"]["message"]["id"])

                    state["transcoding_status"] = "succeeded"
                    retry_response = client.post("/line-webhooks/retry-pending", params={"limit": 10})
                    self.assertEqual(200, retry_response.status_code)
                    retry_body = retry_response.json()
                    self.assertEqual(1, retry_body["processed_count"])
                    self.assertEqual(1, retry_body["retried_count"])
                    self.assertEqual(1, retry_body["ingested_count"])
                    self.assertEqual(0, retry_body["pending_count"])
                    self.assertEqual(0, retry_body["skipped_count"])
                    self.assertEqual("ingested", retry_body["items"][0]["status"])
                    self.assertEqual(pending_log_id, retry_body["items"][0]["source_log_id"])
                    self.assertEqual("CASE-LINE-VIDEO-RETRY-1", retry_body["items"][0]["case_code"])

                    documents = app.state.repository.list_documents(case_id=retry_body["items"][0]["case_id"], source_type="line")
                    self.assertEqual(1, len(documents))
                    self.assertEqual("line", documents[0].source_type)
                    self.assertEqual("line/user/U-line-user-9/message/video-1", documents[0].source_path)

                    retry_logs = app.state.repository.list_operation_logs(event_type="line_webhook_retry_ingested")
                    self.assertEqual(1, len(retry_logs))
                    retry_metadata = json.loads(retry_logs[0].metadata_json)
                    self.assertEqual(pending_log_id, retry_metadata["retry_of_log_id"])
                    self.assertTrue(retry_metadata["retry"])
            finally:
                app.state.repository.close()

    def test_api_lists_pending_line_webhooks_with_original_event_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                notification_line_channel_access_token="line-token",
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "01HZZ",
                        "contentProvider": {"type": "line"},
                        "source": {"type": "user", "userId": "U-line-user-9"},
                        "message": {
                            "id": "video-1",
                            "type": "video",
                            "fileName": "CASE-LINE-PENDING-1.mp4",
                        },
                    }
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            async def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/content/transcoding"):
                    return httpx.Response(200, json={"status": "processing"})
                raise AssertionError(f"Unexpected request path: {request.url.path}")

            app.state.line_webhook_client.transport = httpx.MockTransport(handler)

            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, response.status_code)

                    pending_response = client.get("/line-webhooks/pending")
                    self.assertEqual(200, pending_response.status_code)
                    self.assertEqual("1", pending_response.headers.get("X-Total-Count"))
                    body = pending_response.json()
                    self.assertEqual(1, body["total"])
                    self.assertEqual(1, len(body["items"]))
                    self.assertEqual("CASE-LINE-PENDING-1", body["items"][0]["case_code"])
                    self.assertEqual("content_processing", body["items"][0]["reason"])
                    self.assertEqual("video-1", body["items"][0]["event_json"]["message"]["id"])
            finally:
                app.state.repository.close()

    def test_api_reports_line_webhook_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            success_payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "01HZZ",
                        "source": {"type": "user", "userId": "U-line-user-1"},
                        "message": {
                            "id": "message-1",
                            "type": "text",
                            "text": "CASE-LINE-REPORT Please process this document.",
                        },
                    }
                ],
            }
            success_raw_body = json.dumps(success_payload, ensure_ascii=False).encode("utf-8")
            success_signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), success_raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            try:
                with TestClient(app) as client:
                    success_response = client.post(
                        "/connectors/line/webhook",
                        content=success_raw_body,
                        headers={
                            "X-Line-Signature": success_signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, success_response.status_code)

                    failure_response = client.post(
                        "/connectors/line/webhook",
                        content=success_raw_body,
                        headers={
                            "X-Line-Signature": "invalid-signature",
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(400, failure_response.status_code)

                    report = client.get("/line-webhooks/report")
                    self.assertEqual(200, report.status_code)
                    body = report.json()
                    self.assertEqual(2, body["summary"]["total"])
                    self.assertEqual(1, body["summary"]["ingested_total"])
                    self.assertEqual(0, body["summary"]["skipped_total"])
                    self.assertEqual(1, body["summary"]["signature_invalid_total"])
                    self.assertEqual(0, body["summary"]["pending_backlog_count"])
                    self.assertIsNone(body["pending_backlog_latest"])
                    self.assertGreaterEqual(len(body["recent_events"]), 1)
            finally:
                app.state.repository.close()

    def test_api_lists_line_webhook_activity_with_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                notification_line_channel_access_token="line-token",
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "follow",
                        "webhookEventId": "01HZZ-FOLLOW-ACTIVITY",
                        "source": {"type": "user", "userId": "U-line-user-20"},
                    },
                    {
                        "type": "message",
                        "webhookEventId": "01HZZ-PENDING-ACTIVITY",
                        "contentProvider": {"type": "line"},
                        "source": {"type": "user", "userId": "U-line-user-21"},
                        "replyToken": "reply-token-activity-1",
                        "deliveryContext": {"isRedelivery": True},
                        "message": {
                            "id": "video-activity-1",
                            "type": "video",
                            "fileName": "CASE-LINE-ACTIVITY-1.mp4",
                            "quotedMessageId": "quoted-message-activity-1",
                        },
                    },
                    {
                        "type": "postback",
                        "webhookEventId": "01HZZ-POSTBACK-ACTIVITY",
                        "source": {"type": "user", "userId": "U-line-user-22"},
                        "postback": {"data": "CASE-LINE-ACTIVITY-ACTION"},
                    },
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            async def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/content/transcoding"):
                    return httpx.Response(200, json={"status": "processing"})
                if request.url.path.endswith("/content"):
                    return httpx.Response(200, content=b"video-bytes", headers={"Content-Type": "video/mp4"})
                raise AssertionError(f"Unexpected request path: {request.url.path}")

            app.state.line_webhook_client.transport = httpx.MockTransport(handler)

            try:
                with TestClient(app) as client:
                    webhook_response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, webhook_response.status_code)

                    activity_response = client.get("/line-webhooks/activity")
                    self.assertEqual(200, activity_response.status_code)
                    self.assertEqual("3", activity_response.headers.get("X-Total-Count"))
                    activity_body = activity_response.json()
                    self.assertEqual(3, len(activity_body))
                    self.assertCountEqual(["follow", "postback", "message"], [item["line_event_type"] for item in activity_body])
                    message_activity = next(item for item in activity_body if item["line_event_type"] == "message")
                    self.assertEqual("reply-token-activity-1", message_activity["reply_token"])
                    self.assertTrue(message_activity["is_redelivery"])
                    self.assertEqual("quoted-message-activity-1", message_activity["quoted_message_id"])

                    pending_response = client.get("/line-webhooks/pending")
                    self.assertEqual(200, pending_response.status_code)
                    self.assertEqual("1", pending_response.headers.get("X-Total-Count"))
                    pending_body = pending_response.json()
                    self.assertEqual(1, pending_body["total"])
                    self.assertEqual(1, len(pending_body["items"]))
                    self.assertEqual("reply-token-activity-1", pending_body["items"][0]["reply_token"])
                    self.assertTrue(pending_body["items"][0]["is_redelivery"])
                    self.assertEqual("video-activity-1", pending_body["items"][0]["event_json"]["message"]["id"])

                    filtered_follow = client.get("/line-webhooks/activity", params={"line_event_type": "follow"})
                    self.assertEqual(200, filtered_follow.status_code)
                    self.assertEqual("1", filtered_follow.headers.get("X-Total-Count"))
                    self.assertEqual(1, len(filtered_follow.json()))
                    self.assertEqual("follow", filtered_follow.json()[0]["line_event_type"])

                    filtered_pending = client.get("/line-webhooks/activity", params={"operation_event_type": "line_webhook_pending"})
                    self.assertEqual(200, filtered_pending.status_code)
                    self.assertEqual("1", filtered_pending.headers.get("X-Total-Count"))
                    self.assertEqual(1, len(filtered_pending.json()))
                    self.assertEqual("line_webhook_pending", filtered_pending.json()[0]["operation_event_type"])
            finally:
                app.state.repository.close()

    def test_api_reports_line_webhook_attention_when_backlog_exceeds_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                notification_line_channel_access_token="line-token",
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            ready_payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "follow",
                        "webhookEventId": "01HZZ-FOLLOW-ATTN",
                        "source": {"type": "user", "userId": "U-line-user-30"},
                    }
                ],
            }
            ready_raw_body = json.dumps(ready_payload, ensure_ascii=False).encode("utf-8")
            ready_signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), ready_raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            pending_payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "01HZZ-PENDING-ATTN",
                        "contentProvider": {"type": "line"},
                        "source": {"type": "user", "userId": "U-line-user-31"},
                        "replyToken": "reply-token-attn-1",
                        "deliveryContext": {"isRedelivery": True},
                        "message": {
                            "id": "video-attn-1",
                            "type": "video",
                            "fileName": "CASE-LINE-ATTN-VIDEO.mp4",
                            "quotedMessageId": "quoted-message-attn-1",
                        },
                    }
                ],
            }
            pending_raw_body = json.dumps(pending_payload, ensure_ascii=False).encode("utf-8")
            pending_signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), pending_raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            async def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/content/transcoding"):
                    return httpx.Response(200, json={"status": "processing"})
                raise AssertionError(f"Unexpected request path: {request.url.path}")

            app.state.line_webhook_client.transport = httpx.MockTransport(handler)

            try:
                with TestClient(app) as client:
                    ready_response = client.post(
                        "/connectors/line/webhook",
                        content=ready_raw_body,
                        headers={
                            "X-Line-Signature": ready_signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, ready_response.status_code)

                    report_response = client.get("/line-webhooks/report", params={"pending_backlog_threshold": 1})
                    self.assertEqual(200, report_response.status_code)
                    report_body = report_response.json()
                    self.assertEqual(0, report_body["summary"]["pending_backlog_count"])
                    self.assertFalse(report_body["summary"]["needs_attention"])
                    self.assertIsNone(report_body["summary"]["attention_reason"])
                    self.assertIsNone(report_body["pending_backlog_latest_summary"])

                    pending_response = client.post(
                        "/connectors/line/webhook",
                        content=pending_raw_body,
                        headers={
                            "X-Line-Signature": pending_signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, pending_response.status_code)

                    alert_response = client.get("/line-webhooks/report", params={"pending_backlog_threshold": 1})
                    self.assertEqual(200, alert_response.status_code)
                    alert_body = alert_response.json()
                    self.assertEqual(1, alert_body["summary"]["pending_backlog_count"])
                    self.assertTrue(alert_body["summary"]["needs_attention"])
                    self.assertIn("threshold", alert_body["summary"]["attention_reason"])
                    self.assertEqual("reply-token-attn-1", alert_body["pending_backlog_latest_summary"]["reply_token"])
                    self.assertTrue(alert_body["pending_backlog_latest_summary"]["is_redelivery"])
                    self.assertEqual("quoted-message-attn-1", alert_body["pending_backlog_latest_summary"]["quoted_message_id"])
            finally:
                app.state.repository.close()

    def test_api_exposes_line_webhook_alerts_and_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line_secret = "line-secret"
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
                line_channel_secret=line_secret,
                notification_line_channel_access_token="line-token",
                line_inbox_case_code="LINE-INBOX",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.line_webhook_client.settings = settings
            app.state.line_webhook_client.ingestion_service = app.state.ingestion_service

            payload = {
                "destination": "U1234567890",
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "01HZZ-ALERT",
                        "contentProvider": {"type": "line"},
                        "source": {"type": "user", "userId": "U-line-user-40"},
                        "replyToken": "reply-token-1",
                        "deliveryContext": {"isRedelivery": True},
                        "message": {
                            "id": "video-alert-1",
                            "type": "video",
                            "fileName": "CASE-LINE-ALERT-1.mp4",
                            "quotedMessageId": "quoted-message-1",
                        },
                    }
                ],
            }
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = base64.b64encode(
                hmac.new(line_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("ascii")

            async def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/content/transcoding"):
                    return httpx.Response(200, json={"status": "processing"})
                raise AssertionError(f"Unexpected request path: {request.url.path}")

            app.state.line_webhook_client.transport = httpx.MockTransport(handler)

            try:
                with TestClient(app) as client:
                    webhook_response = client.post(
                        "/connectors/line/webhook",
                        content=raw_body,
                        headers={
                            "X-Line-Signature": signature,
                            "Content-Type": "application/json",
                        },
                    )
                    self.assertEqual(200, webhook_response.status_code)
                    alerts_response = client.get("/line-webhooks/alerts", params={"pending_backlog_threshold": 1})
                    self.assertEqual(200, alerts_response.status_code)
                    alerts_body = alerts_response.json()
                    self.assertEqual(1, alerts_body["alert_total"])
                    self.assertTrue(alerts_body["needs_attention"])
                    self.assertEqual(1, len(alerts_body["alerts"]))
                    self.assertEqual("pending_backlog", alerts_body["alerts"][0]["alert_type"])
                    self.assertEqual("reply-token-1", alerts_body["alerts"][0]["latest_pending_summary"]["reply_token"])
                    self.assertEqual("quoted-message-1", alerts_body["alerts"][0]["latest_pending_summary"]["quoted_message_id"])

                    alerts_markdown = client.get("/line-webhooks/alerts.md", params={"pending_backlog_threshold": 1})
                    self.assertEqual(200, alerts_markdown.status_code)
                    self.assertIn("# O's flow LINE Webhook Alerts", alerts_markdown.text)
                    self.assertIn("pending backlog count: 1", alerts_markdown.text)
                    self.assertIn("pending_backlog", alerts_markdown.text)
                    self.assertIn("latest pending reply_token: reply-token-1", alerts_markdown.text)
                    self.assertIn("latest pending quoted_message_id: quoted-message-1", alerts_markdown.text)

                    markdown_response = client.get("/line-webhooks/report.md", params={"pending_backlog_threshold": 1})
                    self.assertEqual(200, markdown_response.status_code)
                    self.assertIn("# O's flow LINE Webhook Report", markdown_response.text)
                    self.assertIn("pending backlog count: 1", markdown_response.text)
                    self.assertIn("Latest Pending", markdown_response.text)
                    self.assertIn("reply_token: reply-token-1", markdown_response.text)
                    self.assertIn("quoted_message_id: quoted-message-1", markdown_response.text)
            finally:
                app.state.repository.close()

    def test_api_can_upload_file_via_multipart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)

            data = {
                "case_code": "CASE-MULTI-1",
                "title": "Multipart upload",
                "source_type": "api",
                "mime_type": "text/plain",
            }
            files = {
                "file": ("note.txt", b"multipart extracted text", "text/plain"),
            }

            try:
                with TestClient(app) as client:
                    response = client.post("/ingestions/upload", data=data, files=files)
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual("CASE-MULTI-1", body["case_code"])
                    self.assertTrue((settings.storage_root / body["original_storage_key"]).exists())
                    jobs_response = client.get("/processing-jobs", params={"case_id": body["case_id"]})
                    self.assertEqual(200, jobs_response.status_code)
                    self.assertEqual(1, len(jobs_response.json()))
                    self.assertEqual("completed", jobs_response.json()[0]["job_status"])
            finally:
                app.state.repository.close()

    def test_api_rejects_invalid_base64_ingestion_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)

            payload = {
                "case_code": "CASE-JSON-ERR",
                "title": "API ingestion",
                "filename": "input.pdf",
                "content_base64": "not-base64!!",
            }

            try:
                with TestClient(app) as client:
                    response = client.post("/ingestions", json=payload)
                    self.assertEqual(400, response.status_code)
            finally:
                app.state.repository.close()

    def test_api_rejects_invalid_multipart_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)

            data = {
                "case_code": "CASE-MULTI-ERR",
                "title": "Multipart upload",
                "structured_json": "{not-json}",
            }
            files = {
                "file": ("input.pdf", b"multipart-bytes", "application/pdf"),
            }

            try:
                with TestClient(app) as client:
                    response = client.post("/ingestions/upload", data=data, files=files)
                    self.assertEqual(400, response.status_code)
            finally:
                app.state.repository.close()

    def test_api_can_patch_case_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "data" / "app.db"
            with SQLiteRepository(database_path) as repo:
                case = repo.upsert_case(
                    case_code="CASE-PATCH-1",
                    title="Original title",
                    client_name="Original client",
                    status="new",
                    due_date="2026-07-10",
                    invoice_status="unbilled",
                    output_status="pending",
                )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(database_path)
            try:
                with TestClient(app) as client:
                    response = client.patch(
                        f"/cases/{case.id}",
                        json={
                            "title": "Updated title",
                            "status": "in_progress",
                            "due_date": "2026-08-01",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual("Updated title", body["title"])
                    self.assertEqual("in_progress", body["status"])
                    self.assertEqual("2026-08-01", body["due_date"])

                    case_response = client.get(f"/cases/{case.id}")
                    self.assertEqual(200, case_response.status_code)
                    self.assertEqual("Updated title", case_response.json()["case"]["title"])

                    due_response = client.get("/tasks/due", params={"until_date": "2026-07-31", "status": "in_progress"})
                    self.assertEqual(0, len(due_response.json()))
            finally:
                app.state.repository.close()

    def test_api_can_create_or_update_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "data" / "app.db"

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(database_path)
            try:
                with TestClient(app) as client:
                    create_response = client.post(
                        "/cases",
                        json={
                            "case_code": "CASE-POST-1",
                            "title": "Created from API",
                            "client_name": "Client Post",
                            "status": "new",
                            "due_date": "2026-08-01",
                        },
                    )
                    self.assertEqual(200, create_response.status_code)
                    create_body = create_response.json()
                    self.assertEqual("CASE-POST-1", create_body["case_code"])
                    self.assertEqual("Created from API", create_body["title"])

                    update_response = client.post(
                        "/cases",
                        json={
                            "case_code": "CASE-POST-1",
                            "title": "Updated by API",
                            "client_name": "Client Post",
                            "status": "in_progress",
                            "due_date": "2026-08-15",
                        },
                    )
                    self.assertEqual(200, update_response.status_code)
                    update_body = update_response.json()
                    self.assertEqual(create_body["id"], update_body["id"])
                    self.assertEqual("Updated by API", update_body["title"])
                    self.assertEqual("in_progress", update_body["status"])

                    activity_response = client.get(f"/cases/{create_body['id']}/activity")
                    self.assertEqual(200, activity_response.status_code)
                    self.assertEqual("2", activity_response.headers.get("X-Total-Count"))
                    activity_types = [item["event_type"] for item in activity_response.json()]
                    self.assertIn("case_created", activity_types)
                    self.assertIn("case_updated", activity_types)

                    global_logs_response = client.get("/operation-logs", params={"case_id": create_body["id"]})
                    self.assertEqual(200, global_logs_response.status_code)
                    self.assertEqual("2", global_logs_response.headers.get("X-Total-Count"))
                    self.assertEqual(
                        activity_types,
                        [item["event_type"] for item in global_logs_response.json()],
                    )

                    offset_logs_response = client.get(
                        "/operation-logs",
                        params={"case_id": create_body["id"], "limit": 1, "offset": 1},
                    )
                    self.assertEqual(200, offset_logs_response.status_code)
                    self.assertEqual(1, len(offset_logs_response.json()))
                    self.assertEqual("case_created", offset_logs_response.json()[0]["event_type"])
            finally:
                app.state.repository.close()

    def test_api_can_bulk_update_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "data" / "app.db"
            with SQLiteRepository(database_path) as repo:
                case_one = repo.upsert_case(case_code="CASE-BULK-1", title="Bulk one", status="new")
                case_two = repo.upsert_case(case_code="CASE-BULK-2", title="Bulk two", status="new")

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(database_path)
            try:
                with TestClient(app) as client:
                    response = client.patch(
                        "/cases/bulk",
                        json={
                            "case_ids": [case_one.id, case_two.id],
                            "status": "in_progress",
                            "invoice_status": "pending",
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    bodies = response.json()
                    self.assertEqual(2, len(bodies))
                    self.assertEqual({"in_progress"}, {item["status"] for item in bodies})
                    self.assertEqual({"pending"}, {item["invoice_status"] for item in bodies})

                    activity_one = client.get(f"/cases/{case_one.id}/activity")
                    self.assertEqual("1", activity_one.headers.get("X-Total-Count"))
                    self.assertIn("case_bulk_updated", [item["event_type"] for item in activity_one.json()])
                    activity_two = client.get(f"/cases/{case_two.id}/activity")
                    self.assertEqual("1", activity_two.headers.get("X-Total-Count"))
                    self.assertIn("case_bulk_updated", [item["event_type"] for item in activity_two.json()])
            finally:
                app.state.repository.close()

    def test_api_can_search_documents_by_filename_and_storage_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.document_service = DocumentService(settings, app.state.repository, app.state.storage)

            payload = {
                "case_code": "CASE-DOC-SEARCH",
                "title": "Document search",
                "filename": "invoice-summary.txt",
                "content_base64": base64.b64encode(b"search me").decode("ascii"),
                "mime_type": "text/plain",
                "source_type": "api",
            }
            payload_two = {
                "case_code": "CASE-DOC-SEARCH",
                "title": "Document search 2",
                "filename": "notes.txt",
                "content_base64": base64.b64encode(b"search me too").decode("ascii"),
                "mime_type": "text/plain",
                "source_type": "api",
            }

            try:
                with TestClient(app) as client:
                    first = client.post("/ingestions", json=payload)
                    second = client.post("/ingestions", json=payload_two)
                    self.assertEqual(200, first.status_code)
                    self.assertEqual(200, second.status_code)

                    filename_search = client.get("/documents", params={"query": "invoice-summary"})
                    self.assertEqual(200, filename_search.status_code)
                    self.assertEqual(1, len(filename_search.json()))

                    storage_search = client.get(
                        "/documents",
                        params={"query": first.json()["original_storage_key"].split("/")[2]},
                    )
                    self.assertEqual(200, storage_search.status_code)
                    self.assertGreaterEqual(len(storage_search.json()), 1)

                    first_page = client.get("/documents", params={"case_id": first.json()["case_id"], "limit": 1})
                    self.assertEqual(200, first_page.status_code)
                    offset_search = client.get(
                        "/documents",
                        params={"case_id": first.json()["case_id"], "limit": 1, "offset": 1},
                    )
                    self.assertEqual(200, offset_search.status_code)
                    self.assertEqual(1, len(offset_search.json()))
                    self.assertNotEqual(first_page.json()[0]["id"], offset_search.json()[0]["id"])
            finally:
                app.state.repository.close()

    def test_api_can_reassign_document_between_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            with SQLiteRepository(settings.database_path) as repo:
                source_case = repo.upsert_case(
                    case_code="CASE-MOVE-SRC",
                    title="Source case",
                    client_name="Source client",
                    status="in_progress",
                    due_date="2026-07-30",
                    invoice_status="pending",
                    output_status="pending",
                )
                target_case = repo.upsert_case(
                    case_code="CASE-MOVE-TGT",
                    title="Target case",
                    client_name="Target client",
                    status="new",
                    due_date="2026-08-30",
                    invoice_status="unbilled",
                    output_status="pending",
                )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.document_service = DocumentService(settings, app.state.repository, app.state.storage)

            ingest_payload = {
                "case_code": "CASE-MOVE-SRC",
                "title": "Move source",
                "filename": "move.txt",
                "content_base64": base64.b64encode(b"move me").decode("ascii"),
                "mime_type": "text/plain",
                "source_type": "api",
                "extracted_text": "move me",
                "structured_json": {"kind": "move"},
                "output_html": "<html>move me</html>",
            }

            try:
                with TestClient(app) as client:
                    ingest_response = client.post("/ingestions", json=ingest_payload)
                    self.assertEqual(200, ingest_response.status_code)
                    ingest_body = ingest_response.json()

                    case_detail = client.get(f"/cases/{source_case.id}")
                    self.assertEqual(200, case_detail.status_code)
                    artifact_keys = [
                        artifact["storage_key"]
                        for artifact in case_detail.json()["artifacts"]
                        if artifact["document_id"] == ingest_body["document_id"]
                    ]

                    rag_key = f"rag/{source_case.case_code}/{ingest_body['document_id']}.json"
                    self.assertTrue((settings.storage_root / ingest_body["original_storage_key"]).exists())
                    self.assertTrue((settings.storage_root / rag_key).exists())

                    move_response = client.post(
                        f"/documents/{ingest_body['document_id']}/reassign",
                        json={"target_case_id": target_case.id},
                    )
                    self.assertEqual(200, move_response.status_code)
                    move_body = move_response.json()
                    self.assertEqual(source_case.id, move_body["previous_case_id"])
                    self.assertEqual(target_case.id, move_body["new_case_id"])

                    self.assertFalse((settings.storage_root / ingest_body["original_storage_key"]).exists())
                    self.assertTrue((settings.storage_root / ingest_body["original_storage_key"].replace(source_case.case_code, target_case.case_code)).exists())
                    self.assertFalse((settings.storage_root / rag_key).exists())
                    self.assertTrue((settings.storage_root / rag_key.replace(source_case.case_code, target_case.case_code)).exists())
                    for old_key in artifact_keys:
                        self.assertFalse((settings.storage_root / old_key).exists())
                        self.assertTrue((settings.storage_root / old_key.replace(source_case.case_code, target_case.case_code)).exists())

                    source_detail = client.get(f"/cases/{source_case.id}")
                    self.assertEqual(0, len(source_detail.json()["documents"]))

                    target_detail = client.get(f"/cases/{target_case.id}")
                    self.assertEqual(1, len(target_detail.json()["documents"]))
                    self.assertEqual(target_case.id, target_detail.json()["documents"][0]["case_id"])

                    rag_response = client.get("/rag/search", params={"query": "move", "case_id": target_case.id})
                    self.assertEqual(1, len(rag_response.json()))

                    jobs_response = client.get("/processing-jobs", params={"case_id": target_case.id})
                    self.assertEqual(2, len(jobs_response.json()))
                    self.assertEqual(
                        {"ingestion", "document_reassign"},
                        {job["job_type"] for job in jobs_response.json()},
                    )

                    activity_response = client.get(f"/cases/{target_case.id}/activity")
                    self.assertEqual(200, activity_response.status_code)
                    self.assertIn(
                        "document_reassigned",
                        {item["event_type"] for item in activity_response.json()},
                    )

                    global_logs_response = client.get("/operation-logs", params={"document_id": ingest_body["document_id"]})
                    self.assertEqual(200, global_logs_response.status_code)
                    self.assertIn(
                        "document_reassigned",
                        {item["event_type"] for item in global_logs_response.json()},
                    )

                    document_activity = client.get(f"/documents/{ingest_body['document_id']}/activity")
                    self.assertEqual(200, document_activity.status_code)
                    self.assertIn(
                        "document_reassigned",
                        {item["event_type"] for item in document_activity.json()},
                    )
            finally:
                app.state.repository.close()


    def test_api_reports_admin_overview_with_insforge_placeholder_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "APP_ENV": "test",
                "DATABASE_PATH": str(root / "data" / "app.db"),
                "STORAGE_ROOT": str(root / "storage"),
                "OUTPUT_ROOT": str(root / "output"),
                "TEMP_ROOT": str(root / "temp"),
                "RAG_ROOT": str(root / "storage" / "rag"),
                "DISCORD_BOT_TOKEN": "",
                "DISCORD_TARGET_CHANNEL_IDS": "123",
                "AI_PROVIDER": "openai_compatible",
                "AI_API_KEY": "",
                "AI_MODEL": "gpt-4.1",
                "AI_BASE_URL": "https://api.openai.com/v1",
                "INSFORGE_BASE_URL": "https://example.insforge.invalid",
                "INSFORGE_API_KEY": "insforge-key",
                "INSFORGE_DATABASE_URL": "postgresql://example",
                "INSFORGE_PROJECT_ID": "project-123",
                "INSFORGE_STORAGE_BUCKET": "bucket-1",
                "INSFORGE_STORAGE_NAMESPACE": "namespace-1",
                "INSFORGE_AUTH_JWKS_URL": "https://example.insforge.invalid/.well-known/jwks.json",
                "INSFORGE_MCP_BASE_URL": "https://example.insforge.invalid/mcp",
            }

            with patch("app.config.load_dotenv", autospec=True, return_value=None):
                with patch.dict(os.environ, env, clear=True):
                    app = create_app()

            app.state.repository.close()
            app.state.repository = SQLiteRepository(Path(env["DATABASE_PATH"]))

            try:
                with TestClient(app) as client:
                    response = client.get("/admin/overview")
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual("test", body["settings"]["app_env"])
                    self.assertEqual("sqlite", body["settings"]["repository_backend"])
                    self.assertEqual("local", body["settings"]["storage_backend"])
                    self.assertTrue(body["settings"]["insforge"]["base_url_configured"])
                    self.assertTrue(body["settings"]["insforge"]["api_key_configured"])
                    self.assertTrue(body["settings"]["insforge"]["database_url_configured"])
                    self.assertTrue(body["settings"]["insforge"]["project_id_configured"])
                    self.assertTrue(body["settings"]["insforge"]["storage_bucket_configured"])
                    self.assertTrue(body["settings"]["insforge"]["storage_namespace_configured"])
                    self.assertTrue(body["settings"]["insforge"]["auth_jwks_url_configured"])
                    self.assertTrue(body["settings"]["insforge"]["mcp_base_url_configured"])
                    self.assertEqual(0, body["summary"]["cases_total"])
                    self.assertEqual(0, body["summary"]["documents_total"])
                    self.assertEqual(0, body["breakdown"]["case_statuses"]["new"])
                    self.assertEqual(0, body["breakdown"]["invoice_statuses"]["pending"])
                    self.assertEqual(0, body["breakdown"]["output_statuses"]["pending"])
                    self.assertEqual(0, body["breakdown"]["document_source_types"]["line"])
            finally:
                app.state.repository.close()


class ExtractionTests(unittest.TestCase):
    def test_extracts_text_from_docx_bytes(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Hello</w:t></w:r></w:p>
    <w:p><w:r><w:t>World</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", xml)

        text = extract_text("sample.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual("Hello\nWorld", text)

    def test_extract_text_details_reports_builtin_source_for_plain_text(self) -> None:
        from app.services.extraction import extract_text_details

        details = extract_text_details("sample.txt", b"hello world", "text/plain")

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual("hello world", details.text)
        self.assertEqual("text", details.source_type)
        self.assertEqual("builtin", details.engine)

    def test_extract_text_details_handles_xlsx_workbooks(self) -> None:
        from app.services.extraction import extract_text_details

        workbook = io.BytesIO()
        with zipfile.ZipFile(workbook, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "xl/workbook.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                    <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
                </workbook>""",
            )
            archive.writestr(
                "xl/sharedStrings.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">
                    <si><t>Hello</t></si>
                    <si><t>World</t></si>
                </sst>""",
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                    <sheetData>
                        <row r="1">
                            <c r="A1" t="s"><v>0</v></c>
                            <c r="B1" t="s"><v>1</v></c>
                        </row>
                    </sheetData>
                </worksheet>""",
            )

        details = extract_text_details(
            "sample.xlsx",
            workbook.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual("Hello World", details.text)
        self.assertEqual("spreadsheet", details.source_type)
        self.assertEqual("builtin", details.engine)

    def test_extract_text_details_handles_ods_spreadsheets(self) -> None:
        from app.services.extraction import extract_text_details

        workbook = io.BytesIO()
        with zipfile.ZipFile(workbook, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "content.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <office:document-content
                    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                    xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
                    <office:body>
                        <office:text>
                            <text:p>Hello</text:p>
                            <text:p>World</text:p>
                        </office:text>
                    </office:body>
                </office:document-content>""",
            )

        details = extract_text_details(
            "sample.ods",
            workbook.getvalue(),
            "application/vnd.oasis.opendocument.spreadsheet",
        )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual("Hello\nWorld", details.text)
        self.assertEqual("spreadsheet", details.source_type)
        self.assertEqual("builtin", details.engine)

    def test_extract_text_details_handles_odt_documents(self) -> None:
        from app.services.extraction import extract_text_details

        document = io.BytesIO()
        with zipfile.ZipFile(document, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "content.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <office:document-content
                    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                    xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
                    <office:body>
                        <office:text>
                            <text:h>Hello</text:h>
                            <text:p>World</text:p>
                        </office:text>
                    </office:body>
                </office:document-content>""",
            )

        details = extract_text_details(
            "sample.odt",
            document.getvalue(),
            "application/vnd.oasis.opendocument.text",
        )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual("Hello\nWorld", details.text)
        self.assertEqual("odt", details.source_type)
        self.assertEqual("builtin", details.engine)

    def test_extract_text_details_handles_xls_workbooks_when_xlrd_is_available(self) -> None:
        from app.services.extraction import extract_text_details

        class FakeSheet:
            nrows = 2
            ncols = 2

            def cell_value(self, row_index: int, col_index: int):
                values = [["Hello", "World"], ["10", "20"]]
                return values[row_index][col_index]

        class FakeWorkbook:
            def sheets(self):
                return [FakeSheet()]

        fake_module = types.ModuleType("xlrd")
        fake_module.open_workbook = lambda file_contents: FakeWorkbook()  # noqa: ARG005

        with patch.dict(sys.modules, {"xlrd": fake_module}):
            details = extract_text_details("sample.xls", b"fake-xls-bytes", "application/vnd.ms-excel")

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual("Hello World\n10 20", details.text)
        self.assertEqual("spreadsheet", details.source_type)
        self.assertEqual("xlrd", details.engine)

    def test_extract_text_details_handles_csv_files(self) -> None:
        from app.services.extraction import extract_text_details

        details = extract_text_details("sample.csv", b"name,amount\nAlice,10\nBob,20", "text/csv")

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual("name amount\nAlice 10\nBob 20", details.text)
        self.assertEqual("csv", details.source_type)
        self.assertEqual("builtin", details.engine)

    def test_extract_text_details_handles_tsv_files(self) -> None:
        from app.services.extraction import extract_text_details

        details = extract_text_details("sample.tsv", b"name\tamount\nAlice\t10\nBob\t20", "text/tab-separated-values")

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual("name amount\nAlice 10\nBob 20", details.text)
        self.assertEqual("tsv", details.source_type)
        self.assertEqual("builtin", details.engine)

    def test_extract_text_details_handles_config_and_document_text_files(self) -> None:
        from app.services.extraction import extract_text_details

        cases = [
            ("pyproject.toml", b"[tool.poetry]\nname = 'oflow'", "application/toml", "text"),
            ("settings.ini", b"[app]\nname = O's flow", "text/plain", "text"),
            ("notes.rst", b"Title\n=====\n\nBody", "text/x-rst", "text"),
        ]

        for filename, content, mime_type, expected_source_type in cases:
            with self.subTest(filename=filename):
                details = extract_text_details(filename, content, mime_type)
                self.assertIsNotNone(details)
                assert details is not None
                self.assertEqual(expected_source_type, details.source_type)
                self.assertEqual("builtin", details.engine)

    def test_extract_text_details_handles_rtf_documents(self) -> None:
        from app.services.extraction import extract_text_details

        details = extract_text_details("sample.rtf", br"{\rtf1\ansi Hello\par World}", "application/rtf")

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual("Hello World", details.text)
        self.assertEqual("rtf", details.source_type)
        self.assertEqual("builtin", details.engine)

    def test_extract_text_details_handles_xml_documents(self) -> None:
        from app.services.extraction import extract_text_details

        details = extract_text_details("sample.xml", b"<root><title>Hello</title><body>World</body></root>", "application/xml")

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual("Hello World", details.text)
        self.assertEqual("xml", details.source_type)
        self.assertEqual("builtin", details.engine)

    def test_extract_text_details_handles_eml_messages(self) -> None:
        from app.services.extraction import extract_text_details

        eml = (
            "From: sender@example.com\r\n"
            "To: receiver@example.com\r\n"
            "Subject: Example\r\n"
            "Content-Type: multipart/alternative; boundary=boundary\r\n"
            "\r\n"
            "--boundary\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "Plain body\r\n"
            "--boundary\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<p>HTML body</p>\r\n"
            "--boundary--\r\n"
        ).encode("utf-8")

        details = extract_text_details("sample.eml", eml, "message/rfc822")

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(
            "Subject: Example\nFrom: sender@example.com\nTo: receiver@example.com\nPlain body",
            details.text,
        )
        self.assertEqual("eml", details.source_type)
        self.assertEqual("builtin", details.engine)

    def test_extract_text_details_handles_msg_messages_when_extract_msg_is_available(self) -> None:
        from app.services.extraction import extract_text_details

        class FakeMessage:
            def __init__(self, path: str) -> None:
                self.path = path
                self.subject = "Example MSG"
                self.sender = "sender@example.com"
                self.to = "receiver@example.com"
                self.cc = ""
                self.date = "2026-07-05"
                self.body = "Body text"
                self.closed = False

            def close(self) -> None:
                self.closed = True

        fake_module = types.ModuleType("extract_msg")
        fake_module.Message = FakeMessage

        with patch.dict(sys.modules, {"extract_msg": fake_module}):
            details = extract_text_details("sample.msg", b"fake-msg-bytes", "application/vnd.ms-outlook")

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(
            "Subject: Example MSG\nSender: sender@example.com\nTo: receiver@example.com\nDate: 2026-07-05\nBody text",
            details.text,
        )
        self.assertEqual("msg", details.source_type)
        self.assertEqual("extract_msg", details.engine)

    def test_extract_text_details_strips_html_noise(self) -> None:
        from app.services.extraction import extract_text_details

        html_doc = b"<html><head><style>.x{display:none}</style><script>alert('x')</script></head><body><p>Hello</p><p>World</p></body></html>"

        details = extract_text_details("sample.html", html_doc, "text/html")

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual("Hello World", details.text)
        self.assertEqual("html", details.source_type)
        self.assertEqual("builtin", details.engine)

    def test_routes_image_files_into_ocr_hook(self) -> None:
        with patch("app.services.extraction._extract_image_text", return_value="Detected image text") as ocr_hook:
            text = extract_text("sample.png", b"fake-image-bytes", "image/png")

        self.assertEqual("Detected image text", text)
        ocr_hook.assert_called_once_with(b"fake-image-bytes")

    def test_image_extraction_gracefully_returns_none_without_ocr_backend(self) -> None:
        with patch("app.services.extraction._extract_image_text", return_value=None) as ocr_hook:
            text = extract_text("sample.jpg", b"fake-image-bytes", "image/jpeg")

        self.assertIsNone(text)
        ocr_hook.assert_called_once_with(b"fake-image-bytes")

    def test_prepare_image_for_ocr_converts_non_standard_modes_then_grayscale(self) -> None:
        from app.services.extraction import _prepare_image_for_ocr

        calls: list[str] = []

        class FakeImage:
            mode = "CMYK"

            def convert(self, target_mode: str) -> "FakeImage":
                calls.append(target_mode)
                next_mode = target_mode
                result = FakeImage()
                result.mode = next_mode
                return result

        prepared = _prepare_image_for_ocr(FakeImage())

        self.assertEqual(["RGB", "L"], calls)
        self.assertEqual("L", prepared.mode)

    def test_prepare_image_for_ocr_grayscale_converts_rgb_to_l(self) -> None:
        from app.services.extraction import _prepare_image_for_ocr

        calls: list[str] = []

        class FakeImage:
            mode = "RGB"

            def convert(self, target_mode: str) -> "FakeImage":
                calls.append(target_mode)
                result = FakeImage()
                result.mode = target_mode
                return result

        prepared = _prepare_image_for_ocr(FakeImage())

        self.assertEqual(["L"], calls)
        self.assertEqual("L", prepared.mode)

    def test_prepare_image_for_ocr_applies_orientation_before_contrast(self) -> None:
        from app.services.extraction import _prepare_image_for_ocr

        convert_calls: list[str] = []
        helper_calls: list[str] = []

        class FakeImage:
            mode = "CMYK"

            def convert(self, target_mode: str) -> "FakeImage":
                convert_calls.append(target_mode)
                result = FakeImage()
                result.mode = target_mode
                return result

        def fake_orientation(image: FakeImage) -> FakeImage:
            helper_calls.append("orientation")
            return image

        def fake_contrast(image: FakeImage) -> FakeImage:
            helper_calls.append(f"contrast:{image.mode}")
            return image

        with patch("app.services.extraction._apply_image_orientation", side_effect=fake_orientation), patch(
            "app.services.extraction._apply_image_contrast", side_effect=fake_contrast
        ):
            prepared = _prepare_image_for_ocr(FakeImage())

        self.assertEqual(["orientation", "contrast:L"], helper_calls)
        self.assertEqual(["RGB", "L"], convert_calls)
        self.assertEqual("L", prepared.mode)

    def test_extracts_text_from_pdf_bytes_via_pypdf_when_available(self) -> None:
        class FakePage:
            def __init__(self, text: str) -> None:
                self._text = text

            def extract_text(self) -> str:
                return self._text

        class FakePdfReader:
            def __init__(self, stream: io.BytesIO) -> None:
                self.stream = stream
                self.pages = [FakePage("First page"), FakePage("Second page")]

        fake_module = types.ModuleType("pypdf")
        fake_module.PdfReader = FakePdfReader

        with patch.dict(sys.modules, {"pypdf": fake_module}):
            text = extract_text("sample.pdf", b"%PDF-1.4 fake-content", "application/pdf")

        self.assertEqual("First page\nSecond page", text)

    def test_pdf_extraction_falls_back_to_regex_when_library_is_unavailable(self) -> None:
        text = extract_text("sample.pdf", b"(Fallback text) Tj", "application/pdf")

        self.assertEqual("Fallback text", text)

    def test_extracts_text_from_scanned_pdf_bytes_via_pdf2image_ocr_when_available(self) -> None:
        class FakePage:
            pass

        fake_module = types.ModuleType("pdf2image")
        calls: list[tuple[bytes, dict[str, object]]] = []

        def fake_convert_from_bytes(content: bytes, **kwargs: object):  # noqa: ANN001
            calls.append((content, kwargs))
            return [FakePage(), FakePage()]

        fake_module.convert_from_bytes = fake_convert_from_bytes

        with patch.dict(sys.modules, {"pdf2image": fake_module}), patch(
            "app.services.extraction._extract_text_from_image_object",
            side_effect=["Scanned first page", "Scanned second page"],
        ) as ocr_hook:
            text = extract_text("scanned.pdf", b"%PDF-1.4 scanned-content", "application/pdf")

        self.assertEqual("Scanned first page\nScanned second page", text)
        self.assertEqual(2, ocr_hook.call_count)
        self.assertEqual(1, len(calls))
        self.assertEqual({"dpi": 300}, calls[0][1])

    def test_reports_extraction_capabilities_from_installed_optional_modules(self) -> None:
        fake_pypdf = types.ModuleType("pypdf")
        fake_pdfplumber = types.ModuleType("pdfplumber")
        fake_pdf2image = types.ModuleType("pdf2image")
        fake_pil = types.ModuleType("PIL")
        fake_pytesseract = types.ModuleType("pytesseract")

        from app.services.extraction import get_extraction_capabilities

        with patch.dict(
            sys.modules,
            {
                "pypdf": fake_pypdf,
                "pdfplumber": fake_pdfplumber,
                "pdf2image": fake_pdf2image,
                "PIL": fake_pil,
                "pytesseract": fake_pytesseract,
            },
        ):
            capabilities = get_extraction_capabilities()

            self.assertEqual(
                {
                    "pypdf": True,
                    "pdfplumber": True,
                    "pdf2image": True,
                    "pillow": True,
                    "pytesseract": True,
                    "xlrd": False,
                    "extract_msg": False,
                    "pdf_text_parsing_ready": True,
                    "image_ocr_ready": True,
                    "scanned_pdf_ocr_ready": True,
                    "legacy_xls_ready": False,
                    "legacy_outlook_msg_ready": False,
                },
                capabilities,
            )

    def test_reports_extraction_capabilities_on_admin_overview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)

            try:
                with TestClient(app) as client:
                    response = client.get("/admin/overview")
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertIn("extraction", body["settings"])
                    self.assertIn("pypdf", body["settings"]["extraction"])
                    self.assertIn("pytesseract", body["settings"]["extraction"])
            finally:
                app.state.repository.close()


class ApiDeletionTests(unittest.TestCase):
    def test_api_can_delete_document_and_cleanup_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.document_service = DocumentService(settings, app.state.repository, app.state.storage)

            payload = {
                "case_code": "CASE-DELETE-1",
                "title": "Delete me",
                "filename": "delete.pdf",
                "content_base64": base64.b64encode(b"delete-bytes").decode("ascii"),
                "mime_type": "application/pdf",
                "source_type": "api",
                "extracted_text": "Delete text",
                "structured_json": {"kind": "delete"},
                "output_html": "<html>delete</html>",
            }

            try:
                with TestClient(app) as client:
                    ingest_response = client.post("/ingestions", json=payload)
                    self.assertEqual(200, ingest_response.status_code)
                    body = ingest_response.json()
                    original_path = settings.storage_root / body["original_storage_key"]
                    rag_path = settings.storage_root / "rag" / "CASE-DELETE-1" / f"{body['document_id']}.json"
                    self.assertTrue(original_path.exists())
                    self.assertTrue(rag_path.exists())

                    delete_response = client.delete(f"/documents/{body['document_id']}")
                    self.assertEqual(200, delete_response.status_code)
                    delete_body = delete_response.json()
                    self.assertEqual(body["document_id"], delete_body["document_id"])
                    self.assertFalse(original_path.exists())
                    self.assertFalse(rag_path.exists())

                    detail_response = client.get(f"/cases/{body['case_id']}")
                    self.assertEqual(200, detail_response.status_code)
                    self.assertEqual(0, len(detail_response.json()["rag_entries"]))

                    documents_response = client.get("/documents", params={"case_id": body["case_id"], "is_deleted": True})
                    self.assertEqual(200, documents_response.status_code)
                    self.assertEqual(1, len(documents_response.json()))
                    deleted_document_item = documents_response.json()[0]
                    self.assertIn("extraction", deleted_document_item)
                    self.assertFalse(deleted_document_item["extraction"]["available"])
                    self.assertEqual("no_rag_entry", deleted_document_item["extraction"]["reason"])

                    jobs_response = client.get("/processing-jobs", params={"case_id": body["case_id"]})
                    self.assertEqual(200, jobs_response.status_code)
                    self.assertEqual(2, len(jobs_response.json()))
                    self.assertEqual("document_delete", jobs_response.json()[0]["job_type"])
            finally:
                app.state.repository.close()


class ApiReprocessTests(unittest.TestCase):
    def test_api_can_reprocess_document_and_refresh_rag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.document_service = DocumentService(settings, app.state.repository, app.state.storage)

            payload = {
                "case_code": "CASE-REPROCESS-1",
                "title": "Reprocess me",
                "filename": "reprocess.txt",
                "content_base64": base64.b64encode(b"original text").decode("ascii"),
                "mime_type": "text/plain",
                "source_type": "api",
            }

            try:
                with TestClient(app) as client:
                    ingest_response = client.post("/ingestions", json=payload)
                    self.assertEqual(200, ingest_response.status_code)
                    body = ingest_response.json()

                    original_key = body["original_storage_key"]
                    app.state.storage.put_bytes(original_key, b"updated text", "text/plain")

                    reprocess_response = client.post(f"/documents/{body['document_id']}/reprocess")
                    self.assertEqual(200, reprocess_response.status_code)
                    reprocess_body = reprocess_response.json()
                    self.assertEqual(body["document_id"], reprocess_body["document_id"])
                    self.assertEqual(body["case_id"], reprocess_body["case_id"])
                    self.assertGreaterEqual(reprocess_body["extracted_text_length"], len("updated text"))

                    case_detail = client.get(f"/cases/{body['case_id']}")
                    self.assertEqual(200, case_detail.status_code)
                    self.assertEqual(
                        "builtin",
                        json.loads(case_detail.json()["rag_entries"][0]["metadata_json"])["extraction_engine"],
                    )
                    self.assertEqual(
                        "text",
                        json.loads(case_detail.json()["rag_entries"][0]["metadata_json"])["extraction_source"],
                    )

                    document_detail = client.get(f"/documents/{body['document_id']}")
                    self.assertEqual(200, document_detail.status_code)
                    self.assertTrue(document_detail.json()["extraction"]["available"])
                    self.assertEqual("text", document_detail.json()["extraction"]["extraction_source"])
                    self.assertEqual("builtin", document_detail.json()["extraction"]["extraction_engine"])
                    self.assertTrue(document_detail.json()["extraction"]["reprocess"])

                    rag_response = client.get("/rag/search", params={"case_id": body["case_id"], "query": "updated"})
                    self.assertEqual(200, rag_response.status_code)
                    self.assertEqual(1, len(rag_response.json()))
                    self.assertEqual("updated text", rag_response.json()[0]["body_text"])

                    case_response = client.get(f"/cases/{body['case_id']}")
                    self.assertEqual(200, case_response.status_code)
                    self.assertIsNotNone(case_response.json()["case"]["last_processed_at"])

                    jobs_response = client.get("/processing-jobs", params={"case_id": body["case_id"]})
                    self.assertEqual(200, jobs_response.status_code)
                    self.assertEqual("document_reprocess", jobs_response.json()[0]["job_type"])
            finally:
                app.state.repository.close()

    def test_api_can_bulk_reprocess_selected_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.document_service = DocumentService(settings, app.state.repository, app.state.storage)

            payload_one = {
                "case_code": "CASE-BULK-REP-1",
                "title": "Bulk reprocess one",
                "filename": "one.txt",
                "content_base64": base64.b64encode(b"one original").decode("ascii"),
                "mime_type": "text/plain",
                "source_type": "api",
            }
            payload_two = {
                "case_code": "CASE-BULK-REP-2",
                "title": "Bulk reprocess two",
                "filename": "two.txt",
                "content_base64": base64.b64encode(b"two original").decode("ascii"),
                "mime_type": "text/plain",
                "source_type": "api",
            }

            try:
                with TestClient(app) as client:
                    first = client.post("/ingestions", json=payload_one)
                    second = client.post("/ingestions", json=payload_two)
                    self.assertEqual(200, first.status_code)
                    self.assertEqual(200, second.status_code)

                    app.state.storage.put_bytes(first.json()["original_storage_key"], b"one updated", "text/plain")
                    app.state.storage.put_bytes(second.json()["original_storage_key"], b"two updated", "text/plain")

                    response = client.post(
                        "/documents/bulk-reprocess",
                        json={"document_ids": [first.json()["document_id"], second.json()["document_id"]]},
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(2, body["total_documents"])
                    self.assertEqual(2, body["successful_documents"])
                    self.assertEqual(0, body["failed_documents"])

                    first_rag = client.get("/rag/search", params={"query": "one updated", "case_id": first.json()["case_id"]})
                    second_rag = client.get("/rag/search", params={"query": "two updated", "case_id": second.json()["case_id"]})
                    self.assertEqual(1, len(first_rag.json()))
                    self.assertEqual(1, len(second_rag.json()))

                    logs_response = client.get("/operation-logs", params={"event_type": "document_batch_reprocessed"})
                    self.assertEqual(200, logs_response.status_code)
                    self.assertEqual(2, len(logs_response.json()))

                    document_activity = client.get(f"/documents/{first.json()['document_id']}/activity")
                    self.assertEqual(200, document_activity.status_code)
                    self.assertIn(
                        "document_batch_reprocessed",
                        {item["event_type"] for item in document_activity.json()},
                    )
            finally:
                app.state.repository.close()

    def test_api_can_reprocess_all_documents_for_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "storage" / "rag",
                discord_bot_token="",
                target_channel_ids=(123,),
                ai_provider="openai_compatible",
                ai_api_key="",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.storage = LocalFileStorageAdapter(settings.storage_root)
            app.state.ingestion_service = IngestionService(settings, app.state.repository, app.state.storage)
            app.state.document_service = DocumentService(settings, app.state.repository, app.state.storage)

            payload_one = {
                "case_code": "CASE-BATCH-1",
                "title": "Batch one",
                "filename": "one.txt",
                "content_base64": base64.b64encode(b"one text").decode("ascii"),
                "mime_type": "text/plain",
                "source_type": "api",
            }
            payload_two = {
                "case_code": "CASE-BATCH-1",
                "title": "Batch two",
                "filename": "two.txt",
                "content_base64": base64.b64encode(b"two text").decode("ascii"),
                "mime_type": "text/plain",
                "source_type": "api",
            }

            try:
                with TestClient(app) as client:
                    first = client.post("/ingestions", json=payload_one)
                    second = client.post("/ingestions", json=payload_two)
                    self.assertEqual(200, first.status_code)
                    self.assertEqual(200, second.status_code)

                    app.state.storage.put_bytes(first.json()["original_storage_key"], b"one updated text", "text/plain")
                    app.state.storage.put_bytes(second.json()["original_storage_key"], b"two updated text", "text/plain")

                    batch_response = client.post(f"/cases/{first.json()['case_id']}/reprocess-documents")
                    self.assertEqual(200, batch_response.status_code)
                    body = batch_response.json()
                    self.assertEqual(2, body["total_documents"])
                    self.assertEqual(2, body["successful_documents"])
                    self.assertEqual(0, body["failed_documents"])

                    limited_batch_response = client.post(
                        f"/cases/{first.json()['case_id']}/reprocess-documents",
                        params={"limit": -1},
                    )
                    self.assertEqual(200, limited_batch_response.status_code)
                    self.assertEqual(1, limited_batch_response.json()["total_documents"])

                    rag_response = client.get("/rag/search", params={"case_id": first.json()["case_id"], "query": "updated"})
                    self.assertEqual(200, rag_response.status_code)
                    self.assertEqual(2, len(rag_response.json()))

                    case_response = client.get(f"/cases/{first.json()['case_id']}")
                    self.assertEqual(200, case_response.status_code)
                    self.assertIsNotNone(case_response.json()["case"]["last_processed_at"])
            finally:
                app.state.repository.close()


if __name__ == "__main__":
    unittest.main()
