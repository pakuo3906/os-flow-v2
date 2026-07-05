from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InsForgeRepositoryConfig:
    base_url: str
    api_key: str
    database_url: str
    project_id: str


class InsForgeRepository:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        database_url: str,
        project_id: str,
    ) -> None:
        self.config = InsForgeRepositoryConfig(
            base_url=base_url,
            api_key=api_key,
            database_url=database_url,
            project_id=project_id,
        )
        raise NotImplementedError("InsForge repository adapter is not implemented yet.")
