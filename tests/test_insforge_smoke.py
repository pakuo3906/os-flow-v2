from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.cli.insforge_smoke import run


class InsForgeSmokeTests(unittest.TestCase):
    def test_run_reports_missing_configuration_for_repository_and_storage(self) -> None:
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
            }

            with patch("app.config.load_dotenv", autospec=True, return_value=None):
                with patch.dict(os.environ, env, clear=True):
                    buffer = io.StringIO()
                    exit_code = run([], stream=buffer)

        payload = json.loads(buffer.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual("missing_config", payload["repository"]["status"])
        self.assertEqual("missing_config", payload["storage"]["status"])
        self.assertIn("INSFORGE_BASE_URL", payload["repository"]["missing"])
        self.assertIn("INSFORGE_STORAGE_BUCKET", payload["storage"]["missing"])

    def test_run_reports_connection_failure_when_probe_cannot_reach_backend(self) -> None:
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
            }

            with patch("app.config.load_dotenv", autospec=True, return_value=None):
                with patch.dict(os.environ, env, clear=True):
                    buffer = io.StringIO()
                    exit_code = run([], stream=buffer)

        payload = json.loads(buffer.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual("connection_failed", payload["repository"]["status"])
        self.assertEqual("connection_failed", payload["storage"]["status"])
        self.assertIn("Unable to reach", payload["repository"]["error"])
        self.assertIn("Unable to reach", payload["storage"]["error"])


if __name__ == "__main__":
    unittest.main()

