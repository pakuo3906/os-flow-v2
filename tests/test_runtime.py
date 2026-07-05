from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.config import load_settings
from app.repositories.sqlite import SQLiteRepository
from app.runtime import create_repository, create_storage
from app.storage.local import LocalFileStorageAdapter


class RuntimeFactoryTests(unittest.TestCase):
    def test_create_repository_defaults_to_sqlite(self) -> None:
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

            repository = create_repository(settings)
            try:
                self.assertIsInstance(repository, SQLiteRepository)
            finally:
                repository.close()

    def test_create_storage_defaults_to_local(self) -> None:
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

            storage = create_storage(settings)
            self.assertIsInstance(storage, LocalFileStorageAdapter)

    def test_create_repository_accepts_insforge_placeholder(self) -> None:
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
                repository_backend="insforge",
                insforge_base_url="https://example.insforge.invalid",
                insforge_api_key="insforge-key",
                insforge_database_url="postgresql://example",
                insforge_project_id="project-123",
            )

            with self.assertRaises(NotImplementedError):
                create_repository(settings)

    def test_create_storage_accepts_insforge_placeholder(self) -> None:
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
                storage_backend="insforge",
                insforge_base_url="https://example.insforge.invalid",
                insforge_api_key="insforge-key",
                insforge_storage_bucket="bucket-1",
                insforge_storage_namespace="demo",
            )

            with self.assertRaises(NotImplementedError):
                create_storage(settings)

    def test_create_repository_reports_supported_backends(self) -> None:
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
                repository_backend="unknown",
            )

            with self.assertRaises(NotImplementedError) as context:
                create_repository(settings)

            self.assertIn("supported: sqlite, insforge", str(context.exception))

    def test_create_storage_reports_supported_backends(self) -> None:
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
                storage_backend="unknown",
            )

            with self.assertRaises(NotImplementedError) as context:
                create_storage(settings)

            self.assertIn("supported: local, insforge", str(context.exception))

    def test_create_repository_normalizes_backend_name(self) -> None:
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
                repository_backend=" SQLITE ",
            )

            repository = create_repository(settings)
            try:
                self.assertIsInstance(repository, SQLiteRepository)
            finally:
                repository.close()

    def test_create_storage_normalizes_backend_name(self) -> None:
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
                storage_backend=" LOCAL ",
            )

            storage = create_storage(settings)
            self.assertIsInstance(storage, LocalFileStorageAdapter)

    def test_sqlite_repository_close_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = SQLiteRepository(root / "data" / "app.db")
            repository.close()
            repository.close()

    def test_sqlite_repository_connection_rejects_after_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = SQLiteRepository(root / "data" / "app.db")
            self.assertFalse(repository.is_closed)
            repository.close()
            self.assertTrue(repository.is_closed)

            with self.assertRaises(RuntimeError) as context:
                _ = repository.connection

            self.assertIn("SQLiteRepository is closed", str(context.exception))

    def test_create_repository_reports_missing_insforge_config(self) -> None:
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
                repository_backend="insforge",
            )

            with self.assertRaises(ValueError) as context:
                create_repository(settings)

            self.assertIn("INSFORGE_BASE_URL", str(context.exception))
            self.assertIn("INSFORGE_API_KEY", str(context.exception))
            self.assertIn("INSFORGE_DATABASE_URL", str(context.exception))
            self.assertIn("INSFORGE_PROJECT_ID", str(context.exception))

    def test_create_storage_reports_missing_insforge_config(self) -> None:
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
                storage_backend="insforge",
            )

            with self.assertRaises(ValueError) as context:
                create_storage(settings)

            self.assertIn("INSFORGE_BASE_URL", str(context.exception))
            self.assertIn("INSFORGE_API_KEY", str(context.exception))
            self.assertIn("INSFORGE_STORAGE_BUCKET", str(context.exception))
            self.assertIn("INSFORGE_STORAGE_NAMESPACE", str(context.exception))

    def test_load_settings_exposes_insforge_placeholder_values(self) -> None:
        env = {
            "APP_ENV": "test",
            "DATABASE_PATH": "data/app.db",
            "STORAGE_ROOT": "storage",
            "OUTPUT_ROOT": "output",
            "TEMP_ROOT": "temp",
            "RAG_ROOT": "storage/rag",
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
                settings = load_settings()

        self.assertEqual("https://example.insforge.invalid", settings.insforge_base_url)
        self.assertEqual("insforge-key", settings.insforge_api_key)
        self.assertEqual("postgresql://example", settings.insforge_database_url)
        self.assertEqual("project-123", settings.insforge_project_id)
        self.assertEqual("bucket-1", settings.insforge_storage_bucket)
        self.assertEqual("namespace-1", settings.insforge_storage_namespace)
        self.assertEqual("https://example.insforge.invalid/.well-known/jwks.json", settings.insforge_auth_jwks_url)
        self.assertEqual("https://example.insforge.invalid/mcp", settings.insforge_mcp_base_url)


if __name__ == "__main__":
    unittest.main()
