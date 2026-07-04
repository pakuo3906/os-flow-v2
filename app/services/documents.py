from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from app.config import Settings
from app.domain.models import RagEntry
from app.repositories.base import Repository
from app.services.extraction import extract_text
from app.storage.base import StorageAdapter


@dataclass(frozen=True)
class DocumentDeletionResult:
    document_id: int
    case_id: int
    removed_storage_keys: list[str]


@dataclass(frozen=True)
class DocumentReprocessResult:
    document_id: int
    case_id: int
    extracted_text_length: int
    rag_entries: list[RagEntry]


@dataclass(frozen=True)
class DocumentReassignResult:
    document_id: int
    previous_case_id: int
    new_case_id: int
    moved_storage_keys: list[str]


@dataclass(frozen=True)
class DocumentReprocessItem:
    document_id: int
    case_id: int
    status: str
    error_message: str | None = None
    extracted_text_length: int | None = None


@dataclass(frozen=True)
class BatchReprocessResult:
    case_id: int
    total_documents: int
    successful_documents: int
    failed_documents: int
    items: list[DocumentReprocessItem]


@dataclass(frozen=True)
class BatchDocumentReprocessResult:
    total_documents: int
    successful_documents: int
    failed_documents: int
    items: list[DocumentReprocessItem]


class DocumentService:
    def __init__(self, settings: Settings, repository: Repository, storage: StorageAdapter) -> None:
        self.settings = settings
        self.repository = repository
        self.storage = storage

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _hash_bytes(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _normalize_limit(self, limit: int, *, max_limit: int = 100) -> int:
        return max(1, min(limit, max_limit))

    def _move_storage_key(self, source_key: str, target_key: str, moved_storage_keys: list[str]) -> None:
        if source_key == target_key:
            return
        if not self.storage.exists(source_key):
            return
        data = self.storage.get_bytes(source_key)
        self.storage.put_bytes(target_key, data, None)
        self.storage.delete(source_key)
        moved_storage_keys.append(f"{source_key} -> {target_key}")

    def _rebase_case_code(self, storage_key: str, old_case_code: str, new_case_code: str) -> str:
        prefix, separator, suffix = storage_key.partition("/")
        if not separator:
            return storage_key
        case_code, separator, remainder = suffix.partition("/")
        if not separator or case_code != old_case_code:
            return storage_key
        return f"{prefix}/{new_case_code}/{remainder}"

    def delete_document(self, document_id: int) -> DocumentDeletionResult:
        document = self.repository.get_document(document_id)
        case = self.repository.get_case(document.case_id)
        if case is None:
            raise RuntimeError(f"Case not found for document: {document_id}")

        job = self.repository.create_processing_job(
            job_type="document_delete",
            case_id=case.id,
            document_id=document.id,
            job_status="running",
        )

        removed_storage_keys: list[str] = []
        try:
            rag_storage_key = f"rag/{case.case_code}/{document.id}.json"
            self.storage.delete(document.storage_key)
            removed_storage_keys.append(document.storage_key)
            self.storage.delete(rag_storage_key)
            removed_storage_keys.append(rag_storage_key)

            case_detail = self.repository.get_case_detail(case.id)
            if case_detail is not None:
                for artifact in case_detail.artifacts:
                    if artifact.document_id != document.id:
                        continue
                    self.storage.delete(artifact.storage_key)
                    removed_storage_keys.append(artifact.storage_key)

            self.repository.mark_document_deleted(document.id)
            self.repository.record_operation_log(
                event_type="document_deleted",
                entity_type="document",
                entity_id=document.id,
                case_id=case.id,
                document_id=document.id,
                message=f"Document deleted: {document.filename}",
                metadata_json={
                    "removed_storage_keys": removed_storage_keys,
                },
            )
            self.repository.update_processing_job(
                job.id,
                job_status="completed",
                finished_at=self._now(),
            )
            return DocumentDeletionResult(
                document_id=document.id,
                case_id=case.id,
                removed_storage_keys=removed_storage_keys,
            )
        except Exception as exc:
            try:
                self.repository.update_processing_job(
                    job.id,
                    job_status="failed",
                    error_code="DOCUMENT_DELETE_FAILED",
                    error_message=str(exc),
                    finished_at=self._now(),
                )
            except Exception:
                pass
            raise

    def reprocess_document(self, document_id: int) -> DocumentReprocessResult:
        document = self.repository.get_document(document_id)
        case = self.repository.get_case(document.case_id)
        if case is None:
            raise RuntimeError(f"Case not found for document: {document_id}")

        job = self.repository.create_processing_job(
            job_type="document_reprocess",
            case_id=case.id,
            document_id=document.id,
            job_status="running",
        )

        try:
            content = self.storage.get_bytes(document.storage_key)
            extracted_text = extract_text(document.filename, content, document.mime_type)
            if extracted_text is None:
                raise RuntimeError("No text could be extracted from the document.")

            text_key = f"extracted_text/{case.case_code}/{document.id}.txt"
            self.storage.put_bytes(text_key, extracted_text.encode("utf-8"), "text/plain")
            extracted_bytes = extracted_text.encode("utf-8")
            artifact = self.repository.register_artifact(
                document_id=document.id,
                artifact_type="raw_text",
                storage_key=text_key,
                content_hash=self._hash_bytes(extracted_bytes),
                generator="document_reprocess:auto_extracted",
            )
            rag_entry = {
                "artifact_id": artifact.id,
                "chunk_id": "chunk-1",
                "title": document.filename,
                "body_text": extracted_text,
                "metadata_json": {
                    "case_code": case.case_code,
                    "filename": document.filename,
                    "reprocess": True,
                },
                "content_hash": artifact.content_hash,
            }
            rag_entries = self.repository.replace_rag_entries_for_document(document.id, [rag_entry])
            self.repository.upsert_case(
                case_code=case.case_code,
                title=case.title,
                client_name=case.client_name,
                status=case.status,
                due_date=case.due_date,
                invoice_status=case.invoice_status,
                output_status=case.output_status,
                last_processed_at=self._now(),
            )
            self.repository.record_operation_log(
                event_type="document_reprocessed",
                entity_type="document",
                entity_id=document.id,
                case_id=case.id,
                document_id=document.id,
                message=f"Document reprocessed: {document.filename}",
                metadata_json={
                    "extracted_text_length": len(extracted_text),
                    "rag_entry_count": len(rag_entries),
                },
            )
            self.repository.update_processing_job(
                job.id,
                job_status="completed",
                finished_at=self._now(),
            )
            return DocumentReprocessResult(
                document_id=document.id,
                case_id=case.id,
                extracted_text_length=len(extracted_text),
                rag_entries=rag_entries,
            )
        except Exception as exc:
            try:
                self.repository.update_processing_job(
                    job.id,
                    job_status="failed",
                    error_code="DOCUMENT_REPROCESS_FAILED",
                    error_message=str(exc),
                    finished_at=self._now(),
                )
            except Exception:
                pass
            raise

    def reprocess_documents_for_case(self, case_id: int, *, limit: int = 50) -> BatchReprocessResult:
        case = self.repository.get_case(case_id)
        if case is None:
            raise RuntimeError(f"Case not found: {case_id}")

        limit = self._normalize_limit(limit)
        documents = self.repository.list_documents(case_id=case.id, is_deleted=False, limit=limit)
        items: list[DocumentReprocessItem] = []
        successful_documents = 0
        failed_documents = 0

        for document in documents:
            try:
                result = self.reprocess_document(document.id)
                items.append(
                    DocumentReprocessItem(
                        document_id=document.id,
                        case_id=case.id,
                        status="completed",
                        extracted_text_length=result.extracted_text_length,
                    )
                )
                successful_documents += 1
            except Exception as exc:
                items.append(
                    DocumentReprocessItem(
                        document_id=document.id,
                        case_id=case.id,
                        status="failed",
                        error_message=str(exc),
                    )
                )
                failed_documents += 1

        self.repository.record_operation_log(
            event_type="case_reprocessed",
            entity_type="case",
            entity_id=case.id,
            case_id=case.id,
            message=f"Case reprocessed: {case.case_code}",
            metadata_json={
                "total_documents": len(documents),
                "successful_documents": successful_documents,
                "failed_documents": failed_documents,
            },
        )
        return BatchReprocessResult(
            case_id=case.id,
            total_documents=len(documents),
            successful_documents=successful_documents,
            failed_documents=failed_documents,
            items=items,
        )

    def reprocess_documents_by_ids(self, document_ids: list[int]) -> BatchDocumentReprocessResult:
        if not document_ids:
            raise RuntimeError("Document ids must not be empty.")

        items: list[DocumentReprocessItem] = []
        successful_documents = 0
        failed_documents = 0
        for document_id in document_ids:
            try:
                result = self.reprocess_document(document_id)
                items.append(
                    DocumentReprocessItem(
                        document_id=document_id,
                        case_id=result.case_id,
                        status="completed",
                        extracted_text_length=result.extracted_text_length,
                    )
                )
                successful_documents += 1
                self.repository.record_operation_log(
                    event_type="document_batch_reprocessed",
                    entity_type="document",
                    entity_id=document_id,
                    case_id=result.case_id,
                    document_id=document_id,
                    message=f"Document batch reprocessed: {document_id}",
                    metadata_json={
                        "status": "completed",
                        "extracted_text_length": result.extracted_text_length,
                    },
                )
            except Exception as exc:
                try:
                    document = self.repository.get_document(document_id)
                    case_id = document.case_id
                except Exception:
                    case_id = None
                items.append(
                    DocumentReprocessItem(
                        document_id=document_id,
                        case_id=case_id,
                        status="failed",
                        error_message=str(exc),
                    )
                )
                failed_documents += 1
                self.repository.record_operation_log(
                    event_type="document_batch_reprocessed",
                    entity_type="document",
                    entity_id=document_id,
                    case_id=case_id,
                    document_id=document_id,
                    message=f"Document batch reprocessed failed: {document_id}",
                    metadata_json={
                        "status": "failed",
                        "error_message": str(exc),
                    },
                )
        return BatchDocumentReprocessResult(
            total_documents=len(document_ids),
            successful_documents=successful_documents,
            failed_documents=failed_documents,
            items=items,
        )

    def reassign_document(self, document_id: int, target_case_id: int) -> DocumentReassignResult:
        document = self.repository.get_document(document_id)
        source_case = self.repository.get_case(document.case_id)
        if source_case is None:
            raise RuntimeError(f"Case not found for document: {document_id}")
        target_case = self.repository.get_case(target_case_id)
        if target_case is None:
            raise RuntimeError(f"Target case not found: {target_case_id}")
        if source_case.id == target_case.id:
            return DocumentReassignResult(
                document_id=document.id,
                previous_case_id=source_case.id,
                new_case_id=target_case.id,
                moved_storage_keys=[],
            )

        job = self.repository.create_processing_job(
            job_type="document_reassign",
            case_id=target_case.id,
            document_id=document.id,
            job_status="running",
        )

        moved_storage_keys: list[str] = []
        planned_moves: list[tuple[str, str]] = []
        case_detail = self.repository.get_case_detail(source_case.id)
        artifact_storage_keys: dict[int, str] = {}
        try:
            new_document_storage_key = self._rebase_case_code(document.storage_key, source_case.case_code, target_case.case_code)
            planned_moves.append((document.storage_key, new_document_storage_key))

            if case_detail is not None:
                for artifact in case_detail.artifacts:
                    if artifact.document_id != document.id:
                        continue
                    target_storage_key = self._rebase_case_code(artifact.storage_key, source_case.case_code, target_case.case_code)
                    planned_moves.append((artifact.storage_key, target_storage_key))
                    artifact_storage_keys[artifact.id] = target_storage_key

            rag_source_key = f"rag/{source_case.case_code}/{document.id}.json"
            rag_target_key = f"rag/{target_case.case_code}/{document.id}.json"
            planned_moves.append((rag_source_key, rag_target_key))

            for source_key, target_key in planned_moves:
                self._move_storage_key(source_key, target_key, moved_storage_keys)

            self.repository.reassign_document(
                document.id,
                case_id=target_case.id,
                storage_key=new_document_storage_key,
                artifact_storage_keys=artifact_storage_keys,
            )
            self.repository.record_operation_log(
                event_type="document_reassigned",
                entity_type="document",
                entity_id=document.id,
                case_id=target_case.id,
                document_id=document.id,
                message=f"Document reassigned: {document.filename}",
                metadata_json={
                    "previous_case_id": source_case.id,
                    "new_case_id": target_case.id,
                    "moved_storage_keys": moved_storage_keys,
                },
            )
            self.repository.update_processing_job(
                job.id,
                job_status="completed",
                finished_at=self._now(),
            )
            return DocumentReassignResult(
                document_id=document.id,
                previous_case_id=source_case.id,
                new_case_id=target_case.id,
                moved_storage_keys=moved_storage_keys,
            )
        except Exception as exc:
            for source_key, target_key in reversed(planned_moves):
                if self.storage.exists(target_key):
                    self.storage.put_bytes(source_key, self.storage.get_bytes(target_key), None)
                    self.storage.delete(target_key)
            try:
                self.repository.update_processing_job(
                    job.id,
                    job_status="failed",
                    error_code="DOCUMENT_REASSIGN_FAILED",
                    error_message=str(exc),
                    finished_at=self._now(),
                )
            except Exception:
                pass
            raise
