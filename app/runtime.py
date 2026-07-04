from __future__ import annotations

from app.config import Settings
from app.repositories.insforge import InsForgeRepository
from app.repositories.sqlite import SQLiteRepository
from app.storage.insforge import InsForgeStorageAdapter
from app.storage.local import LocalFileStorageAdapter


def create_repository(settings: Settings):
    backend = settings.repository_backend.strip().lower()
    if backend == "sqlite":
        return SQLiteRepository(settings.database_path)
    if backend == "insforge":
        return InsForgeRepository(
            database_url=settings.insforge_database_url,
            project_id=settings.insforge_project_id,
        )
    raise NotImplementedError(f"Unsupported repository backend: {settings.repository_backend}")


def create_storage(settings: Settings):
    backend = settings.storage_backend.strip().lower()
    if backend == "local":
        return LocalFileStorageAdapter(settings.storage_root)
    if backend == "insforge":
        return InsForgeStorageAdapter(
            storage_namespace=settings.insforge_storage_namespace,
            storage_root=settings.storage_root,
        )
    raise NotImplementedError(f"Unsupported storage backend: {settings.storage_backend}")
