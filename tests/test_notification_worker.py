from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.cli.notification_worker import build_parser, run
from app.domain.models import NotificationDeliveryLog
from app.repositories.sqlite import SQLiteRepository


class NotificationWorkerTests(unittest.TestCase):
    def test_dry_run_auto_reports_routes_without_sending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                repo.upsert_case(
                    case_code="CASE-DRY-RUN",
                    title="Dry run sample",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                fake_app = self._build_fake_app(
                    repo,
                    notification_webhook_url="https://discord.test/webhook",
                    notification_line_channel_access_token="line-token",
                    notification_line_recipient_ids=("U1234567890",),
                )
                buffer = io.StringIO()
                with patch("app.cli.notification_worker.create_app", return_value=fake_app), patch(
                    "app.cli.notification_worker.deliver_digest"
                ) as deliver_mock:
                    exit_code = run(["--as-of", "2026-07-03", "--dry-run", "--deliver-to", "auto"], stream=buffer)

                self.assertEqual(0, exit_code)
                deliver_mock.assert_not_called()
                output = buffer.getvalue()
                self.assertIn("[dry-run] auto routing would use: line, discord", output)
                self.assertIn("Notification Digest", output)
                self.assertIn("CASE-DRY-RUN", output)
            finally:
                repo.close()

    def test_dry_run_explicit_target_reports_missing_configuration_without_sending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                repo.upsert_case(
                    case_code="CASE-DRY-RUN-EXPLICIT",
                    title="Dry run sample",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                fake_app = self._build_fake_app(repo)
                buffer = io.StringIO()
                with patch("app.cli.notification_worker.create_app", return_value=fake_app), patch(
                    "app.cli.notification_worker.deliver_digest"
                ) as deliver_mock:
                    exit_code = run(["--as-of", "2026-07-03", "--dry-run", "--deliver-to", "discord-webhook"], stream=buffer)

                self.assertEqual(0, exit_code)
                deliver_mock.assert_not_called()
                output = buffer.getvalue()
                self.assertIn("[dry-run] Discord webhook delivery is not configured.", output)
                self.assertIn("CASE-DRY-RUN-EXPLICIT", output)
            finally:
                repo.close()

    def test_successful_delivery_is_recorded_in_delivery_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                repo.upsert_case(
                    case_code="CASE-DELIVERY-LOG",
                    title="Delivery log sample",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                fake_app = self._build_fake_app(
                    _RepositoryProxy(repo),
                    notification_webhook_url="https://discord.test/webhook",
                    notification_line_channel_access_token="line-token",
                    notification_line_recipient_ids=("U1234567890",),
                )
                buffer = io.StringIO()

                async def fake_deliver(digest, delivery):  # noqa: ANN001
                    return SimpleNamespace(
                        destination="auto:line,discord",
                        delivered_count=len(digest.notifications),
                        message="line: line_messaging_api | discord: discord_webhook",
                    )

                with patch("app.cli.notification_worker.create_app", return_value=fake_app), patch(
                    "app.cli.notification_worker.deliver_digest",
                    side_effect=fake_deliver,
                ):
                    exit_code = run(["--as-of", "2026-07-03", "--deliver-to", "auto"], stream=buffer)

                self.assertEqual(0, exit_code)
                self.assertEqual(1, repo.count_notification_deliveries())
                rows = repo.list_notification_deliveries()
                self.assertEqual(1, len(rows))
                row = rows[0]
                self.assertIsInstance(row, NotificationDeliveryLog)
                self.assertEqual("auto", row.deliver_to)
                self.assertEqual("auto:line,discord", row.destination)
                self.assertEqual("success", row.status)
                self.assertGreaterEqual(row.delivered_count, 1)
                self.assertIn("notification_count", row.metadata_json)
                self.assertIn("line: line_messaging_api", row.message)
                self.assertIn("CASE-DELIVERY-LOG", buffer.getvalue())
            finally:
                repo.close()

    def test_delivery_retries_before_succeeding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                repo.upsert_case(
                    case_code="CASE-RETRY",
                    title="Retry sample",
                    status="in_progress",
                    due_date="2026-07-03",
                    invoice_status="pending",
                )
                fake_app = self._build_fake_app(
                    _RepositoryProxy(repo),
                    notification_webhook_url="https://discord.test/webhook",
                    notification_line_channel_access_token="line-token",
                    notification_line_recipient_ids=("U1234567890",),
                )
                buffer = io.StringIO()
                attempts = {"count": 0}

                async def flaky_deliver(digest, delivery):  # noqa: ANN001
                    attempts["count"] += 1
                    if attempts["count"] == 1:
                        raise RuntimeError("temporary network issue")
                    return SimpleNamespace(
                        destination="auto:line,discord",
                        delivered_count=len(digest.notifications),
                        message="line: line_messaging_api | discord: discord_webhook",
                    )

                with patch("app.cli.notification_worker.create_app", return_value=fake_app), patch(
                    "app.cli.notification_worker.deliver_digest",
                    side_effect=flaky_deliver,
                ):
                    exit_code = run(
                        [
                            "--as-of",
                            "2026-07-03",
                            "--deliver-to",
                            "auto",
                            "--retry-attempts",
                            "1",
                            "--retry-delay-seconds",
                            "0",
                        ],
                        stream=buffer,
                    )

                self.assertEqual(0, exit_code)
                self.assertEqual(2, attempts["count"])
                rows = repo.list_notification_deliveries()
                self.assertEqual(1, len(rows))
                row = rows[0]
                self.assertIn('"attempts": 2', row.metadata_json)
                self.assertEqual("success", row.status)
                self.assertEqual("auto:line,discord", row.destination)
            finally:
                repo.close()

    def test_report_mode_outputs_markdown_without_sending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                self._seed_delivery_history(repo)
                fake_app = self._build_fake_app(repo)
                buffer = io.StringIO()

                with patch("app.cli.notification_worker.create_app", return_value=fake_app), patch(
                    "app.cli.notification_worker.deliver_digest"
                ) as deliver_mock:
                    exit_code = run(
                        [
                            "report",
                            "--report-format",
                            "markdown",
                            "--report-granularity",
                            "week",
                            "--report-minimum-total-for-attention",
                            "2",
                        ],
                        stream=buffer,
                    )

                self.assertEqual(0, exit_code)
                deliver_mock.assert_not_called()
                output = buffer.getvalue()
                self.assertIn("O's flow Notification Delivery Report", output)
                self.assertIn("scope total: 3", output)
                self.assertIn("attention targets: auto", output)
                self.assertIn("2026-W27", output)
                self.assertIn("needs attention: True", output)
            finally:
                repo.close()

    def test_report_mode_writes_json_output_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                self._seed_delivery_history(repo)
                fake_app = self._build_fake_app(repo)
                output_path = Path(tmp) / "report.json"
                buffer = io.StringIO()

                with patch("app.cli.notification_worker.create_app", return_value=fake_app), patch(
                    "app.cli.notification_worker.deliver_digest"
                ) as deliver_mock:
                    exit_code = run(
                        [
                            "report",
                            "--report-format",
                            "json",
                            "--report-granularity",
                            "week",
                            "--report-minimum-total-for-attention",
                            "2",
                            "--output",
                            str(output_path),
                        ],
                        stream=buffer,
                    )

                self.assertEqual(0, exit_code)
                deliver_mock.assert_not_called()
                self.assertTrue(output_path.exists())
                file_text = output_path.read_text(encoding="utf-8")
                self.assertIn('"scope_total": 3', file_text)
                self.assertIn('"granularity": "week"', file_text)
                self.assertIn('"attention_targets": [', file_text)
                self.assertIn('"scope_total": 3', buffer.getvalue())
            finally:
                repo.close()

    def test_line_webhook_report_mode_outputs_markdown_without_sending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                fake_app = self._build_fake_app(repo)
                buffer = io.StringIO()

                with patch("app.cli.notification_worker.create_app", return_value=fake_app):
                    exit_code = run(
                        [
                            "line-webhook-report",
                            "--report-format",
                            "markdown",
                            "--report-limit",
                            "5",
                        ],
                        stream=buffer,
                    )

                self.assertEqual(0, exit_code)
                output = buffer.getvalue()
                self.assertIn("O's flow LINE Webhook Report", output)
                self.assertIn("pending backlog count: 0", output)
                self.assertIn("No recent events.", output)
            finally:
                repo.close()

    def test_line_webhook_report_mode_writes_json_output_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                fake_app = self._build_fake_app(repo)
                output_path = Path(tmp) / "line-webhook-report.json"
                buffer = io.StringIO()

                with patch("app.cli.notification_worker.create_app", return_value=fake_app):
                    exit_code = run(
                        [
                            "line-webhook-report",
                            "--report-format",
                            "json",
                            "--output",
                            str(output_path),
                        ],
                        stream=buffer,
                    )

                self.assertEqual(0, exit_code)
                self.assertTrue(output_path.exists())
                file_text = output_path.read_text(encoding="utf-8")
                self.assertIn('"pending_backlog_count": 0', file_text)
                self.assertIn('"needs_attention": false', file_text)
                self.assertIn('"pending_backlog_count": 0', file_text)
                self.assertIn('"recent_events": [', file_text)
            finally:
                repo.close()

    def test_line_webhook_alerts_mode_outputs_markdown_without_sending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                self._seed_line_webhook_pending_events(repo)
                fake_app = self._build_fake_app(repo)
                buffer = io.StringIO()

                with patch("app.cli.notification_worker.create_app", return_value=fake_app), patch(
                    "app.cli.notification_worker._deliver_with_retry"
                ) as deliver_mock:
                    exit_code = run(
                        [
                            "line-webhook-alerts",
                            "--report-format",
                            "markdown",
                            "--report-limit",
                            "5",
                            "--report-pending-backlog-threshold",
                            "1",
                        ],
                        stream=buffer,
                    )

                self.assertEqual(0, exit_code)
                deliver_mock.assert_not_called()
                output = buffer.getvalue()
                self.assertIn("O's flow LINE Webhook Alerts", output)
                self.assertIn("pending backlog count: 2", output)
                self.assertIn("attention reason:", output)
                self.assertIn("Latest Pending", output)
                self.assertIn("Pending LINE webhook video", output)
            finally:
                repo.close()

    def test_line_webhook_alerts_mode_dry_run_reports_routes_without_sending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                self._seed_line_webhook_pending_events(repo)
                fake_app = self._build_fake_app(repo)
                buffer = io.StringIO()

                with patch("app.cli.notification_worker.create_app", return_value=fake_app):
                    exit_code = run(
                        [
                            "line-webhook-alerts",
                            "--dry-run",
                            "--deliver-to",
                            "auto",
                            "--report-pending-backlog-threshold",
                            "1",
                        ],
                        stream=buffer,
                    )

                self.assertEqual(0, exit_code)
                output = buffer.getvalue()
                self.assertIn("[dry-run] auto routing has no configured delivery targets.", output)
                self.assertIn("O's flow LINE Webhook Alerts", output)
            finally:
                repo.close()

    def test_line_webhook_alerts_mode_writes_json_output_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                self._seed_line_webhook_pending_events(repo)
                fake_app = self._build_fake_app(repo)
                output_path = Path(tmp) / "line-webhook-alerts.json"
                buffer = io.StringIO()

                with patch("app.cli.notification_worker.create_app", return_value=fake_app), patch(
                    "app.cli.notification_worker._deliver_with_retry"
                ) as deliver_mock:
                    exit_code = run(
                        [
                            "line-webhook-alerts",
                            "--report-format",
                            "json",
                            "--report-limit",
                            "5",
                            "--report-pending-backlog-threshold",
                            "1",
                            "--output",
                            str(output_path),
                        ],
                        stream=buffer,
                    )

                self.assertEqual(0, exit_code)
                deliver_mock.assert_not_called()
                self.assertTrue(output_path.exists())
                file_text = output_path.read_text(encoding="utf-8")
                self.assertIn('"as_of": "', file_text)
                self.assertIn('"notifications": [', file_text)
                self.assertIn('"line_webhook_alert"', file_text)
                self.assertIn('"severity": "warning"', file_text)
                self.assertIn("Latest pending LINE webhook event: Pending LINE webhook video", file_text)
                self.assertIn('"notifications": [', buffer.getvalue())
            finally:
                repo.close()

    def test_line_webhook_alerts_mode_records_delivery_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                self._seed_line_webhook_pending_events(repo)
                fake_app = self._build_fake_app(_RepositoryProxy(repo))
                buffer = io.StringIO()

                with patch("app.cli.notification_worker.create_app", return_value=fake_app), patch(
                    "app.cli.notification_worker._deliver_with_retry",
                    return_value=(SimpleNamespace(destination="stdout", delivered_count=2, message="line-webhook-alerts delivered"), 1),
                ) as retry_mock:
                    exit_code = run(
                        [
                            "line-webhook-alerts",
                            "--report-format",
                            "markdown",
                            "--report-limit",
                            "5",
                            "--report-pending-backlog-threshold",
                            "1",
                            "--deliver-to",
                            "stdout",
                        ],
                        stream=buffer,
                    )

                self.assertEqual(0, exit_code)
                retry_mock.assert_called_once()
                self.assertEqual(1, repo.count_notification_deliveries())
                row = repo.list_notification_deliveries()[0]
                self.assertEqual("stdout", row.deliver_to)
                self.assertEqual("stdout", row.destination)
                self.assertEqual(2, row.delivered_count)
                self.assertIn("line-webhook-alerts delivered", row.message)
                self.assertIn('"attempts": 1', row.metadata_json)
            finally:
                repo.close()

    def test_legacy_report_flag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteRepository(Path(tmp) / "app.db")
            try:
                fake_app = self._build_fake_app(repo)
                buffer = io.StringIO()
                with patch("app.cli.notification_worker.create_app", return_value=fake_app):
                    with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                        run(["--report"], stream=buffer)
            finally:
                repo.close()

    def test_parser_help_shows_digest_and_report_commands(self) -> None:
        help_text = build_parser().format_help()
        self.assertIn("digest", help_text)
        self.assertIn("report", help_text)
        self.assertIn("line-webhook-report", help_text)
        self.assertIn("line-webhook-alerts", help_text)
        self.assertIn("python -m app.cli.notification_worker digest --dry-run --deliver-to auto", help_text)
        self.assertIn("python -m app.cli.notification_worker report --report-format markdown --report-granularity week", help_text)
        self.assertIn(
            "python -m app.cli.notification_worker line-webhook-report --report-format markdown",
            help_text,
        )

    def test_run_help_shows_top_level_commands(self) -> None:
        buffer = io.StringIO()

        exit_code = run(["--help"], stream=buffer)

        self.assertEqual(0, exit_code)
        help_text = buffer.getvalue()
        self.assertIn("digest", help_text)
        self.assertIn("report", help_text)
        self.assertIn("line-webhook-report", help_text)
        self.assertIn("line-webhook-alerts", help_text)
        self.assertIn(
            "Generate a daily notification digest, a notification delivery report, or LINE webhook alerts.",
            help_text,
        )

    def _build_fake_app(
        self,
        repository,
        *,
        notification_webhook_url: str | None = None,
        notification_line_channel_access_token: str | None = None,
        notification_line_recipient_ids: tuple[str, ...] = (),
        notification_slack_webhook_url: str | None = None,
    ):
        settings = SimpleNamespace(
            notification_webhook_url=notification_webhook_url,
            notification_webhook_username=None,
            notification_webhook_avatar_url=None,
            notification_slack_webhook_url=notification_slack_webhook_url,
            notification_line_api_base_url="https://api.line.me",
            notification_line_channel_access_token=notification_line_channel_access_token,
            notification_line_recipient_ids=notification_line_recipient_ids,
            notification_email_smtp_host=None,
            notification_email_smtp_port=587,
            notification_email_smtp_username=None,
            notification_email_smtp_password=None,
            notification_email_use_tls=True,
            notification_email_from=None,
            notification_email_recipients=(),
            notification_email_subject_prefix="O's flow",
            notification_auto_urgent_targets=("line", "discord"),
            notification_auto_warning_targets=("slack", "email"),
        )
        state = SimpleNamespace(repository=repository, settings=settings)
        return SimpleNamespace(state=state)

    def _seed_delivery_history(self, repository) -> None:  # noqa: ANN001
        with patch("app.repositories.sqlite._now", side_effect=["2026-07-02T09:00:00+00:00"]):
            repository.record_notification_delivery(
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
            repository.record_notification_delivery(
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
            repository.record_notification_delivery(
                deliver_to="slack-webhook",
                destination="slack_webhook",
                delivered_count=1,
                digest_as_of="2026-07-03",
                due_lookahead_days=1,
                invoice_lookahead_days=7,
                status="success",
                message="slack: slack_webhook",
                metadata_json={"notification_count": 1, "attempts": 1},
            )

    def _seed_line_webhook_pending_events(self, repository) -> None:  # noqa: ANN001
        with patch("app.repositories.sqlite._now", side_effect=["2026-07-04T08:00:00+00:00"]):
            repository.record_operation_log(
                event_type="line_webhook_pending",
                entity_type="line_webhook",
                message="Pending LINE webhook audio",
                case_id=None,
                document_id=None,
                metadata_json={
                    "status": "pending",
                    "event_type": "audio",
                    "message_type": "audio",
                    "event_summary": "Audio content is still being prepared by LINE.",
                    "line_case_code": "LINE-INBOX",
                },
            )
        with patch("app.repositories.sqlite._now", side_effect=["2026-07-04T09:00:00+00:00"]):
            repository.record_operation_log(
                event_type="line_webhook_pending",
                entity_type="line_webhook",
                message="Pending LINE webhook video",
                case_id=None,
                document_id=None,
                metadata_json={
                    "status": "pending",
                    "event_type": "video",
                    "message_type": "video",
                    "event_summary": "Video content is still being prepared by LINE.",
                    "line_case_code": "LINE-INBOX",
                },
            )


class _RepositoryProxy:
    def __init__(self, repository) -> None:  # noqa: ANN001
        self._repository = repository

    def __getattr__(self, name: str):  # noqa: ANN401
        return getattr(self._repository, name)

    def close(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
