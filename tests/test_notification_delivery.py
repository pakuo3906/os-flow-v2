from __future__ import annotations

import tempfile
import unittest
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

import httpx

from app.domain.models import NotificationBatch
from app.repositories.sqlite import SQLiteRepository
from app.services.notification_delivery import (
    AutoRoutingNotificationDelivery,
    DiscordWebhookNotificationDelivery,
    EmailSmtpNotificationDelivery,
    _chunk_discord_message,
    render_digest_markdown,
    LineMessagingApiNotificationDelivery,
    SlackWebhookNotificationDelivery,
)
from app.services.notifications import NotificationService


class NotificationDeliveryTests(unittest.TestCase):
    def test_render_digest_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            with SQLiteRepository(db_path) as repo:
                repo.upsert_case(
                    case_code="CASE-001",
                    title="Sample",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                digest = NotificationService(repo).build_daily_digest(as_of=date(2026, 7, 3))

                markdown = render_digest_markdown(digest)

                self.assertIn("Notification Digest", markdown)
                self.assertIn("CASE-001", markdown)
                self.assertIn("Due Task", markdown)

    def test_chunk_discord_message_splits_long_content(self) -> None:
        chunks = _chunk_discord_message("x" * 2001, limit=1900)

        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 1900 for chunk in chunks))

    def test_discord_webhook_delivery_posts_chunked_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            with SQLiteRepository(db_path) as repo:
                repo.upsert_case(
                    case_code="CASE-002",
                    title="Webhook sample",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                digest = NotificationService(repo).build_daily_digest(as_of=date(2026, 7, 3))

                requests: list[httpx.Request] = []

                def handler(request: httpx.Request) -> httpx.Response:
                    requests.append(request)
                    return httpx.Response(204)

                transport = httpx.MockTransport(handler)
                delivery = DiscordWebhookNotificationDelivery(
                    "https://discord.test/webhook",
                    username="O's flow",
                    avatar_url="https://example.invalid/avatar.png",
                    transport=transport,
                )

                result = self._run_async(delivery.send(digest))

                self.assertEqual("discord_webhook", result.destination)
                self.assertGreaterEqual(result.delivered_count, 1)
                self.assertGreaterEqual(len(requests), 1)
                body = requests[0].read().decode("utf-8")
                self.assertIn("CASE-002", body)
                self.assertIn("username", body)

    def test_email_smtp_delivery_sends_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            with SQLiteRepository(db_path) as repo:
                repo.upsert_case(
                    case_code="CASE-EMAIL",
                    title="Email sample",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                digest = NotificationService(repo).build_daily_digest(as_of=date(2026, 7, 3))

                fake_client = FakeSMTPClient()
                with patch("app.services.notification_delivery.smtplib.SMTP", return_value=fake_client):
                    delivery = EmailSmtpNotificationDelivery(
                        smtp_host="smtp.example.com",
                        smtp_port=587,
                        username="user@example.com",
                        password="secret",
                        use_tls=True,
                        from_address="noreply@example.com",
                        recipients=("ops@example.com",),
                        subject_prefix="O's flow",
                    )
                    result = self._run_async(delivery.send(digest))

                self.assertEqual("email_smtp", result.destination)
                self.assertEqual(1, fake_client.starttls_calls)
                self.assertEqual([("user@example.com", "secret")], fake_client.login_calls)
                self.assertEqual(1, len(fake_client.sent_messages))
                email = fake_client.sent_messages[0]
                self.assertIsInstance(email, EmailMessage)
                self.assertIn("CASE-EMAIL", email.get_content())
                self.assertIn("Notification Digest", email["Subject"])

    def test_slack_webhook_delivery_posts_text_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            with SQLiteRepository(db_path) as repo:
                repo.upsert_case(
                    case_code="CASE-SLACK",
                    title="Slack sample",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                digest = NotificationService(repo).build_daily_digest(as_of=date(2026, 7, 3))

                requests: list[httpx.Request] = []

                def handler(request: httpx.Request) -> httpx.Response:
                    requests.append(request)
                    return httpx.Response(200, text="ok")

                transport = httpx.MockTransport(handler)
                delivery = SlackWebhookNotificationDelivery(
                    "https://hooks.slack.test/webhook",
                    transport=transport,
                )

                result = self._run_async(delivery.send(digest))

                self.assertEqual("slack_webhook", result.destination)
                self.assertGreaterEqual(result.delivered_count, 1)
                self.assertGreaterEqual(len(requests), 1)
                body = requests[0].read().decode("utf-8")
                self.assertIn("CASE-SLACK", body)
                self.assertIn('"text"', body)

    def test_line_messaging_api_delivery_posts_push_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            with SQLiteRepository(db_path) as repo:
                repo.upsert_case(
                    case_code="CASE-LINE",
                    title="Line sample",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                digest = NotificationService(repo).build_daily_digest(as_of=date(2026, 7, 3))

                requests: list[httpx.Request] = []

                def handler(request: httpx.Request) -> httpx.Response:
                    requests.append(request)
                    return httpx.Response(200, json={"ok": True})

                transport = httpx.MockTransport(handler)
                delivery = LineMessagingApiNotificationDelivery(
                    api_base_url="https://api.line.me",
                    channel_access_token="line-token",
                    recipient_ids=("U1234567890", "C1234567890"),
                    transport=transport,
                )

                result = self._run_async(delivery.send(digest))

                self.assertEqual("line_messaging_api", result.destination)
                self.assertGreaterEqual(result.delivered_count, 1)
                self.assertEqual(2, len(requests))
                body = requests[0].read().decode("utf-8")
                self.assertIn("CASE-LINE", body)
                self.assertIn('"to"', body)
                self.assertIn('"messages"', body)

    def test_auto_routing_prefers_line_and_discord_for_urgent_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            with SQLiteRepository(db_path) as repo:
                repo.upsert_case(
                    case_code="CASE-AUTO-URGENT",
                    title="Urgent sample",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                digest = NotificationService(repo).build_daily_digest(as_of=date(2026, 7, 3))

                line = RecordingDelivery("line")
                discord = RecordingDelivery("discord")
                slack = RecordingDelivery("slack")
                email = RecordingDelivery("email")
                delivery = AutoRoutingNotificationDelivery(
                    {
                        "line": line,
                        "discord": discord,
                        "slack": slack,
                        "email": email,
                    }
                )

                result = self._run_async(delivery.send(digest))

                self.assertEqual("auto:line,discord", result.destination)
                self.assertEqual(4, result.delivered_count)
                self.assertEqual(1, len(line.calls))
                self.assertEqual(1, len(discord.calls))
                self.assertEqual(0, len(slack.calls))
                self.assertEqual(0, len(email.calls))

    def test_auto_routing_prefers_slack_and_email_for_warning_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            with SQLiteRepository(db_path) as repo:
                repo.upsert_case(
                    case_code="CASE-AUTO-WARN",
                    title="Warning sample",
                    status="in_progress",
                    due_date="2026-07-10",
                    invoice_status="pending",
                )
                digest = NotificationService(repo).build_daily_digest(as_of=date(2026, 7, 3))

                line = RecordingDelivery("line")
                discord = RecordingDelivery("discord")
                slack = RecordingDelivery("slack")
                email = RecordingDelivery("email")
                delivery = AutoRoutingNotificationDelivery(
                    {
                        "line": line,
                        "discord": discord,
                        "slack": slack,
                        "email": email,
                    }
                )

                result = self._run_async(delivery.send(digest))

                self.assertEqual("auto:slack,email", result.destination)
                self.assertEqual(2, result.delivered_count)
                self.assertEqual(0, len(line.calls))
                self.assertEqual(0, len(discord.calls))
                self.assertEqual(1, len(slack.calls))
                self.assertEqual(1, len(email.calls))

    def test_auto_routing_respects_custom_target_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            with SQLiteRepository(db_path) as repo:
                repo.upsert_case(
                    case_code="CASE-AUTO-CUSTOM",
                    title="Custom sample",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                digest = NotificationService(repo).build_daily_digest(as_of=date(2026, 7, 3))

                line = RecordingDelivery("line")
                discord = RecordingDelivery("discord")
                slack = RecordingDelivery("slack")
                email = RecordingDelivery("email")
                delivery = AutoRoutingNotificationDelivery(
                    {
                        "line": line,
                        "discord": discord,
                        "slack": slack,
                        "email": email,
                    },
                    urgent_targets=("slack",),
                    warning_targets=("discord",),
                )

                result = self._run_async(delivery.send(digest))

                self.assertEqual("auto:slack", result.destination)
                self.assertEqual(2, result.delivered_count)
                self.assertEqual(0, len(line.calls))
                self.assertEqual(0, len(discord.calls))
                self.assertEqual(1, len(slack.calls))
                self.assertEqual(0, len(email.calls))

    def _run_async(self, awaitable):  # noqa: ANN001
        import asyncio

        return asyncio.run(awaitable)


class FakeSMTPClient:
    def __init__(self) -> None:
        self.starttls_calls = 0
        self.login_calls: list[tuple[str, str]] = []
        self.sent_messages: list[EmailMessage] = []

    def __enter__(self) -> "FakeSMTPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def starttls(self) -> None:
        self.starttls_calls += 1

    def login(self, username: str, password: str) -> None:
        self.login_calls.append((username, password))

    def send_message(self, email: EmailMessage) -> None:
        self.sent_messages.append(email)


class RecordingDelivery:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[NotificationBatch] = []

    async def send(self, digest):
        self.calls.append(digest)
        return type(
            "DeliveryResultStub",
            (),
            {
                "delivered_count": len(digest.notifications),
                "destination": self.name,
                "message": self.name,
            },
        )()


if __name__ == "__main__":
    unittest.main()
