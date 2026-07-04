from __future__ import annotations

import unittest

from app.cli.entrypoint import build_command


class EntrypointTests(unittest.TestCase):
    def test_build_command_api_mode(self) -> None:
        self.assertEqual(
            ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            build_command("api"),
        )

    def test_build_command_worker_mode(self) -> None:
        command = build_command("notification-worker")

        self.assertEqual("app.cli.notification_worker", command[2])
        self.assertEqual("digest", command[3])

    def test_build_command_report_mode(self) -> None:
        command = build_command("notification-report")

        self.assertEqual("app.cli.notification_worker", command[2])
        self.assertEqual("report", command[3])

    def test_build_command_line_webhook_report_mode(self) -> None:
        command = build_command("notification-line-webhook-report")

        self.assertEqual("app.cli.notification_worker", command[2])
        self.assertEqual("line-webhook-report", command[3])

    def test_build_command_line_webhook_alerts_mode(self) -> None:
        command = build_command("notification-line-webhook-alerts")

        self.assertEqual("app.cli.notification_worker", command[2])
        self.assertEqual("line-webhook-alerts", command[3])

    def test_build_command_rejects_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            build_command("unknown")


if __name__ == "__main__":
    unittest.main()
