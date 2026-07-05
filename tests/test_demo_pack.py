from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import Settings
from app.repositories.sqlite import SQLiteRepository
from app.services.demo_pack import build_demo_pack_guide, build_missing_submissions_payload, seed_line_field_organization_pack
from app.services.ingestion import IngestionService
from app.storage.local import LocalFileStorageAdapter


def _make_settings(root: Path) -> Settings:
    return Settings(
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


def _make_env(root: Path) -> dict[str, str]:
    return {
        "APP_ENV": "test",
        "DATABASE_PATH": str(root / "data" / "app.db"),
        "STORAGE_ROOT": str(root / "storage"),
        "OUTPUT_ROOT": str(root / "output"),
        "TEMP_ROOT": str(root / "temp"),
        "RAG_ROOT": str(root / "storage" / "rag"),
        "DISCORD_TARGET_CHANNEL_IDS": "123",
        "AI_PROVIDER": "openai_compatible",
        "AI_API_KEY": "",
        "AI_MODEL": "gpt-4.1",
        "AI_BASE_URL": "https://api.openai.com/v1",
        "LINE_INBOX_CASE_CODE": "LINE-INBOX",
    }


class DemoPackTests(unittest.TestCase):
    def test_seed_demo_pack_creates_reviewable_line_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _make_settings(root)
            with SQLiteRepository(settings.database_path) as repo:
                storage = LocalFileStorageAdapter(settings.storage_root)
                service = IngestionService(settings, repo, storage)

                result = seed_line_field_organization_pack(settings, repo, service)

                self.assertTrue(result["seeded"])
                self.assertEqual(4, len(result["seeded_case_codes"]))
                self.assertEqual(4, len(result["created_document_ids"]))
                self.assertFalse(result["reused_cases"])
                guide = result["demo_pack"]
                self.assertEqual("LINE現場整理パック", guide["title"])
                self.assertEqual("scripts/seed_demo_pack.ps1", guide["seed_script"])
                self.assertIn("/admin/missing-submissions", [resource["path"] for resource in guide["resources"]])
                self.assertEqual(4, guide["current_counts"]["cases"])
                self.assertEqual(4, guide["current_counts"]["documents"])
                self.assertTrue(guide["missing_submissions_preview"])
                self.assertIn("missing_submission_reason", guide["missing_submissions_preview"][0])
                self.assertGreaterEqual(repo.count_operation_logs(event_type="demo_pack_seeded"), 1)
                deliveries = repo.list_notification_deliveries(limit=10)
                self.assertGreaterEqual(len(deliveries), 2)
                self.assertEqual({"success", "failed"}, {deliveries[0].status, deliveries[1].status})

                missing = build_missing_submissions_payload(repo, due_before=date.today().isoformat())
                self.assertEqual("/admin/missing-submissions", missing["collection_path"])
                self.assertGreaterEqual(missing["total"], 1)

    def test_admin_manifest_and_ui_surface_demo_pack_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _make_settings(root)
            with SQLiteRepository(settings.database_path) as repo:
                storage = LocalFileStorageAdapter(settings.storage_root)
                service = IngestionService(settings, repo, storage)
                seed_line_field_organization_pack(settings, repo, service)

            env = _make_env(root)
            with patch("app.config.load_dotenv", autospec=True, return_value=None):
                with patch.dict(os.environ, env, clear=True):
                    app = create_app()

            try:
                with TestClient(app) as client:
                    demo_pack = client.get("/admin/demo-pack")
                    self.assertEqual(200, demo_pack.status_code)
                    demo_pack_body = demo_pack.json()
                    self.assertEqual("LINE現場整理パック", demo_pack_body["title"])
                    self.assertIn("案件・書類・請求・提出漏れ", demo_pack_body["scenario"])
                    self.assertIn("/admin/ui", [resource["path"] for resource in demo_pack_body["resources"]])
                    self.assertEqual(4, demo_pack_body["current_counts"]["cases"])
                    self.assertEqual(4, demo_pack_body["current_counts"]["documents"])

                    invoices = client.get("/admin/invoices")
                    self.assertEqual(200, invoices.status_code)
                    self.assertEqual(4, len(invoices.json()))
                    self.assertTrue(all("invoice_status" in item for item in invoices.json()))

                    missing = client.get("/admin/missing-submissions")
                    self.assertEqual(200, missing.status_code)
                    missing_body = missing.json()
                    self.assertEqual(1, missing_body["total"])
                    self.assertTrue(all("missing_submission_reason" in item for item in missing_body["items"]))

                    admin_resources = client.get("/admin/resources")
                    self.assertEqual(200, admin_resources.status_code)
                    resource_names = [resource["name"] for resource in admin_resources.json()["resources"]]
                    self.assertIn("invoices", resource_names)
                    self.assertIn("missing_submissions", resource_names)

                    admin_react_admin = client.get("/admin/react-admin")
                    self.assertEqual(200, admin_react_admin.status_code)
                    react_resource_names = [resource["name"] for resource in admin_react_admin.json()["resources"]]
                    self.assertIn("invoices", react_resource_names)
                    self.assertIn("missing_submissions", react_resource_names)

                    admin_ui = client.get("/admin/ui")
                    self.assertEqual(200, admin_ui.status_code)
                    self.assertIn("LINE現場整理パック", admin_ui.text)
                    self.assertIn("demoPackButton", admin_ui.text)
                    self.assertIn("/admin/demo-pack", admin_ui.text)
                    self.assertIn("/admin/missing-submissions", admin_ui.text)
                    self.assertIn("/invoices", admin_ui.text)
            finally:
                app.state.repository.close()
