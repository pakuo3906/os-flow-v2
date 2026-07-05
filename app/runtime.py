from __future__ import annotations

from app.config import Settings
from app.repositories.insforge import InsForgeRepository
from app.repositories.sqlite import SQLiteRepository
from app.storage.insforge import InsForgeStorageAdapter
from app.storage.local import LocalFileStorageAdapter


def _normalize_backend_name(value: str) -> str:
    return value.strip().lower()


def _require_insforge_repository_config(settings: Settings) -> None:
    missing = []
    for name, value in {
        "INSFORGE_BASE_URL": settings.insforge_base_url,
        "INSFORGE_API_KEY": settings.insforge_api_key,
        "INSFORGE_DATABASE_URL": settings.insforge_database_url,
        "INSFORGE_PROJECT_ID": settings.insforge_project_id,
    }.items():
        if not (value or "").strip():
            missing.append(name)
    if missing:
        raise ValueError(
            "InsForge repository backend requires these environment variables: " + ", ".join(missing)
        )


def _require_insforge_storage_config(settings: Settings) -> None:
    missing = []
    for name, value in {
        "INSFORGE_BASE_URL": settings.insforge_base_url,
        "INSFORGE_API_KEY": settings.insforge_api_key,
        "INSFORGE_STORAGE_BUCKET": settings.insforge_storage_bucket,
        "INSFORGE_STORAGE_NAMESPACE": settings.insforge_storage_namespace,
    }.items():
        if not (value or "").strip():
            missing.append(name)
    if missing:
        raise ValueError(
            "InsForge storage backend requires these environment variables: " + ", ".join(missing)
        )


def create_repository(settings: Settings):
    backend = _normalize_backend_name(settings.repository_backend)
    if backend == "sqlite":
        return SQLiteRepository(settings.database_path)
    if backend == "insforge":
        _require_insforge_repository_config(settings)
        return InsForgeRepository(
            base_url=settings.insforge_base_url,
            api_key=settings.insforge_api_key,
            database_url=settings.insforge_database_url,
            project_id=settings.insforge_project_id,
        )
    raise NotImplementedError("Unsupported repository backend: " + settings.repository_backend + " (supported: sqlite, insforge)")


def create_storage(settings: Settings):
    backend = _normalize_backend_name(settings.storage_backend)
    if backend == "local":
        return LocalFileStorageAdapter(settings.storage_root)
    if backend == "insforge":
        _require_insforge_storage_config(settings)
        return InsForgeStorageAdapter(
            base_url=settings.insforge_base_url,
            api_key=settings.insforge_api_key,
            storage_bucket=settings.insforge_storage_bucket,
            storage_namespace=settings.insforge_storage_namespace,
            storage_root=settings.storage_root,
        )
    raise NotImplementedError("Unsupported storage backend: " + settings.storage_backend + " (supported: local, insforge)")
