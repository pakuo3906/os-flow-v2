from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, TextIO

from app.services.document_snapshots import (
    attach_document_extraction_snapshots,
    build_document_extraction_snapshot,
)
from app.repositories.base import Repository
from app.mcp.tools import (
    get_case_detail_tool,
    list_documents_tool,
    list_due_tasks_tool,
    list_invoices_tool,
    search_cases_tool,
    search_rag_tool,
)

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SERVER_NAME = "o-s-flow-v2"
MCP_SERVER_TITLE = "O's flow V2 MCP Server"
MCP_SERVER_VERSION = "0.1.0"


def _tool_specs() -> list[dict[str, object]]:
    return [
        {
            "name": "search_cases",
            "title": "Search Cases",
            "description": "Search cases by code, title, client name, status, or due date.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "status": {"type": ["string", "null"]},
                    "due_before": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "offset": {"type": "integer", "minimum": 0},
                },
            },
        },
        {
            "name": "get_case_detail",
            "title": "Get Case Detail",
            "description": "Fetch a case with its documents, artifacts, and active RAG entries.",
            "inputSchema": {
                "type": "object",
                "properties": {"case_id": {"type": "integer", "minimum": 1}},
                "required": ["case_id"],
            },
        },
        {
            "name": "list_documents",
            "title": "List Documents",
            "description": "List documents for cases and operational review.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": ["integer", "null"], "minimum": 1},
                    "source_type": {"type": ["string", "null"]},
                    "is_deleted": {"type": ["boolean", "null"]},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "offset": {"type": "integer", "minimum": 0},
                },
            },
        },
        {
            "name": "list_due_tasks",
            "title": "List Due Tasks",
            "description": "List cases due by a target date.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "until_date": {"type": "string"},
                    "status": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "offset": {"type": "integer", "minimum": 0},
                },
                "required": ["until_date"],
            },
        },
        {
            "name": "list_invoices",
            "title": "List Invoices",
            "description": "List cases by invoice state and due date.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "invoice_status": {"type": ["string", "null"]},
                    "due_before": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "offset": {"type": "integer", "minimum": 0},
                },
            },
        },
        {
            "name": "search_rag",
            "title": "Search RAG",
            "description": "Search reusable extracted content and RAG output.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "case_id": {"type": ["integer", "null"], "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "offset": {"type": "integer", "minimum": 0},
                },
                "required": ["query"],
            },
        },
    ]


def _resource_template_specs() -> list[dict[str, object]]:
    return [
        {
            "uriTemplate": "oflow://cases/{caseId}",
            "name": "case_detail",
            "title": "Case Detail",
            "description": "Read a case, its documents, and active RAG entries by case ID.",
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "oflow://documents/{documentId}",
            "name": "document_detail",
            "title": "Document Detail",
            "description": "Read a document by document ID.",
            "mimeType": "application/json",
        },
    ]


def _prompt_specs() -> list[dict[str, object]]:
    return [
        {
            "name": "case_review",
            "title": "Case Review",
            "description": "Ask for an operational review of a case using its ledger context.",
            "arguments": [
                {
                    "name": "case_id",
                    "description": "The case ID to review.",
                    "required": True,
                },
                {
                    "name": "focus",
                    "description": "Optional review focus such as deadline, billing, or output status.",
                    "required": False,
                },
            ],
        },
        {
            "name": "document_review",
            "title": "Document Review",
            "description": "Ask for a review of a specific document and its extracted context.",
            "arguments": [
                {
                    "name": "document_id",
                    "description": "The document ID to review.",
                    "required": True,
                },
                {
                    "name": "focus",
                    "description": "Optional review focus such as extraction quality or routing.",
                    "required": False,
                },
            ],
        },
        {
            "name": "ingestion_followup",
            "title": "Ingestion Follow-up",
            "description": "Ask for next steps after a file or chat ingestion.",
            "arguments": [
                {
                    "name": "case_code",
                    "description": "The case code to follow up on.",
                    "required": True,
                },
                {
                    "name": "message",
                    "description": "The user message or instruction to interpret.",
                    "required": True,
                },
            ],
        },
    ]


def _resource_specs(repository: Repository) -> list[dict[str, object]]:
    resources: list[dict[str, object]] = [
        {
            "uri": "oflow://summary",
            "name": "summary",
            "title": "System Summary",
            "description": "High-level counts for the O's flow ledger.",
            "mimeType": "application/json",
        },
        {
            "uri": "oflow://mcp/subscriptions",
            "name": "subscriptions",
            "title": "MCP Subscriptions",
            "description": "Current MCP subscription snapshot.",
            "mimeType": "application/json",
        },
        {
            "uri": "oflow://mcp/events",
            "name": "events",
            "title": "MCP Events",
            "description": "Queued MCP event snapshot.",
            "mimeType": "application/json",
        },
        {
            "uri": "oflow://mcp/overview",
            "name": "overview",
            "title": "MCP Overview",
            "description": "Combined MCP subscription and event snapshot.",
            "mimeType": "application/json",
        },
        {
            "uri": "oflow://mcp/dashboard",
            "name": "dashboard",
            "title": "MCP Dashboard",
            "description": "Compact MCP operational summary.",
            "mimeType": "application/json",
        }
    ]
    for case in repository.search_cases(limit=20):
        resources.append(
            {
                "uri": f"oflow://cases/{case.id}",
                "name": case.case_code,
                "title": case.title,
                "description": f"Case detail for {case.case_code}",
                "mimeType": "application/json",
            }
        )
    for document in repository.list_documents(limit=20):
        resources.append(
            {
                "uri": f"oflow://documents/{document.id}",
                "name": document.filename,
                "title": document.filename,
                "description": f"Document detail for {document.filename}",
                "mimeType": document.mime_type or "application/json",
            }
        )
    return resources


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return _json_safe(asdict(value))
    return value


def _tool_result(payload: Any) -> dict[str, object]:
    return {
        "content": [{"type": "text", "text": json.dumps(_json_safe(payload), ensure_ascii=False)}],
        "structuredContent": _json_safe(payload),
        "isError": False,
    }


def _error_result(message: str) -> dict[str, object]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


class MCPServer:
    def __init__(
        self,
        repository: Repository,
        subscriptions: set[str] | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> None:
        self.repository = repository
        self._initialized = False
        self._subscriptions: set[str] = subscriptions if subscriptions is not None else set()
        self._events: list[dict[str, Any]] = events if events is not None else []

    def initialize(self, params: dict[str, Any] | None) -> dict[str, object]:
        requested_version = (params or {}).get("protocolVersion", MCP_PROTOCOL_VERSION)
        if requested_version != MCP_PROTOCOL_VERSION:
            requested_version = MCP_PROTOCOL_VERSION
        self._initialized = True
        self._record_event(
            event_type="mcp_initialized",
            message="MCP session initialized.",
            metadata_json={"protocol_version": requested_version},
        )
        return {
            "protocolVersion": requested_version,
            "capabilities": {
                "logging": {},
                "resources": {
                    "listChanged": True,
                },
                "prompts": {
                    "listChanged": True,
                },
                "tools": {"listChanged": True},
            },
            "serverInfo": {
                "name": MCP_SERVER_NAME,
                "title": MCP_SERVER_TITLE,
                "version": MCP_SERVER_VERSION,
            },
        }

    def handle(self, message: dict[str, Any]) -> dict[str, object] | None:
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            return self._response(request_id, self.initialize(message.get("params")))
        if method == "notifications/initialized":
            self._initialized = True
            return None
        if method == "ping":
            self._record_event(event_type="mcp_pinged", message="MCP ping received.")
            return self._response(request_id, {})
        if method == "resources/list":
            self._record_event(event_type="mcp_resources_listed", message="MCP resources listed.")
            return self._response(request_id, {"resources": _resource_specs(self.repository), "nextCursor": None})
        if method == "resources/subscribe":
            params = message.get("params") or {}
            uri = params.get("uri")
            if not uri:
                return self._response(request_id, _error_result("Missing resource URI."))
            self._subscriptions.add(uri)
            self._queue_event(
                event_type="subscription_changed",
                resource_uri=uri,
                payload={"method": "resources/subscribe", "uri": uri, "subscriptions": sorted(self._subscriptions)},
            )
            self._record_event(
                event_type="mcp_resource_subscribed",
                message="MCP resource subscribed.",
                metadata_json={"uri": uri},
            )
            return self._response(request_id, {"uri": uri, "subscribed": True, "subscriptions": sorted(self._subscriptions)})
        if method == "resources/unsubscribe":
            params = message.get("params") or {}
            uri = params.get("uri")
            if not uri:
                return self._response(request_id, _error_result("Missing resource URI."))
            self._subscriptions.discard(uri)
            self._queue_event(
                event_type="subscription_changed",
                resource_uri=uri,
                payload={"method": "resources/unsubscribe", "uri": uri, "subscriptions": sorted(self._subscriptions)},
            )
            self._record_event(
                event_type="mcp_resource_unsubscribed",
                message="MCP resource unsubscribed.",
                metadata_json={"uri": uri},
            )
            return self._response(request_id, {"uri": uri, "subscribed": False, "subscriptions": sorted(self._subscriptions)})
        if method == "resources/read":
            params = message.get("params") or {}
            uri = params.get("uri")
            self._record_event(
                event_type="mcp_resource_read",
                message="MCP resource read.",
                metadata_json={"uri": uri},
            )
            return self._response(request_id, self._read_resource(uri))
        if method == "resources/templates/list":
            self._record_event(event_type="mcp_resource_templates_listed", message="MCP resource templates listed.")
            return self._response(request_id, {"resourceTemplates": _resource_template_specs(), "nextCursor": None})
        if method == "prompts/list":
            self._record_event(event_type="mcp_prompts_listed", message="MCP prompts listed.")
            return self._response(request_id, {"prompts": _prompt_specs(), "nextCursor": None})
        if method == "prompts/get":
            params = message.get("params") or {}
            prompt_name = params.get("name")
            self._record_event(
                event_type="mcp_prompt_get",
                message="MCP prompt retrieved.",
                metadata_json={"name": prompt_name},
            )
            return self._response(request_id, self._get_prompt(params))
        if method == "tools/list":
            self._record_event(event_type="mcp_tools_listed", message="MCP tools listed.")
            return self._response(request_id, {"tools": _tool_specs(), "nextCursor": None})
        if method == "tools/call":
            params = message.get("params") or {}
            tool_name = params.get("name")
            self._record_event(
                event_type="mcp_tool_called",
                message="MCP tool called.",
                metadata_json={"name": tool_name},
            )
            return self._response(request_id, self._call_tool(params))
        self._record_event(
            event_type="mcp_unknown_method",
            message="Unknown MCP method received.",
            metadata_json={"method": method},
        )
        return self._error(request_id, -32601, f"Unknown method: {method}")

    def _get_prompt(self, params: dict[str, Any]) -> dict[str, object]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "case_review":
            case_id = arguments.get("case_id")
            if case_id is None:
                return _error_result("Missing required argument: case_id")
            case = self.repository.get_case(case_id)
            if case is None:
                return _error_result("Case not found.")
            focus = arguments.get("focus") or "overall operational status"
            return {
                "description": "Case review prompt",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Review case {case.case_code} ({case.title}) with focus on {focus}.\n"
                                f"Case status: {case.status}\n"
                                f"Due date: {case.due_date or 'n/a'}\n"
                                f"Invoice status: {case.invoice_status}\n"
                                f"Output status: {case.output_status}"
                            ),
                        },
                    }
                ],
            }
        if name == "document_review":
            document_id = arguments.get("document_id")
            if document_id is None:
                return _error_result("Missing required argument: document_id")
            document = self.repository.get_document(document_id)
            focus = arguments.get("focus") or "extraction quality and routing"
            return {
                "description": "Document review prompt",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Review document {document.filename} (ID {document.id}) with focus on {focus}.\n"
                                f"Case ID: {document.case_id}\n"
                                f"Source type: {document.source_type}\n"
                                f"Storage key: {document.storage_key}\n"
                                f"Deleted: {document.is_deleted}"
                            ),
                        },
                    }
                ],
            }
        if name == "ingestion_followup":
            case_code = arguments.get("case_code")
            message = arguments.get("message")
            if not case_code:
                return _error_result("Missing required argument: case_code")
            if not message:
                return _error_result("Missing required argument: message")
            return {
                "description": "Ingestion follow-up prompt",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Interpret the following instruction for case {case_code} and identify the next operational step.\n"
                                f"Instruction: {message}"
                            ),
                        },
                    }
                ],
            }
        return _error_result(f"Unknown prompt: {name}")

    def _read_resource(self, uri: str | None) -> dict[str, object]:
        if not uri:
            return _error_result("Missing resource URI.")
        if uri == "oflow://summary":
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(
                            {
                                "cases_total": self.repository.count_cases(),
                                "documents_total": self.repository.count_documents(),
                                "documents_active": self.repository.count_documents(is_deleted=False),
                                "processing_jobs_total": self.repository.count_processing_jobs(),
                                "operation_logs_total": self.repository.count_operation_logs(),
                                "notification_deliveries_total": self.repository.count_notification_deliveries(),
                                "rag_entries_total": self.repository.count_rag(query=""),
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            }
        if uri == "oflow://mcp/subscriptions":
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(
                            {
                                "subscriptions": sorted(self._subscriptions),
                                "subscription_count": len(self._subscriptions),
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            }
        if uri == "oflow://mcp/events":
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(
                            {
                                "events": _json_safe(self._events),
                                "event_count": len(self._events),
                                "event_type_counts": _event_type_counts(self._events),
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            }
        if uri == "oflow://mcp/overview":
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(
                            {
                                "subscriptions": sorted(self._subscriptions),
                                "subscription_count": len(self._subscriptions),
                                "events": _json_safe(self._events),
                                "event_count": len(self._events),
                                "event_type_counts": _event_type_counts(self._events),
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            }
        if uri == "oflow://mcp/dashboard":
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(
                            {
                                "summary": {
                                    "active_sessions": 1 if self._initialized else 0,
                                    "total_subscriptions": len(self._subscriptions),
                                    "total_events": len(self._events),
                                    "latest_event_at": _latest_event_at(self._events),
                                    "event_type_counts": _event_type_counts(self._events),
                                    "resource_event_counts": _resource_event_counts(self._events),
                                    "top_resource_event_counts": _top_counts(_resource_event_counts(self._events)),
                                },
                                "subscriptions": sorted(self._subscriptions),
                                "events": _json_safe(self._events),
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            }
        if uri.startswith("oflow://cases/"):
            case_id = _parse_int_uri_segment(uri, "oflow://cases/")
            if case_id is None:
                return _error_result("Invalid case resource URI.")
            detail = self.repository.get_case_detail(case_id)
            if detail is None:
                return {"content": [{"type": "text", "text": "Case not found."}], "isError": True}
            serialized_detail = _json_safe(detail)
            serialized_detail["documents"] = attach_document_extraction_snapshots(
                getattr(detail, "documents", []),
                self.repository.get_case_detail,
                serialize_document=_json_safe,
            )
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(serialized_detail, ensure_ascii=False),
                    }
                ]
            }
        if uri.startswith("oflow://documents/"):
            document_id = _parse_int_uri_segment(uri, "oflow://documents/")
            if document_id is None:
                return _error_result("Invalid document resource URI.")
            document = self.repository.get_document(document_id)
            document_payload = _json_safe(document)
            extraction = build_document_extraction_snapshot(
                self.repository.get_case_detail(document.case_id) if document is not None else None,
                document_id,
                case_id=document.case_id if document is not None else None,
            )
            if extraction is not None:
                document_payload = dict(document_payload)
                document_payload["extraction"] = extraction
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(document_payload, ensure_ascii=False),
                    }
                ]
            }
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "Resource not found."}], "isError": True}

    def _call_tool(self, params: dict[str, Any]) -> dict[str, object]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            if name == "search_cases":
                return _tool_result(search_cases_tool(self.repository, **arguments))
            if name == "get_case_detail":
                result = get_case_detail_tool(self.repository, **arguments)
                if result is None:
                    return _error_result("Case not found.")
                return _tool_result(result)
            if name == "list_documents":
                return _tool_result(list_documents_tool(self.repository, **arguments))
            if name == "list_due_tasks":
                return _tool_result(list_due_tasks_tool(self.repository, **arguments))
            if name == "list_invoices":
                return _tool_result(list_invoices_tool(self.repository, **arguments))
            if name == "search_rag":
                return _tool_result(search_rag_tool(self.repository, **arguments))
        except TypeError as exc:
            return _error_result(f"Invalid arguments for {name}: {exc}")
        except Exception as exc:
            return _error_result(str(exc))
        return _error_result(f"Unknown tool: {name}")

    def _response(self, request_id: Any, result: dict[str, object]) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error(self, request_id: Any, code: int, message: str) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def _record_event(
        self,
        *,
        event_type: str,
        message: str,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.repository.record_operation_log(
                event_type=event_type,
                entity_type="mcp",
                entity_id=None,
                case_id=None,
                document_id=None,
                message=message,
                metadata_json=metadata_json or {},
            )
        except Exception:
            pass

    def _queue_event(self, *, event_type: str, resource_uri: str, payload: dict[str, Any]) -> None:
        self._events.append(
            {
                "event_type": event_type,
                "resource_uri": resource_uri,
                "payload": payload,
                "recorded_at": self._now(),
            }
        )

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_int_uri_segment(uri: str, prefix: str) -> int | None:
    suffix = uri[len(prefix) :]
    try:
        return int(suffix)
    except ValueError:
        return None


def _event_type_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        key = str(event.get("event_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _resource_event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        key = str(event.get("resource_uri") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _top_counts(counts: dict[str, int], *, limit: int = 5) -> dict[str, int]:
    items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return dict(items[:limit])


def _latest_event_at(events: list[dict[str, Any]]) -> str | None:
    latest_event_at: str | None = None
    for event in events:
        recorded_at = event.get("recorded_at")
        if recorded_at is None:
            continue
        if latest_event_at is None or str(recorded_at) > latest_event_at:
            latest_event_at = str(recorded_at)
    return latest_event_at


def run_stdio_server(server: MCPServer, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        message = json.loads(line)
        response = server.handle(message)
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False))
        stdout.write("\n")
        stdout.flush()
