from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import load_settings
from app.runtime import create_repository, create_storage
from app.services.demo_pack import build_demo_pack_guide, seed_line_field_organization_pack
from app.services.ingestion import IngestionService


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed and inspect the LINE現場整理 demo pack.",
        formatter_class=_HelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = False

    seed_parser = subparsers.add_parser("seed", help="Seed demo data for the LINE現場整理 pack.")
    seed_parser.add_argument("--output", type=Path, default=None, help="Optional file to write the JSON payload to.")

    status_parser = subparsers.add_parser("status", help="Show the current LINE現場整理 demo pack guide.")
    status_parser.add_argument("--output", type=Path, default=None, help="Optional file to write the JSON payload to.")

    return parser


def run(argv: list[str] | None = None, *, stream=None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else sys.argv[1:])
    output = stream if stream is not None else sys.stdout
    settings = load_settings()
    repository = create_repository(settings)
    storage = create_storage(settings)
    ingestion_service = IngestionService(settings, repository, storage)
    try:
        if args.command == "seed":
            payload = seed_line_field_organization_pack(settings, repository, ingestion_service)
        else:
            payload = build_demo_pack_guide(repository, settings)
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        if getattr(args, "output", None):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, file=output)
        return 0
    finally:
        repository.close()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
