from __future__ import annotations

import os
import sys


def build_command(run_mode: str) -> list[str]:
    normalized = (run_mode or "api").strip().lower()
    if normalized in {"api", "server"}:
        return ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
    if normalized in {"mcp", "mcp-server"}:
        return [sys.executable, "-m", "app.cli.mcp_server"]
    if normalized in {"notification-worker", "worker", "digest", "notification-digest"}:
        return [sys.executable, "-m", "app.cli.notification_worker", "digest"]
    if normalized in {"notification-report", "report"}:
        return [sys.executable, "-m", "app.cli.notification_worker", "report"]
    if normalized in {"notification-line-webhook-report", "line-webhook-report", "line-webhook-reporting"}:
        return [sys.executable, "-m", "app.cli.notification_worker", "line-webhook-report"]
    if normalized in {"notification-line-webhook-alerts", "line-webhook-alerts", "line-webhook-alert"}:
        return [sys.executable, "-m", "app.cli.notification_worker", "line-webhook-alerts"]
    raise ValueError(f"Unsupported APP_RUN_MODE: {run_mode}")


def main() -> None:
    command = build_command(os.getenv("APP_RUN_MODE", "api"))
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
