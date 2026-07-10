from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request, urlopen


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

    def probe_connection(self, *, timeout: float = 5.0) -> dict[str, str | int]:
        """Check whether the configured InsForge base URL is reachable.

        This intentionally probes only transport reachability. It does not assume any
        specific InsForge database endpoint yet, so it can distinguish missing
        configuration from network-level failures without pretending the repository
        adapter is fully implemented.
        """
        request = Request(self.config.base_url, method="GET")
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - controlled config URL
                status = getattr(response, "status", None) or response.getcode()
                return {
                    "status": "ready",
                    "http_status": int(status),
                    "base_url": self.config.base_url,
                }
        except HTTPError as exc:
            return {
                "status": "ready",
                "http_status": int(exc.code),
                "base_url": self.config.base_url,
            }
        except (URLError, Exception) as exc:
            raise ConnectionError(f"Unable to reach InsForge repository base URL: {self.config.base_url}") from exc

