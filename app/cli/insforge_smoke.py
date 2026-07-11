from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
from typing import Any

from app.config import load_settings
from app.runtime import create_repository, create_storage


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _extract_missing_variables(exc: ValueError) -> list[str]:
    text = str(exc)
    marker = ": "
    if marker not in text:
        return []
    return [item.strip() for item in text.split(marker, 1)[1].split(",") if item.strip()]


def _build_probe_result(label: str, settings, *, factory, backend_field: str) -> dict[str, Any]:  # noqa: ANN001
    probe_settings = replace(settings, **{backend_field: "insforge"})
    try:
        adapter = factory(probe_settings)
    except ValueError as exc:
        return {
            "label": label,
            "requested_backend": "insforge",
            "status": "missing_config",
            "missing": _extract_missing_variables(exc),
            "error": str(exc),
        }
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return {
            "label": label,
            "requested_backend": "insforge",
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    probe = getattr(adapter, "probe_connection", None)
    if not callable(probe):
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
        return {
            "label": label,
            "requested_backend": "insforge",
            "status": "not_implemented",
            "error": "Adapter does not expose a connection probe yet.",
        }

    try:
        probe_result = probe()
    except ConnectionError as exc:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
        return {
            "label": label,
            "requested_backend": "insforge",
            "status": "connection_failed",
            "error": str(exc),
        }
    except NotImplementedError as exc:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
        return {
            "label": label,
            "requested_backend": "insforge",
            "status": "not_implemented",
            "error": str(exc),
        }

    close = getattr(adapter, "close", None)
    if callable(close):
        close()
    payload = {
        "label": label,
        "requested_backend": "insforge",
        "status": "ready",
    }
    if isinstance(probe_result, dict):
        payload.update(_serialize(probe_result))
    else:
        payload["probe_result"] = _serialize(probe_result)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe the InsForge repository and storage adapters using the current environment.",
        formatter_class=_HelpFormatter,
    )
    return parser


def run(argv: list[str] | None = None, *, stream=None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
    _ = args
    output = stream if stream is not None else sys.stdout
    settings = load_settings()
    payload = {
        "checked_at": _now(),
        "app_env": settings.app_env,
        "configured_backends": {
            "repository": settings.repository_backend,
            "storage": settings.storage_backend,
        },
        "repository": _build_probe_result(
            "repository",
            settings,
            factory=create_repository,
            backend_field="repository_backend",
        ),
        "storage": _build_probe_result(
            "storage",
            settings,
            factory=create_storage,
            backend_field="storage_backend",
        ),
    }
    print(json.dumps(_serialize(payload), ensure_ascii=False, indent=2), file=output)
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()

