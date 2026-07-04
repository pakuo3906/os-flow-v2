from __future__ import annotations

from typing import Protocol

from app.domain.models import (
    Artifact,
    Case,
    CaseDetail,
    Document,
    NotificationDeliveryLog,
    NotificationDeliveryTrend,
    OperationLog,
    ProcessingJob,
    RagEntry,
)


class Repository(Protocol):
    def upsert_case(
        self,
        *,
        case_code: str,
        title: str,
        client_name: str | None = None,
        status: str = "new",
        due_date: str | None = None,
        invoice_status: str = "unbilled",
        output_status: str = "pending",
        last_processed_at: str | None = None,
    ) -> Case:
        ...

    def update_case(
        self,
        case_id: int,
        *,
        title: str | None = None,
        client_name: str | None = None,
        status: str | None = None,
        due_date: str | None = None,
        invoice_status: str | None = None,
        output_status: str | None = None,
        last_processed_at: str | None = None,
        record_log: bool = True,
    ) -> Case:
        ...

    def reassign_document(
        self,
        document_id: int,
        *,
        case_id: int,
        storage_key: str,
        artifact_storage_keys: dict[int, str] | None = None,
    ) -> Document:
        ...

    def get_case(self, case_id: int) -> Case | None:
        ...

    def get_document(self, document_id: int) -> Document | None:
        ...

    def list_documents(
        self,
        *,
        case_id: int | None = None,
        source_type: str | None = None,
        is_deleted: bool | None = None,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        ...

    def count_documents(
        self,
        *,
        case_id: int | None = None,
        source_type: str | None = None,
        is_deleted: bool | None = None,
        query: str = "",
    ) -> int:
        ...

    def get_case_detail(self, case_id: int) -> CaseDetail | None:
        ...

    def search_cases(
        self,
        query: str = "",
        status: str | None = None,
        due_before: str | None = None,
        invoice_status: str | None = None,
        output_status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Case]:
        ...

    def count_cases(
        self,
        query: str = "",
        status: str | None = None,
        due_before: str | None = None,
        invoice_status: str | None = None,
        output_status: str | None = None,
    ) -> int:
        ...

    def list_due_tasks(self, until_date: str, status: str | None = None, limit: int = 50, offset: int = 0) -> list[Case]:
        ...

    def count_due_tasks(self, until_date: str, status: str | None = None) -> int:
        ...

    def list_invoices(
        self,
        invoice_status: str | None = None,
        due_before: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Case]:
        ...

    def count_invoices(self, invoice_status: str | None = None, due_before: str | None = None) -> int:
        ...

    def register_document(
        self,
        *,
        case_id: int,
        source_type: str,
        storage_key: str,
        filename: str,
        mime_type: str | None,
        content_hash: str,
        size_bytes: int,
        source_path: str | None = None,
    ) -> Document:
        ...

    def mark_document_deleted(self, document_id: int) -> None:
        ...

    def register_artifact(
        self,
        *,
        document_id: int,
        artifact_type: str,
        storage_key: str,
        content_hash: str,
        generator: str,
    ) -> Artifact:
        ...

    def replace_rag_entries_for_document(self, document_id: int, entries: list[dict[str, object]]) -> list[RagEntry]:
        ...

    def search_rag(self, query: str, case_id: int | None = None, limit: int = 20, offset: int = 0) -> list[RagEntry]:
        ...

    def count_rag(self, query: str, case_id: int | None = None) -> int:
        ...

    def create_processing_job(
        self,
        *,
        job_type: str,
        case_id: int | None = None,
        document_id: int | None = None,
        job_status: str = "running",
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ProcessingJob:
        ...

    def update_processing_job(
        self,
        job_id: int,
        *,
        case_id: int | None = None,
        document_id: int | None = None,
        job_status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        finished_at: str | None = None,
    ) -> ProcessingJob:
        ...

    def get_processing_job(self, job_id: int) -> ProcessingJob | None:
        ...

    def list_processing_jobs(
        self,
        *,
        case_id: int | None = None,
        document_id: int | None = None,
        job_type: str | None = None,
        job_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProcessingJob]:
        ...

    def count_processing_jobs(
        self,
        *,
        case_id: int | None = None,
        document_id: int | None = None,
        job_type: str | None = None,
        job_status: str | None = None,
    ) -> int:
        ...

    def record_operation_log(
        self,
        *,
        event_type: str,
        entity_type: str,
        message: str,
        entity_id: int | None = None,
        case_id: int | None = None,
        document_id: int | None = None,
        metadata_json: dict[str, object] | None = None,
    ) -> OperationLog:
        ...

    def list_operation_logs(
        self,
        *,
        case_id: int | None = None,
        document_id: int | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OperationLog]:
        ...

    def count_operation_logs(
        self,
        *,
        case_id: int | None = None,
        document_id: int | None = None,
        event_type: str | None = None,
    ) -> int:
        ...

    def record_notification_delivery(
        self,
        *,
        deliver_to: str,
        destination: str,
        delivered_count: int,
        digest_as_of: str,
        due_lookahead_days: int,
        invoice_lookahead_days: int,
        status: str = "success",
        message: str = "",
        error_message: str | None = None,
        metadata_json: dict[str, object] | None = None,
    ) -> NotificationDeliveryLog:
        ...

    def list_notification_deliveries(
        self,
        *,
        deliver_to: str | None = None,
        status: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NotificationDeliveryLog]:
        ...

    def count_notification_deliveries(
        self,
        *,
        deliver_to: str | None = None,
        status: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> int:
        ...

    def list_notification_delivery_trends(
        self,
        *,
        deliver_to: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit_days: int = 30,
        failure_rate_threshold: float = 0.25,
        minimum_total_for_attention: int = 5,
    ) -> list[NotificationDeliveryTrend]:
        ...
