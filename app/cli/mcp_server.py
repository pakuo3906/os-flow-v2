from __future__ import annotations

from app.config import load_settings
from app.mcp.server import MCPServer, run_stdio_server
from app.runtime import create_repository


def main() -> None:
    settings = load_settings()
    repository = create_repository(settings)
    try:
        run_stdio_server(MCPServer(repository))
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()
