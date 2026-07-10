from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.request_context import build_customer_scope_metadata, get_customer_scope
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_pagination(limit: int, offset: int, *, max_limit: int = 100) -> tuple[int, int]:
    return max(1, min(limit, max_limit)), max(0, offset)


def _current_customer_scope_identity() -> tuple[str, str] | None:
    scope = get_customer_scope()
    if not scope or not scope.get("ready"):
        return None
    slug = scope.get("effective_slug")
    name = scope.get("effective_name")
    if not slug or not name:
        return None
    return str(slug), str(name)


def _current_customer_scope_metadata() -> dict[str, object] | None:
    scope = get_customer_scope()
    if not scope or not scope.get("ready"):
        return None
    slug = scope.get("effective_slug")
    name = scope.get("effective_name")
    if not slug or not name:
        return None
    return {
        "slug": str(slug),
        "name": str(name),
        "scope": build_customer_scope_metadata(scope),
    }


class SQLiteRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON;")
        self._closed = False
        self._init_schema()
        self._ensure_case_customer_columns()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("SQLiteRepository is closed.")
        return self._connection

    @property
    def is_closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "SQLiteRepository":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._connection.close()
        self._closed = True

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_code TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                client_name TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                due_date TEXT,
                invoice_status TEXT NOT NULL DEFAULT 'unbilled',
                output_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_processed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                source_path TEXT,
                storage_key TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT,
                content_hash TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(case_id) REFERENCES cases(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS document_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                artifact_type TEXT NOT NULL,
                storage_key TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                generator TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS rag_index_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                artifact_id INTEGER,
                chunk_id TEXT NOT NULL,
                title TEXT NOT NULL,
                body_text TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
                FOREIGN KEY(artifact_id) REFERENCES document_artifacts(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS processing_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER,
                document_id INTEGER,
                job_type TEXT NOT NULL,
                job_status TEXT NOT NULL,
                error_code TEXT,
                error_message TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY(case_id) REFERENCES cases(id) ON DELETE SET NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                case_id INTEGER,
                document_id INTEGER,
                message TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(case_id) REFERENCES cases(id) ON DELETE SET NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS notification_delivery_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deliver_to TEXT NOT NULL,
                destination TEXT NOT NULL,
                delivered_count INTEGER NOT NULL,
                digest_as_of TEXT NOT NULL,
                due_lookahead_days INTEGER NOT NULL,
                invoice_lookahead_days INTEGER NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                error_message TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cases_status_due ON cases(status, due_date);
            CREATE INDEX IF NOT EXISTS idx_cases_invoice ON cases(invoice_status, due_date);
            CREATE INDEX IF NOT EXISTS idx_documents_case_filename ON documents(case_id, filename, version);
            CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash);
            CREATE INDEX IF NOT EXISTS idx_rag_document_active ON rag_index_entries(document_id, is_active);
            CREATE INDEX IF NOT EXISTS idx_processing_jobs_case_status ON processing_jobs(case_id, job_status, started_at);
            CREATE INDEX IF NOT EXISTS idx_processing_jobs_document_status ON processing_jobs(document_id, job_status, started_at);
            CREATE INDEX IF NOT EXISTS idx_operation_logs_case_created ON operation_logs(case_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operation_logs_document_created ON operation_logs(document_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_notification_delivery_logs_created ON notification_delivery_logs(created_at DESC, id DESC);
            """
        )
        self.connection.commit()

    def _ensure_case_customer_columns(self) -> None:
        existing_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(cases)").fetchall()
        }
        if "customer_slug" not in existing_columns:
            self.connection.execute("ALTER TABLE cases ADD COLUMN customer_slug TEXT")
        if "customer_name" not in existing_columns:
            self.connection.execute("ALTER TABLE cases ADD COLUMN customer_name TEXT")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_cases_customer_slug ON cases(customer_slug, updated_at)")
        self.connection.commit()

    def _scope_case_row(self, row: sqlite3.Row | None) -> bool:
        if row is None:
            return False
        scope = _current_customer_scope_identity()
        if scope is None:
            return True
        return (row["customer_slug"] or None) == scope[0]

    def _apply_case_scope_filters(self, clauses: list[str], params: list[object], *, table_alias: str = "cases") -> None:
        scope = _current_customer_scope_identity()
        if scope is None:
            return
        clauses.append(f"{table_alias}.customer_slug = ?")
        params.append(scope[0])

    def _scope_case_assignment(self, existing_row: sqlite3.Row | None = None) -> tuple[str | None, str | None]:
        scope = _current_customer_scope_identity()
        if scope is not None:
            return scope
        if existing_row is not None:
            return existing_row["customer_slug"], existing_row["customer_name"]
        return None, None

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
        now = _now()
        row = self.connection.execute("SELECT * FROM cases WHERE case_code = ?", (case_code,)).fetchone()
        customer_scope = _current_customer_scope_identity()
        if row is not None and customer_scope is not None and (row["customer_slug"] or None) not in {None, customer_scope[0]}:
            raise RuntimeError(f"Case not found after upsert: {case_code}")
        customer_slug, customer_name = self._scope_case_assignment(row)
        if row:
            self.connection.execute(
                """
                UPDATE cases
                SET title = ?, client_name = ?, customer_slug = ?, customer_name = ?, status = ?, due_date = ?, invoice_status = ?,
                    output_status = ?, updated_at = ?, last_processed_at = ?
                WHERE case_code = ?
                """,
                (
                    title,
                    client_name,
                    customer_slug,
                    customer_name,
                    status,
                    due_date,
                    invoice_status,
                    output_status,
                    now,
                    last_processed_at,
                    case_code,
                ),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO cases (
                    case_code, title, client_name, customer_slug, customer_name, status, due_date, invoice_status,
                    output_status, created_at, updated_at, last_processed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_code,
                    title,
                    client_name,
                    customer_slug,
                    customer_name,
                    status,
                    due_date,
                    invoice_status,
                    output_status,
                    now,
                    now,
                    last_processed_at,
                ),
            )
        self.connection.commit()
        return self._get_case_by_code(case_code)

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
        updates = []
        params: list[object] = []
        existing_row = self.connection.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if existing_row is None:
            raise RuntimeError(f"Case not found: {case_id}")
        if not self._scope_case_row(existing_row):
            raise RuntimeError(f"Case not found: {case_id}")
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if client_name is not None:
            updates.append("client_name = ?")
            params.append(client_name)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if due_date is not None:
            updates.append("due_date = ?")
            params.append(due_date)
        if invoice_status is not None:
            updates.append("invoice_status = ?")
            params.append(invoice_status)
        if output_status is not None:
            updates.append("output_status = ?")
            params.append(output_status)
        if last_processed_at is not None:
            updates.append("last_processed_at = ?")
            params.append(last_processed_at)
        if not updates:
            existing = self.get_case(case_id)
            if existing is None:
                raise RuntimeError(f"Case not found: {case_id}")
            return existing

        updates.append("updated_at = ?")
        params.append(_now())
        params.append(case_id)
        self.connection.execute(
            f"UPDATE cases SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.connection.commit()
        updated = self.get_case(case_id)
        if updated is None:
            raise RuntimeError(f"Case not found after update: {case_id}")
        if record_log:
            self.record_operation_log(
                event_type="case_updated",
                entity_type="case",
                entity_id=updated.id,
                case_id=updated.id,
                message=f"Case updated: {updated.case_code}",
                metadata_json={
                    "changed_fields": [name for name, value in (
                        ("title", title),
                        ("client_name", client_name),
                        ("status", status),
                        ("due_date", due_date),
                        ("invoice_status", invoice_status),
                        ("output_status", output_status),
                        ("last_processed_at", last_processed_at),
                    ) if value is not None]
                },
            )
        return updated

    def get_case(self, case_id: int) -> Case | None:
        row = self.connection.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return self._row_to_case(row) if self._scope_case_row(row) else None

    def get_case_detail(self, case_id: int) -> CaseDetail | None:
        case = self.get_case(case_id)
        if case is None:
            return None
        document_rows = self.connection.execute(
            "SELECT * FROM documents WHERE case_id = ? ORDER BY created_at DESC, id DESC",
            (case_id,),
        ).fetchall()
        documents = [self._row_to_document(row) for row in document_rows]
        document_ids = [document.id for document in documents]
        artifacts: list[Artifact] = []
        rag_entries: list[RagEntry] = []
        if document_ids:
            placeholders = ", ".join("?" for _ in document_ids)
            artifact_rows = self.connection.execute(
                f"SELECT * FROM document_artifacts WHERE document_id IN ({placeholders}) ORDER BY created_at DESC, id DESC",
                document_ids,
            ).fetchall()
            artifacts = [self._row_to_artifact(row) for row in artifact_rows]
            rag_rows = self.connection.execute(
                f"SELECT * FROM rag_index_entries WHERE document_id IN ({placeholders}) AND is_active = 1 ORDER BY updated_at DESC, id DESC",
                document_ids,
            ).fetchall()
            rag_entries = [self._row_to_rag_entry(row) for row in rag_rows]
        return CaseDetail(case=case, documents=documents, artifacts=artifacts, rag_entries=rag_entries)

    def _get_case_by_code(self, case_code: str) -> Case:
        row = self.connection.execute("SELECT * FROM cases WHERE case_code = ?", (case_code,)).fetchone()
        if row is None or not self._scope_case_row(row):
            raise RuntimeError(f"Case not found after upsert: {case_code}")
        return self._row_to_case(row)

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
        limit, offset = _normalize_pagination(limit, offset)
        clauses = []
        params: list[object] = []
        self._apply_case_scope_filters(clauses, params)
        if query:
            clauses.append("(case_code LIKE ? OR title LIKE ? OR client_name LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if status:
            clauses.append("status = ?")
            params.append(status)
        if due_before:
            clauses.append("due_date IS NOT NULL AND due_date <= ?")
            params.append(due_before)
        if invoice_status:
            clauses.append("invoice_status = ?")
            params.append(invoice_status)
        if output_status:
            clauses.append("output_status = ?")
            params.append(output_status)
        sql = "SELECT * FROM cases"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.connection.execute(sql, params).fetchall()
        return [self._row_to_case(row) for row in rows]

    def count_cases(
        self,
        query: str = "",
        status: str | None = None,
        due_before: str | None = None,
        invoice_status: str | None = None,
        output_status: str | None = None,
    ) -> int:
        clauses = []
        params: list[object] = []
        self._apply_case_scope_filters(clauses, params)
        if query:
            clauses.append("(case_code LIKE ? OR title LIKE ? OR client_name LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if status:
            clauses.append("status = ?")
            params.append(status)
        if due_before:
            clauses.append("due_date IS NOT NULL AND due_date <= ?")
            params.append(due_before)
        if invoice_status:
            clauses.append("invoice_status = ?")
            params.append(invoice_status)
        if output_status:
            clauses.append("output_status = ?")
            params.append(output_status)
        sql = "SELECT COUNT(*) AS total FROM cases"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        row = self.connection.execute(sql, params).fetchone()
        return int(row["total"]) if row else 0

    def list_due_tasks(self, until_date: str, status: str | None = None, limit: int = 50, offset: int = 0) -> list[Case]:
        limit, offset = _normalize_pagination(limit, offset)
        clauses = ["due_date IS NOT NULL", "due_date <= ?"]
        params: list[object] = [until_date]
        self._apply_case_scope_filters(clauses, params)
        if status:
            clauses.append("status = ?")
            params.append(status)
        sql = "SELECT * FROM cases WHERE " + " AND ".join(clauses) + " ORDER BY due_date ASC"
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.connection.execute(sql, params).fetchall()
        return [self._row_to_case(row) for row in rows]

    def count_due_tasks(self, until_date: str, status: str | None = None) -> int:
        clauses = ["due_date IS NOT NULL", "due_date <= ?"]
        params: list[object] = [until_date]
        self._apply_case_scope_filters(clauses, params)
        if status:
            clauses.append("status = ?")
            params.append(status)
        sql = "SELECT COUNT(*) AS total FROM cases WHERE " + " AND ".join(clauses)
        row = self.connection.execute(sql, params).fetchone()
        return int(row["total"]) if row else 0

    def list_invoices(
        self,
        invoice_status: str | None = None,
        due_before: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Case]:
        limit, offset = _normalize_pagination(limit, offset)
        clauses = []
        params: list[object] = []
        self._apply_case_scope_filters(clauses, params)
        if invoice_status:
            clauses.append("invoice_status = ?")
            params.append(invoice_status)
        if due_before:
            clauses.append("due_date IS NOT NULL AND due_date <= ?")
            params.append(due_before)
        sql = "SELECT * FROM cases"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.connection.execute(sql, params).fetchall()
        return [self._row_to_case(row) for row in rows]

    def count_invoices(self, invoice_status: str | None = None, due_before: str | None = None) -> int:
        clauses = []
        params: list[object] = []
        self._apply_case_scope_filters(clauses, params)
        if invoice_status:
            clauses.append("invoice_status = ?")
            params.append(invoice_status)
        if due_before:
            clauses.append("due_date IS NOT NULL AND due_date <= ?")
            params.append(due_before)
        sql = "SELECT COUNT(*) AS total FROM cases"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        row = self.connection.execute(sql, params).fetchone()
        return int(row["total"]) if row else 0

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
        now = _now()
        case = self.get_case(case_id)
        if case is None:
            raise RuntimeError(f"Case not found: {case_id}")
        row = self.connection.execute(
            """
            SELECT * FROM documents
            WHERE case_id = ? AND filename = ? AND content_hash = ? AND is_deleted = 0
            ORDER BY version DESC
            LIMIT 1
            """,
            (case_id, filename, content_hash),
        ).fetchone()
        if row:
            return self._row_to_document(row)

        version_row = self.connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS max_version FROM documents WHERE case_id = ? AND filename = ?",
            (case_id, filename),
        ).fetchone()
        version = int(version_row["max_version"]) + 1 if version_row else 1

        cursor = self.connection.execute(
            """
            INSERT INTO documents (
                case_id, source_type, source_path, storage_key, filename, mime_type,
                content_hash, size_bytes, version, is_deleted, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (case_id, source_type, source_path, storage_key, filename, mime_type, content_hash, size_bytes, version, now, now),
        )
        self.connection.commit()
        return self.get_document(cursor.lastrowid)

    def get_document(self, document_id: int) -> Document:
        row = self.connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"Document not found: {document_id}")
        if self.get_case(row["case_id"]) is None:
            raise RuntimeError(f"Document not found: {document_id}")
        return self._row_to_document(row)

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
        limit, offset = _normalize_pagination(limit, offset)
        clauses = []
        params: list[object] = []
        if case_id is not None:
            clauses.append("documents.case_id = ?")
            params.append(case_id)
        self._apply_case_scope_filters(clauses, params, table_alias="cases")
        if source_type is not None:
            clauses.append("source_type = ?")
            params.append(source_type)
        if is_deleted is not None:
            clauses.append("is_deleted = ?")
            params.append(1 if is_deleted else 0)
        if query:
            clauses.append("(filename LIKE ? OR source_path LIKE ? OR storage_key LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        sql = "SELECT documents.* FROM documents JOIN cases ON cases.id = documents.case_id"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY documents.updated_at DESC, documents.id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.connection.execute(sql, params).fetchall()
        return [self._row_to_document(row) for row in rows]

    def count_documents(
        self,
        *,
        case_id: int | None = None,
        source_type: str | None = None,
        is_deleted: bool | None = None,
        query: str = "",
    ) -> int:
        clauses = []
        params: list[object] = []
        if case_id is not None:
            clauses.append("documents.case_id = ?")
            params.append(case_id)
        self._apply_case_scope_filters(clauses, params, table_alias="cases")
        if source_type is not None:
            clauses.append("source_type = ?")
            params.append(source_type)
        if is_deleted is not None:
            clauses.append("is_deleted = ?")
            params.append(1 if is_deleted else 0)
        if query:
            clauses.append("(filename LIKE ? OR source_path LIKE ? OR storage_key LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        sql = "SELECT COUNT(*) AS total FROM documents JOIN cases ON cases.id = documents.case_id"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        row = self.connection.execute(sql, params).fetchone()
        return int(row["total"]) if row else 0

    def mark_document_deleted(self, document_id: int) -> None:
        now = _now()
        document = self.get_document(document_id)
        self.connection.execute(
            """
            UPDATE documents
            SET is_deleted = 1, deleted_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, document_id),
        )
        self.connection.execute(
            """
            UPDATE rag_index_entries
            SET is_active = 0, updated_at = ?
            WHERE document_id = ?
            """,
            (now, document_id),
        )
        self.connection.commit()

    def reassign_document(
        self,
        document_id: int,
        *,
        case_id: int,
        storage_key: str,
        artifact_storage_keys: dict[int, str] | None = None,
    ) -> Document:
        now = _now()
        artifact_storage_keys = artifact_storage_keys or {}
        source_case_id_row = self.connection.execute("SELECT case_id FROM documents WHERE id = ?", (document_id,)).fetchone()
        if source_case_id_row is None:
            raise RuntimeError(f"Document not found: {document_id}")
        source_case_id = int(source_case_id_row["case_id"])
        if self.get_case(source_case_id) is None or self.get_case(case_id) is None:
            raise RuntimeError(f"Document not found: {document_id}")
        cursor = self.connection.execute(
            """
            UPDATE documents
            SET case_id = ?, storage_key = ?, updated_at = ?
            WHERE id = ?
            """,
            (case_id, storage_key, now, document_id),
        )
        if cursor.rowcount == 0:
            self.connection.rollback()
            raise RuntimeError(f"Document not found: {document_id}")
        for artifact_id, updated_storage_key in artifact_storage_keys.items():
            self.connection.execute(
                "UPDATE document_artifacts SET storage_key = ? WHERE id = ?",
                (updated_storage_key, artifact_id),
            )
        self.connection.execute(
            "UPDATE processing_jobs SET case_id = ? WHERE document_id = ?",
            (case_id, document_id),
        )
        self.connection.execute(
            "UPDATE cases SET updated_at = ? WHERE id IN (?, ?)",
            (now, source_case_id, case_id),
        )
        self.connection.commit()
        updated = self.get_document(document_id)
        if updated is None:
            raise RuntimeError(f"Document not found after reassignment: {document_id}")
        return updated

    def register_artifact(
        self,
        *,
        document_id: int,
        artifact_type: str,
        storage_key: str,
        content_hash: str,
        generator: str,
    ) -> Artifact:
        now = _now()
        cursor = self.connection.execute(
            """
            INSERT INTO document_artifacts (
                document_id, artifact_type, storage_key, content_hash, generator, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (document_id, artifact_type, storage_key, content_hash, generator, now),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM document_artifacts WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if row is None:
            raise RuntimeError("Failed to read inserted artifact.")
        return self._row_to_artifact(row)

    def replace_rag_entries_for_document(self, document_id: int, entries: list[dict[str, object]]) -> list[RagEntry]:
        now = _now()
        self.connection.execute(
            "UPDATE rag_index_entries SET is_active = 0, updated_at = ? WHERE document_id = ?",
            (now, document_id),
        )
        created: list[RagEntry] = []
        for entry in entries:
            cursor = self.connection.execute(
                """
                INSERT INTO rag_index_entries (
                    document_id, artifact_id, chunk_id, title, body_text, metadata_json,
                    content_hash, is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    document_id,
                    entry.get("artifact_id"),
                    str(entry.get("chunk_id", "")),
                    str(entry.get("title", "")),
                    str(entry.get("body_text", "")),
                    json.dumps(entry.get("metadata_json", {}), ensure_ascii=False),
                    str(entry.get("content_hash", "")),
                    now,
                    now,
                ),
            )
            row = self.connection.execute("SELECT * FROM rag_index_entries WHERE id = ?", (cursor.lastrowid,)).fetchone()
            if row is None:
                raise RuntimeError("Failed to read inserted RAG entry.")
            created.append(self._row_to_rag_entry(row))
        self.connection.commit()
        return created

    def search_rag(self, query: str, case_id: int | None = None, limit: int = 20, offset: int = 0) -> list[RagEntry]:
        limit, offset = _normalize_pagination(limit, offset)
        clauses = ["is_active = 1"]
        params: list[object] = []
        if query:
            clauses.append("(title LIKE ? OR body_text LIKE ? OR metadata_json LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if case_id is not None:
            clauses.append("document_id IN (SELECT id FROM documents WHERE case_id = ?)")
            params.append(case_id)
        sql = "SELECT * FROM rag_index_entries WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.connection.execute(sql, params).fetchall()
        return [self._row_to_rag_entry(row) for row in rows]

    def count_rag(self, query: str, case_id: int | None = None) -> int:
        clauses = ["is_active = 1"]
        params: list[object] = []
        if query:
            clauses.append("(title LIKE ? OR body_text LIKE ? OR metadata_json LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if case_id is not None:
            clauses.append("document_id IN (SELECT id FROM documents WHERE case_id = ?)")
            params.append(case_id)
        sql = "SELECT COUNT(*) AS total FROM rag_index_entries WHERE " + " AND ".join(clauses)
        row = self.connection.execute(sql, params).fetchone()
        return int(row["total"]) if row else 0

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
        now = _now()
        cursor = self.connection.execute(
            """
            INSERT INTO processing_jobs (
                case_id, document_id, job_type, job_status, error_code, error_message, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (case_id, document_id, job_type, job_status, error_code, error_message, now),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM processing_jobs WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if row is None:
            raise RuntimeError("Failed to read inserted processing job.")
        return self._row_to_processing_job(row)

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
        updates = []
        params: list[object] = []
        if case_id is not None:
            updates.append("case_id = ?")
            params.append(case_id)
        if document_id is not None:
            updates.append("document_id = ?")
            params.append(document_id)
        if job_status is not None:
            updates.append("job_status = ?")
            params.append(job_status)
        if error_code is not None:
            updates.append("error_code = ?")
            params.append(error_code)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if finished_at is not None:
            updates.append("finished_at = ?")
            params.append(finished_at)
        if not updates:
            existing = self.get_processing_job(job_id)
            if existing is None:
                raise RuntimeError(f"Processing job not found: {job_id}")
            return existing

        params.append(job_id)
        self.connection.execute(
            f"UPDATE processing_jobs SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.connection.commit()
        updated = self.get_processing_job(job_id)
        if updated is None:
            raise RuntimeError(f"Processing job not found after update: {job_id}")
        return updated

    def get_processing_job(self, job_id: int) -> ProcessingJob | None:
        row = self.connection.execute("SELECT * FROM processing_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_processing_job(row) if row else None

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
        limit, offset = _normalize_pagination(limit, offset)
        clauses = []
        params: list[object] = []
        if case_id is not None:
            clauses.append("case_id = ?")
            params.append(case_id)
        if document_id is not None:
            clauses.append("document_id = ?")
            params.append(document_id)
        if job_type is not None:
            clauses.append("job_type = ?")
            params.append(job_type)
        if job_status is not None:
            clauses.append("job_status = ?")
            params.append(job_status)
        sql = "SELECT * FROM processing_jobs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.connection.execute(sql, params).fetchall()
        return [self._row_to_processing_job(row) for row in rows]

    def count_processing_jobs(
        self,
        *,
        case_id: int | None = None,
        document_id: int | None = None,
        job_type: str | None = None,
        job_status: str | None = None,
    ) -> int:
        clauses = []
        params: list[object] = []
        if case_id is not None:
            clauses.append("case_id = ?")
            params.append(case_id)
        if document_id is not None:
            clauses.append("document_id = ?")
            params.append(document_id)
        if job_type is not None:
            clauses.append("job_type = ?")
            params.append(job_type)
        if job_status is not None:
            clauses.append("job_status = ?")
            params.append(job_status)
        sql = "SELECT COUNT(*) AS total FROM processing_jobs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        row = self.connection.execute(sql, params).fetchone()
        return int(row["total"]) if row else 0

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
        now = _now()
        metadata_dict = dict(metadata_json or {})
        customer_scope = build_customer_scope_metadata(get_customer_scope())
        if customer_scope is not None and "customer_scope" not in metadata_dict:
            metadata_dict["customer_scope"] = customer_scope
        metadata = json.dumps(metadata_dict, ensure_ascii=False)
        cursor = self.connection.execute(
            """
            INSERT INTO operation_logs (
                event_type, entity_type, entity_id, case_id, document_id, message, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_type, entity_type, entity_id, case_id, document_id, message, metadata, now),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM operation_logs WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if row is None:
            raise RuntimeError("Failed to read inserted operation log.")
        return self._row_to_operation_log(row)

    def list_operation_logs(
        self,
        *,
        case_id: int | None = None,
        document_id: int | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OperationLog]:
        limit, offset = _normalize_pagination(limit, offset)
        clauses = []
        params: list[object] = []
        if case_id is not None:
            clauses.append("case_id = ?")
            params.append(case_id)
        if document_id is not None:
            clauses.append("document_id = ?")
            params.append(document_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        sql = "SELECT * FROM operation_logs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.connection.execute(sql, params).fetchall()
        return [self._row_to_operation_log(row) for row in rows]

    def get_operation_log(self, operation_log_id: int) -> OperationLog | None:
        row = self.connection.execute("SELECT * FROM operation_logs WHERE id = ?", (operation_log_id,)).fetchone()
        return self._row_to_operation_log(row) if row is not None else None

    def count_operation_logs(
        self,
        *,
        case_id: int | None = None,
        document_id: int | None = None,
        event_type: str | None = None,
    ) -> int:
        clauses = []
        params: list[object] = []
        if case_id is not None:
            clauses.append("case_id = ?")
            params.append(case_id)
        if document_id is not None:
            clauses.append("document_id = ?")
            params.append(document_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        sql = "SELECT COUNT(*) AS total FROM operation_logs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        row = self.connection.execute(sql, params).fetchone()
        return int(row["total"]) if row else 0

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
        now = _now()
        metadata_dict = dict(metadata_json or {})
        customer_scope = build_customer_scope_metadata(get_customer_scope())
        if customer_scope is not None and "customer_scope" not in metadata_dict:
            metadata_dict["customer_scope"] = customer_scope
        metadata = json.dumps(metadata_dict, ensure_ascii=False)
        cursor = self.connection.execute(
            """
            INSERT INTO notification_delivery_logs (
                deliver_to, destination, delivered_count, digest_as_of, due_lookahead_days,
                invoice_lookahead_days, status, message, error_message, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                deliver_to,
                destination,
                delivered_count,
                digest_as_of,
                due_lookahead_days,
                invoice_lookahead_days,
                status,
                message,
                error_message,
                metadata,
                now,
            ),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM notification_delivery_logs WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if row is None:
            raise RuntimeError("Failed to read inserted notification delivery log.")
        return self._row_to_notification_delivery_log(row)

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
        limit, offset = _normalize_pagination(limit, offset)
        clauses = []
        params: list[object] = []
        if deliver_to is not None:
            clauses.append("deliver_to = ?")
            params.append(deliver_to)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if created_after is not None:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before is not None:
            clauses.append("created_at <= ?")
            params.append(created_before)
        sql = "SELECT * FROM notification_delivery_logs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.connection.execute(sql, params).fetchall()
        return [self._row_to_notification_delivery_log(row) for row in rows]

    def get_notification_delivery(self, notification_delivery_id: int) -> NotificationDeliveryLog | None:
        row = self.connection.execute(
            "SELECT * FROM notification_delivery_logs WHERE id = ?",
            (notification_delivery_id,),
        ).fetchone()
        return self._row_to_notification_delivery_log(row) if row is not None else None

    def count_notification_deliveries(
        self,
        *,
        deliver_to: str | None = None,
        status: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> int:
        clauses = []
        params: list[object] = []
        if deliver_to is not None:
            clauses.append("deliver_to = ?")
            params.append(deliver_to)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if created_after is not None:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before is not None:
            clauses.append("created_at <= ?")
            params.append(created_before)
        sql = "SELECT COUNT(*) AS total FROM notification_delivery_logs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        row = self.connection.execute(sql, params).fetchone()
        return int(row["total"]) if row else 0

    def list_notification_delivery_trends(
        self,
        *,
        deliver_to: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        granularity: str = "day",
        limit_days: int = 30,
        failure_rate_threshold: float = 0.25,
        minimum_total_for_attention: int = 5,
    ) -> list[NotificationDeliveryTrend]:
        limit_days = max(1, min(limit_days, 365))
        clauses = []
        params: list[object] = []
        if deliver_to is not None:
            clauses.append("deliver_to = ?")
            params.append(deliver_to)
        if created_after is not None:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before is not None:
            clauses.append("created_at <= ?")
            params.append(created_before)
        sql = "SELECT created_at, status FROM notification_delivery_logs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self.connection.execute(sql, params).fetchall()
        grouped: dict[str, dict[str, int]] = {}
        for row in rows:
            period = _notification_delivery_period(row["created_at"], granularity)
            bucket = grouped.setdefault(period, {"total": 0, "success_total": 0, "failed_total": 0})
            bucket["total"] += 1
            if row["status"] == "success":
                bucket["success_total"] += 1
            elif row["status"] == "failed":
                bucket["failed_total"] += 1
        periods = sorted(grouped.keys(), reverse=True)[:limit_days]
        trends: list[NotificationDeliveryTrend] = []
        for period in periods:
            bucket = grouped[period]
            total = bucket["total"]
            success_total = bucket["success_total"]
            failed_total = bucket["failed_total"]
            failure_rate = round((failed_total / total) if total else 0.0, 4)
            needs_attention = total >= minimum_total_for_attention and failure_rate >= failure_rate_threshold
            trends.append(
                NotificationDeliveryTrend(
                    period=period,
                    granularity=granularity,
                    total=total,
                    success_total=success_total,
                    failed_total=failed_total,
                    failure_rate=failure_rate,
                    needs_attention=needs_attention,
                    attention_reason=(
                        f"failure_rate {failure_rate:.4f} is at or above threshold {failure_rate_threshold:.4f}"
                        if needs_attention
                        else None
                    ),
                )
        )
        return trends

    def _row_to_case(self, row: sqlite3.Row) -> Case:
        return Case(
            id=row["id"],
            case_code=row["case_code"],
            title=row["title"],
            client_name=row["client_name"],
            customer_slug=row["customer_slug"],
            customer_name=row["customer_name"],
            status=row["status"],
            due_date=row["due_date"],
            invoice_status=row["invoice_status"],
            output_status=row["output_status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_processed_at=row["last_processed_at"],
        )

    def _row_to_document(self, row: sqlite3.Row) -> Document:
        return Document(
            id=row["id"],
            case_id=row["case_id"],
            source_type=row["source_type"],
            source_path=row["source_path"],
            storage_key=row["storage_key"],
            filename=row["filename"],
            mime_type=row["mime_type"],
            content_hash=row["content_hash"],
            size_bytes=row["size_bytes"],
            version=row["version"],
            is_deleted=bool(row["is_deleted"]),
            deleted_at=row["deleted_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_artifact(self, row: sqlite3.Row) -> Artifact:
        return Artifact(
            id=row["id"],
            document_id=row["document_id"],
            artifact_type=row["artifact_type"],
            storage_key=row["storage_key"],
            content_hash=row["content_hash"],
            generator=row["generator"],
            created_at=row["created_at"],
        )

    def _row_to_rag_entry(self, row: sqlite3.Row) -> RagEntry:
        return RagEntry(
            id=row["id"],
            document_id=row["document_id"],
            artifact_id=row["artifact_id"],
            chunk_id=row["chunk_id"],
            title=row["title"],
            body_text=row["body_text"],
            metadata_json=row["metadata_json"],
            content_hash=row["content_hash"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_processing_job(self, row: sqlite3.Row) -> ProcessingJob:
        return ProcessingJob(
            id=row["id"],
            case_id=row["case_id"],
            document_id=row["document_id"],
            job_type=row["job_type"],
            job_status=row["job_status"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def _row_to_operation_log(self, row: sqlite3.Row) -> OperationLog:
        return OperationLog(
            id=row["id"],
            event_type=row["event_type"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            case_id=row["case_id"],
            document_id=row["document_id"],
            message=row["message"],
            metadata_json=row["metadata_json"],
            created_at=row["created_at"],
        )

    def _row_to_notification_delivery_log(self, row: sqlite3.Row) -> NotificationDeliveryLog:
        return NotificationDeliveryLog(
            id=row["id"],
            deliver_to=row["deliver_to"],
            destination=row["destination"],
            delivered_count=row["delivered_count"],
            digest_as_of=row["digest_as_of"],
            due_lookahead_days=row["due_lookahead_days"],
            invoice_lookahead_days=row["invoice_lookahead_days"],
            status=row["status"],
            message=row["message"],
            error_message=row["error_message"],
            metadata_json=row["metadata_json"],
            created_at=row["created_at"],
        )


def _notification_delivery_period(created_at: str, granularity: str) -> str:
    delivery_date = datetime.fromisoformat(created_at).date()
    if granularity == "day":
        return delivery_date.isoformat()
    if granularity == "week":
        year, week, _ = delivery_date.isocalendar()
        return f"{year}-W{week:02d}"
    if granularity == "month":
        return f"{delivery_date.year:04d}-{delivery_date.month:02d}"
    raise ValueError(f"Unsupported notification delivery granularity: {granularity}")
