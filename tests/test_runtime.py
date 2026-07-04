from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
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
                insforge_storage_namespace="demo",
            )

            with self.assertRaises(NotImplementedError):
                create_storage(settings)


if __name__ == "__main__":
    unittest.main()
