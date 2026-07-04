from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.domain.models import IngestionRequest
from app.repositories.base import Repository
from app.services.extraction import extract_text
from app.storage.base import StorageAdapter


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_name(name: str) -> str:
    return Path(name).name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class IngestionResult:
    case_code: str
    case_id: int
    document_id: int
    content_hash: str
    original_storage_key: str
    rag_storage_keys: list[str]


class IngestionService:
    def __init__(self, settings: Settings, repository: Repository, storage: StorageAdapter) -> None:
        self.settings = settings
        self.repository = repository
        self.storage = storage

    def ingest(self, request: IngestionRequest) -> IngestionResult:
        case = self.repository.upsert_case(
            case_code=request.case_code,
            title=request.title,
            client_name=request.client_name,
            due_date=request.due_date,
            invoice_status=request.invoice_status,
            output_status=request.output_status,
        )
        job = self.repository.create_processing_job(job_type="ingestion", case_id=case.id, job_status="running")
        try:
            content_hash = _hash_bytes(request.content)
            original_storage_key = f"originals/{case.case_code}/{content_hash[:12]}_{_safe_name(request.filename)}"
            self.storage.put_bytes(original_storage_key, request.content, request.mime_type)

            document = self.repository.register_document(
                case_id=case.id,
                source_type=request.source_type,
                source_path=request.source_path,
                storage_key=original_storage_key,
                filename=request.filename,
                mime_type=request.mime_type,
                content_hash=content_hash,
                size_bytes=len(request.content),
            )

            rag_storage_keys: list[str] = []
            provided_text = request.extracted_text.strip() if request.extracted_text and request.extracted_text.strip() else None
            extracted_text = provided_text or extract_text(request.filename, request.content, request.mime_type)
            if extracted_text is not None:
                text_key = f"extracted_text/{case.case_code}/{document.id}.txt"
                self.storage.put_bytes(text_key, extracted_text.encode("utf-8"), "text/plain")
                artifact = self.repository.register_artifact(
                    document_id=document.id,
                    artifact_type="raw_text",
                    storage_key=text_key,
                    content_hash=_hash_bytes(extracted_text.encode("utf-8")),
                    generator="ingestion:provided" if provided_text is not None else "ingestion:auto_extracted",
                )
                rag_payload = {
                    "artifact_id": artifact.id,
                    "chunk_id": "chunk-1",
                    "title": request.title or request.filename,
                    "body_text": extracted_text,
                    "metadata_json": {
                        "case_code": case.case_code,
                        "filename": request.filename,
                        "extraction_mode": "provided" if provided_text is not None else "auto",
                    },
                    "content_hash": artifact.content_hash,
                }
                rag_key = f"rag/{case.case_code}/{document.id}.json"
                self.storage.put_bytes(rag_key, json.dumps([rag_payload], ensure_ascii=False, indent=2).encode("utf-8"), "application/json")
                self.repository.replace_rag_entries_for_document(document.id, [rag_payload])
                rag_storage_keys.append(rag_key)

            if request.structured_json is not None:
                json_key = f"structured_json/{case.case_code}/{document.id}.json"
                payload = json.dumps(request.structured_json, ensure_ascii=False, indent=2).encode("utf-8")
                self.storage.put_bytes(json_key, payload, "application/json")
                self.repository.register_artifact(
                    document_id=document.id,
                    artifact_type="structured_json",
                    storage_key=json_key,
                    content_hash=_hash_bytes(payload),
                    generator="ingestion",
                )

            if request.output_html is not None:
                output_key = f"outputs/{case.case_code}/{document.id}.html"
                payload = request.output_html.encode("utf-8")
                self.storage.put_bytes(output_key, payload, "text/html")
                self.repository.register_artifact(
                    document_id=document.id,
                    artifact_type="output_html",
                    storage_key=output_key,
                    content_hash=_hash_bytes(payload),
                    generator="ingestion",
                )

            self.repository.update_processing_job(
                job.id,
                document_id=document.id,
                job_status="completed",
                finished_at=_now(),
            )
            self.repository.record_operation_log(
                event_type="ingestion_completed",
                entity_type="document",
                entity_id=document.id,
                case_id=case.id,
                document_id=document.id,
                message=f"Ingestion completed: {request.filename}",
                metadata_json={
                    "content_hash": content_hash,
                    "original_storage_key": original_storage_key,
                    "rag_storage_keys": rag_storage_keys,
                },
            )
            return IngestionResult(
                case_code=case.case_code,
                case_id=case.id,
                document_id=document.id,
                content_hash=content_hash,
                original_storage_key=original_storage_key,
                rag_storage_keys=rag_storage_keys,
            )
        except Exception as exc:
            try:
                self.repository.update_processing_job(
                    job.id,
                    job_status="failed",
                    error_code="INGESTION_FAILED",
                    error_message=str(exc),
                    finished_at=_now(),
                )
            except Exception:
                pass
            raise
