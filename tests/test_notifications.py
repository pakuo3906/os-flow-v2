from __future__ import annotations

import tempfile
import unittest
import warnings
from datetime import date
from pathlib import Path
from unittest.mock import patch

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.",
)

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import Settings
from app.repositories.sqlite import SQLiteRepository
from app.services.notifications import NotificationService


class NotificationServiceTests(unittest.TestCase):
    def test_daily_digest_includes_due_and_invoice_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "app.db"
            with SQLiteRepository(db_path) as repo:
                repo.upsert_case(
                    case_code="CASE-OVERDUE",
                    title="Overdue case",
                    status="in_progress",
                    due_date="2026-07-02",
                    invoice_status="pending",
                )
                repo.upsert_case(
                    case_code="CASE-TODAY",
                    title="Today case",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                repo.upsert_case(
                    case_code="CASE-TOMORROW",
                    title="Tomorrow case",
                    status="in_progress",
                    due_date="2026-07-04",
                    invoice_status="pending",
                )

                service = NotificationService(repo)
                digest = service.build_daily_digest(
                    as_of=date(2026, 7, 3),
                    due_lookahead_days=1,
                    invoice_lookahead_days=1,
                    case_status="in_progress",
                    invoice_status="pending",
                )

                self.assertEqual("2026-07-03", digest.as_of)
                self.assertEqual(6, len(digest.notifications))
                self.assertEqual("overdue", digest.notifications[0].severity)
                self.assertEqual("due_task", digest.notifications[0].category)
                self.assertIn("CASE-OVERDUE", digest.notifications[0].message)
                self.assertEqual(
                    ["due_task", "invoice_reminder", "due_task", "invoice_reminder", "due_task", "invoice_reminder"],
                    [item.category for item in digest.notifications],
                )
                self.assertEqual(
                    ["overdue", "overdue", "urgent", "urgent", "warning", "warning"],
                    [item.severity for item in digest.notifications],
                )


class NotificationApiTests(unittest.TestCase):
    def test_notifications_endpoint_returns_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "rag",
                discord_bot_token="",
                target_channel_ids=(123456789012345678,),
                ai_provider="openai_compatible",
                ai_api_key="test",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.ingestion_service = app.state.ingestion_service.__class__(
                settings, app.state.repository, app.state.storage
            )
            app.state.document_service = app.state.document_service.__class__(
                settings, app.state.repository, app.state.storage
            )

            try:
                repo = app.state.repository
                repo.upsert_case(
                    case_code="CASE-API-OVERDUE",
                    title="API overdue",
                    status="in_progress",
                    due_date="2026-07-02",
                    invoice_status="pending",
                )
                repo.upsert_case(
                    case_code="CASE-API-TODAY",
                    title="API today",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )

                with TestClient(app) as client:
                    response = client.get(
                        "/notifications/due",
                        params={
                            "as_of": "2026-07-03",
                            "due_lookahead_days": 1,
                            "invoice_lookahead_days": 1,
                        },
                    )
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual("2026-07-03", body["as_of"])
                    self.assertGreaterEqual(len(body["notifications"]), 2)
                    self.assertEqual("due_task", body["notifications"][0]["category"])
            finally:
                app.state.repository.close()

    def test_notification_deliveries_endpoint_lists_delivery_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "rag",
                discord_bot_token="",
                target_channel_ids=(123456789012345678,),
                ai_provider="openai_compatible",
                ai_api_key="test",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.ingestion_service = app.state.ingestion_service.__class__(
                settings, app.state.repository, app.state.storage
            )
            app.state.document_service = app.state.document_service.__class__(
                settings, app.state.repository, app.state.storage
            )

            try:
                repo = app.state.repository
                repo.record_notification_delivery(
                    deliver_to="auto",
                    destination="auto:line,discord",
                    delivered_count=2,
                    digest_as_of="2026-07-03",
                    due_lookahead_days=1,
                    invoice_lookahead_days=7,
                    status="success",
                    message="line: line_messaging_api | discord: discord_webhook",
                    metadata_json={"notification_count": 2},
                )

                with TestClient(app) as client:
                    response = client.get("/notification-deliveries")
                    self.assertEqual(200, response.status_code)
                    self.assertEqual("1", response.headers.get("X-Total-Count"))
                    body = response.json()
                    self.assertEqual(1, len(body))
                    self.assertEqual("auto", body[0]["deliver_to"])
                    self.assertEqual("success", body[0]["status"])

                    summary = client.get("/summary")
                    self.assertEqual(200, summary.status_code)
                    self.assertEqual(1, summary.json()["notification_deliveries_total"])
            finally:
                app.state.repository.close()

    def test_notification_deliveries_endpoint_supports_created_at_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "rag",
                discord_bot_token="",
                target_channel_ids=(123456789012345678,),
                ai_provider="openai_compatible",
                ai_api_key="test",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.ingestion_service = app.state.ingestion_service.__class__(
                settings, app.state.repository, app.state.storage
            )
            app.state.document_service = app.state.document_service.__class__(
                settings, app.state.repository, app.state.storage
            )

            try:
                repo = app.state.repository
                repo.record_notification_delivery(
                    deliver_to="auto",
                    destination="auto:line,discord",
                    delivered_count=2,
                    digest_as_of="2026-07-03",
                    due_lookahead_days=1,
                    invoice_lookahead_days=7,
                    status="success",
                    message="line: line_messaging_api | discord: discord_webhook",
                    metadata_json={"notification_count": 2, "attempts": 1},
                )
                repo.record_notification_delivery(
                    deliver_to="auto",
                    destination="error:auto",
                    delivered_count=0,
                    digest_as_of="2026-07-03",
                    due_lookahead_days=1,
                    invoice_lookahead_days=7,
                    status="failed",
                    message="",
                    error_message="temporary network issue",
                    metadata_json={"notification_count": 2, "attempts": 2},
                )
                repo.record_notification_delivery(
                    deliver_to="slack-webhook",
                    destination="slack_webhook",
                    delivered_count=1,
                    digest_as_of="2026-07-03",
                    due_lookahead_days=1,
                    invoice_lookahead_days=7,
                    status="success",
                    message="slack digest sample",
                    metadata_json={"notification_count": 1, "attempts": 1},
                )
                repo.record_notification_delivery(
                    deliver_to="slack-webhook",
                    destination="slack_webhook",
                    delivered_count=1,
                    digest_as_of="2026-07-03",
                    due_lookahead_days=1,
                    invoice_lookahead_days=7,
                    status="success",
                    message="slack digest sample",
                    metadata_json={"notification_count": 1, "attempts": 1},
                )

                with TestClient(app) as client:
                    filtered = client.get(
                        "/notification-deliveries",
                        params={"status": "failed", "created_after": "2099-01-01T00:00:00+00:00"},
                    )
                    self.assertEqual(200, filtered.status_code)
                    self.assertEqual("0", filtered.headers.get("X-Total-Count"))
                    self.assertEqual([], filtered.json())

                    filtered = client.get(
                        "/notification-deliveries",
                        params={"status": "failed", "created_before": "2099-12-31T23:59:59+00:00"},
                    )
                    self.assertEqual(200, filtered.status_code)
                    self.assertEqual("1", filtered.headers.get("X-Total-Count"))
                    self.assertEqual(1, len(filtered.json()))
                    self.assertEqual("failed", filtered.json()[0]["status"])
            finally:
                app.state.repository.close()

    def test_notification_delivery_summary_reports_success_and_failure_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "rag",
                discord_bot_token="",
                target_channel_ids=(123456789012345678,),
                ai_provider="openai_compatible",
                ai_api_key="test",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.ingestion_service = app.state.ingestion_service.__class__(
                settings, app.state.repository, app.state.storage
            )
            app.state.document_service = app.state.document_service.__class__(
                settings, app.state.repository, app.state.storage
            )

            try:
                repo = app.state.repository
                repo.record_notification_delivery(
                    deliver_to="auto",
                    destination="auto:line,discord",
                    delivered_count=2,
                    digest_as_of="2026-07-03",
                    due_lookahead_days=1,
                    invoice_lookahead_days=7,
                    status="success",
                    message="line: line_messaging_api | discord: discord_webhook",
                    metadata_json={"notification_count": 2, "attempts": 1},
                )
                repo.record_notification_delivery(
                    deliver_to="auto",
                    destination="error:auto",
                    delivered_count=0,
                    digest_as_of="2026-07-03",
                    due_lookahead_days=1,
                    invoice_lookahead_days=7,
                    status="failed",
                    message="",
                    error_message="temporary network issue",
                    metadata_json={"notification_count": 2, "attempts": 2},
                )
                repo.record_notification_delivery(
                    deliver_to="slack-webhook",
                    destination="slack_webhook",
                    delivered_count=1,
                    digest_as_of="2026-07-03",
                    due_lookahead_days=1,
                    invoice_lookahead_days=7,
                    status="success",
                    message="slack digest sample",
                    metadata_json={"notification_count": 1, "attempts": 1},
                )

                with TestClient(app) as client:
                    response = client.get("/notification-deliveries/summary")
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual(3, body["total"])
                    self.assertEqual(2, body["success_total"])
                    self.assertEqual(1, body["failed_total"])
                    self.assertEqual(0.3333, body["failure_rate"])
                    self.assertEqual(False, body["needs_attention"])
                    self.assertIsNone(body["attention_reason"])
                    self.assertEqual([], body["attention_targets"])
                    self.assertEqual("success", body["latest_delivery"]["status"])
                    self.assertEqual("2026-07-03", body["latest_delivery"]["digest_as_of"])
                    self.assertEqual("2026-07-03", body["latest_success"]["digest_as_of"])
                    self.assertEqual("2026-07-03", body["latest_failure"]["digest_as_of"])
                    self.assertEqual(2, body["by_deliver_to"]["auto"]["total"])
                    self.assertEqual(1, body["by_deliver_to"]["auto"]["success_total"])
                    self.assertEqual(1, body["by_deliver_to"]["auto"]["failed_total"])
                    self.assertEqual(0.5, body["by_deliver_to"]["auto"]["failure_rate"])
                    self.assertEqual(False, body["by_deliver_to"]["auto"]["needs_attention"])
                    self.assertIsNone(body["by_deliver_to"]["auto"]["attention_reason"])
                    self.assertEqual("2026-07-03", body["by_deliver_to"]["auto"]["latest_delivery"]["digest_as_of"])
                    self.assertEqual(1, body["by_deliver_to"]["slack-webhook"]["total"])
                    self.assertEqual(1, body["by_deliver_to"]["slack-webhook"]["success_total"])
                    scoped = client.get(
                        "/notification-deliveries/summary",
                        params={"deliver_to": "auto", "minimum_total_for_attention": 2},
                    )
                    self.assertEqual(200, scoped.status_code)
                    scoped_body = scoped.json()
                    self.assertEqual(2, scoped_body["total"])
                    self.assertEqual(1, scoped_body["success_total"])
                    self.assertEqual(1, scoped_body["failed_total"])
                    self.assertEqual(0.5, scoped_body["failure_rate"])
                    self.assertEqual(True, scoped_body["needs_attention"])
                    self.assertIsNotNone(scoped_body["attention_reason"])
                    self.assertEqual("failed", scoped_body["latest_delivery"]["status"])
                    self.assertEqual(1, len(body["recent_failures"]))
                    self.assertEqual("failed", body["recent_failures"][0]["status"])
                    self.assertEqual("auto", body["recent_failures"][0]["deliver_to"])

                    filtered = client.get(
                        "/notification-deliveries/summary",
                        params={"created_after": "2099-01-01T00:00:00+00:00"},
                    )
                    self.assertEqual(200, filtered.status_code)
                    filtered_body = filtered.json()
                    self.assertEqual(0, filtered_body["total"])
                    self.assertEqual(0, filtered_body["success_total"])
                    self.assertEqual(0, filtered_body["failed_total"])
                    self.assertEqual(0.0, filtered_body["failure_rate"])
                    self.assertEqual(False, filtered_body["needs_attention"])
                    self.assertIsNone(filtered_body["attention_reason"])
                    self.assertEqual([], filtered_body["attention_targets"])
                    self.assertIsNone(filtered_body["latest_delivery"])
                    self.assertIsNone(filtered_body["latest_success"])
                    self.assertIsNone(filtered_body["latest_failure"])
                    self.assertEqual(0, filtered_body["by_deliver_to"]["auto"]["total"])
                    self.assertEqual(0, filtered_body["by_deliver_to"]["auto"]["success_total"])
                    self.assertEqual(0, filtered_body["by_deliver_to"]["auto"]["failed_total"])
                    self.assertEqual(0.0, filtered_body["by_deliver_to"]["auto"]["failure_rate"])
                    self.assertEqual(False, filtered_body["by_deliver_to"]["auto"]["needs_attention"])
                    self.assertIsNone(filtered_body["by_deliver_to"]["auto"]["attention_reason"])
                    self.assertIsNone(filtered_body["by_deliver_to"]["auto"]["latest_delivery"])
                    self.assertEqual([], filtered_body["recent_failures"])

                    limited = client.get(
                        "/notification-deliveries/summary",
                        params={"recent_failures_limit": 1},
                    )
                    self.assertEqual(200, limited.status_code)
                    limited_body = limited.json()
                    self.assertEqual(1, len(limited_body["recent_failures"]))

                    low_risk = client.get(
                        "/notification-deliveries/summary",
                        params={"failure_rate_threshold": 0.25, "minimum_total_for_attention": 2},
                    )
                    self.assertEqual(200, low_risk.status_code)
                    low_risk_body = low_risk.json()
                    self.assertEqual(True, low_risk_body["needs_attention"])
                    self.assertIsNotNone(low_risk_body["attention_reason"])
                    self.assertEqual(2, low_risk_body["by_deliver_to"]["auto"]["total"])
                    self.assertEqual(True, low_risk_body["by_deliver_to"]["auto"]["needs_attention"])
                    self.assertIsNotNone(low_risk_body["by_deliver_to"]["auto"]["attention_reason"])
                    self.assertEqual(["auto"], low_risk_body["attention_targets"])
            finally:
                app.state.repository.close()

    def test_notification_delivery_trends_returns_daily_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                app_env="test",
                database_path=root / "data" / "app.db",
                storage_root=root / "storage",
                output_root=root / "output",
                temp_root=root / "temp",
                rag_root=root / "rag",
                discord_bot_token="",
                target_channel_ids=(123456789012345678,),
                ai_provider="openai_compatible",
                ai_api_key="test",
                ai_model="gpt-4.1",
                ai_base_url="https://api.openai.com/v1",
            )
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(settings.database_path)
            app.state.ingestion_service = app.state.ingestion_service.__class__(
                settings, app.state.repository, app.state.storage
            )
            app.state.document_service = app.state.document_service.__class__(
                settings, app.state.repository, app.state.storage
            )

            try:
                repo = app.state.repository
                with patch("app.repositories.sqlite._now", side_effect=["2026-07-02T09:00:00+00:00"]):
                    repo.record_notification_delivery(
                        deliver_to="auto",
                        destination="auto:line,discord",
                        delivered_count=2,
                        digest_as_of="2026-07-02",
                        due_lookahead_days=1,
                        invoice_lookahead_days=7,
                        status="success",
                        message="line: line_messaging_api | discord: discord_webhook",
                        metadata_json={"notification_count": 2, "attempts": 1},
                    )
                with patch("app.repositories.sqlite._now", side_effect=["2026-07-02T10:00:00+00:00"]):
                    repo.record_notification_delivery(
                        deliver_to="auto",
                        destination="error:auto",
                        delivered_count=0,
                        digest_as_of="2026-07-02",
                        due_lookahead_days=1,
                        invoice_lookahead_days=7,
                        status="failed",
                        message="",
                        error_message="temporary network issue",
                        metadata_json={"notification_count": 2, "attempts": 2},
                    )
                with patch("app.repositories.sqlite._now", side_effect=["2026-07-03T11:00:00+00:00"]):
                    repo.record_notification_delivery(
                        deliver_to="auto",
                        destination="auto:line,discord",
                        delivered_count=2,
                        digest_as_of="2026-07-03",
                        due_lookahead_days=1,
                        invoice_lookahead_days=7,
                        status="success",
                        message="line: line_messaging_api | discord: discord_webhook",
                        metadata_json={"notification_count": 2, "attempts": 1},
                    )

                with TestClient(app) as client:
                    response = client.get("/notification-deliveries/trends")
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual("day", body["granularity"])
                    self.assertEqual(2, len(body["trends"]))
                    self.assertEqual("2026-07-03", body["trends"][0]["period"])
                    self.assertEqual("day", body["trends"][0]["granularity"])
                    self.assertEqual(1, body["trends"][0]["total"])
                    self.assertEqual(1, body["trends"][0]["success_total"])
                    self.assertEqual(0, body["trends"][0]["failed_total"])
                    self.assertEqual(0.0, body["trends"][0]["failure_rate"])
                    self.assertEqual("2026-07-02", body["trends"][1]["period"])
                    self.assertEqual("day", body["trends"][1]["granularity"])
                    self.assertEqual(2, body["trends"][1]["total"])
                    self.assertEqual(1, body["trends"][1]["success_total"])
                    self.assertEqual(1, body["trends"][1]["failed_total"])
                    self.assertEqual(0.5, body["trends"][1]["failure_rate"])
                    self.assertEqual(False, body["trends"][1]["needs_attention"])
                    self.assertIsNone(body["trends"][1]["attention_reason"])

                    weekly = client.get("/notification-deliveries/trends", params={"granularity": "week"})
                    self.assertEqual(200, weekly.status_code)
                    weekly_body = weekly.json()
                    self.assertEqual("week", weekly_body["granularity"])
                    self.assertEqual(1, len(weekly_body["trends"]))
                    self.assertEqual("2026-W27", weekly_body["trends"][0]["period"])
                    self.assertEqual(3, weekly_body["trends"][0]["total"])
                    self.assertEqual(2, weekly_body["trends"][0]["success_total"])
                    self.assertEqual(1, weekly_body["trends"][0]["failed_total"])

                    monthly = client.get("/notification-deliveries/trends", params={"granularity": "month"})
                    self.assertEqual(200, monthly.status_code)
                    monthly_body = monthly.json()
                    self.assertEqual("month", monthly_body["granularity"])
                    self.assertEqual(1, len(monthly_body["trends"]))
                    self.assertEqual("2026-07", monthly_body["trends"][0]["period"])
                    self.assertEqual(3, monthly_body["trends"][0]["total"])

                    invalid = client.get("/notification-deliveries/trends", params={"granularity": "quarter"})
                    self.assertEqual(400, invalid.status_code)

                    alerts = client.get(
                        "/notification-deliveries/alerts",
                        params={"minimum_total_for_attention": 2},
                    )
                    self.assertEqual(200, alerts.status_code)
                    alerts_body = alerts.json()
                    self.assertEqual("day", alerts_body["granularity"])
                    self.assertEqual(1, alerts_body["alert_total"])
                    self.assertEqual("2026-07-02", alerts_body["alerts"][0]["period"])
                    self.assertEqual(True, alerts_body["alerts"][0]["needs_attention"])
                    self.assertEqual(None, alerts_body["deliver_to"])

                    report = client.get(
                        "/notification-deliveries/report",
                        params={"granularity": "week", "minimum_total_for_attention": 2},
                    )
                    self.assertEqual(200, report.status_code)
                    report_body = report.json()
                    self.assertEqual("week", report_body["granularity"])
                    self.assertEqual(3, report_body["summary"]["total"])
                    self.assertEqual(2, report_body["summary"]["success_total"])
                    self.assertEqual(1, report_body["summary"]["failed_total"])
                    self.assertEqual(3, report_body["scope_total"])
                    self.assertEqual(["auto"], report_body["attention_targets"])
                    self.assertEqual("success", report_body["latest_delivery"]["status"])
                    self.assertEqual("2026-07-03", report_body["latest_success"]["digest_as_of"])
                    self.assertEqual("2026-07-02", report_body["latest_failure"]["digest_as_of"])
                    self.assertEqual(True, report_body["needs_attention"])
                    self.assertIsNotNone(report_body["attention_reason"])
                    self.assertEqual("2026-07-02", report_body["summary"]["recent_failures"][0]["digest_as_of"])
                    self.assertEqual("2026-W27", report_body["trends"]["trends"][0]["period"])
                    self.assertEqual(3, report_body["trends"]["trends"][0]["total"])
                    self.assertEqual("week", report_body["trends"]["granularity"])
                    self.assertEqual(1, report_body["alerts"]["alert_total"])

                    report_markdown = client.get(
                        "/notification-deliveries/report.md",
                        params={"granularity": "week", "minimum_total_for_attention": 2},
                    )
                    self.assertEqual(200, report_markdown.status_code)
                    self.assertIn("O's flow Notification Delivery Report", report_markdown.text)
                    self.assertIn("scope total: 3", report_markdown.text)
                    self.assertIn("attention targets: auto", report_markdown.text)
                    self.assertIn("2026-W27", report_markdown.text)
                    self.assertIn("needs_attention=True", report_markdown.text)
            finally:
                app.state.repository.close()


if __name__ == "__main__":
    unittest.main()
