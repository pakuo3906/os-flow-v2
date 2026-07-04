from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.mcp.server import MCPServer
from app.repositories.base import Repository

MCP_PROTOCOL_VERSION = "2025-06-18"


class MCPHttpTransport:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self._sessions: dict[str, dict[str, str]] = {}
        self._subscriptions: dict[str, set[str]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}

    def handle_post(
        self,
        payload: dict[str, Any],
        *,
        accept: str | None = None,
        protocol_version: str | None = None,
        session_id: str | None = None,
    ) -> JSONResponse | Response:
        method = payload.get("method")
        request_id = payload.get("id")
        server_subscriptions = self._subscriptions.setdefault(session_id, set()) if session_id else set()
        server_events = self._events.setdefault(session_id, []) if session_id else []
        server = MCPServer(self.repository, subscriptions=server_subscriptions, events=server_events)

        if not _accepts_streamable_http(accept):
            return JSONResponse(
                {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Accept must include application/json and text/event-stream."}},
                status_code=400,
            )

        if method != "initialize" and not str(method or "").startswith("notifications/"):
            if protocol_version not in {None, MCP_PROTOCOL_VERSION}:
                return JSONResponse(
                    {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Unsupported MCP protocol version."}},
                    status_code=400,
                )
            if not session_id or session_id not in self._sessions:
                return JSONResponse(
                    {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Missing or unknown MCP session."}},
                    status_code=400,
                )
            self._touch_session(session_id)

        response = server.handle(payload)
        if request_id is None:
            return Response(status_code=202)
        headers: dict[str, str] = {}
        if method == "initialize":
            session_id = secrets.token_urlsafe(16)
            self._sessions[session_id] = {
                "protocol_version": MCP_PROTOCOL_VERSION,
                "state": "active",
                "created_at": _now(),
                "last_seen_at": _now(),
            }
            self._subscriptions.setdefault(session_id, set())
            self._events.setdefault(session_id, [])
            headers["Mcp-Session-Id"] = session_id
            headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
        elif session_id is not None:
            headers["Mcp-Session-Id"] = session_id
        return JSONResponse(response, headers=headers)

    def handle_get(self, request: Request) -> StreamingResponse | Response:
        accept = request.headers.get("accept", "")
        if "text/event-stream" not in accept:
            return Response(status_code=406, media_type="text/plain", content="Client must accept text/event-stream.")

        session_id = request.headers.get("Mcp-Session-Id")
        headers: dict[str, str] = {}
        if session_id is not None:
            if session_id not in self._sessions:
                return JSONResponse(
                    {"detail": "Unknown MCP session."},
                    status_code=404,
                )
            self._touch_session(session_id)
            headers["Mcp-Session-Id"] = session_id

        async def event_stream():
            if session_id is not None:
                for event in self._drain_session_events(session_id):
                    yield f"event: mcp.notification\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield ": connected\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)

    def handle_delete(self, session_id: str | None) -> JSONResponse:
        if session_id and session_id in self._sessions:
            self.notify_resource_changed(
                "oflow://mcp/subscriptions",
                event_type="subscription_changed",
                payload={"method": "session_deleted", "session_id": session_id},
            )
            self._sessions.pop(session_id, None)
            self._subscriptions.pop(session_id, None)
            self._events.pop(session_id, None)
            return JSONResponse({}, status_code=202)
        return JSONResponse({"detail": "Unknown or missing session."}, status_code=404)

    def notify_resource_changed(
        self,
        resource_uri: str,
        *,
        payload: dict[str, Any] | None = None,
        event_type: str = "resource_changed",
    ) -> int:
        queued = 0
        for session_id in list(self._sessions):
            subscriptions = self._subscriptions.get(session_id, set())
            if resource_uri not in subscriptions and not ("oflow://summary" in subscriptions and resource_uri != "oflow://summary"):
                continue
            self._events.setdefault(session_id, []).append(
                {
                    "event_type": event_type,
                    "resource_uri": resource_uri,
                    "payload": payload or {},
                    "recorded_at": _now(),
                }
            )
            queued += 1
        return queued

    def list_subscriptions(self) -> dict[str, object]:
        sessions: list[dict[str, object]] = []
        for session_id, session in self._sessions.items():
            sessions.append(
                {
                    "session_id": session_id,
                    "protocol_version": session.get("protocol_version"),
                    "state": session.get("state"),
                    "created_at": session.get("created_at"),
                    "last_seen_at": session.get("last_seen_at"),
                    "subscriptions": sorted(self._subscriptions.get(session_id, set())),
                }
            )
        sessions.sort(key=lambda item: str(item["session_id"]))
        return {"sessions": sessions, "session_count": len(sessions)}

    def list_events(
        self,
        session_id: str | None = None,
        *,
        event_type: str | None = None,
        resource_uri: str | None = None,
    ) -> dict[str, object]:
        if session_id is not None:
            if session_id not in self._sessions:
                return {"sessions": [], "session_count": 0}
            events = self._events.get(session_id, [])
            filtered_events = _filter_events(events, event_type=event_type, resource_uri=resource_uri)
            return {
                "sessions": [
                    {
                        "session_id": session_id,
                        "events": filtered_events,
                        "event_count": len(filtered_events),
                        "event_type_counts": _count_event_types(filtered_events),
                    }
                ],
                "session_count": 1,
            }
        sessions: list[dict[str, object]] = []
        for current_session_id in self._sessions:
            events = _filter_events(
                self._events.get(current_session_id, []),
                event_type=event_type,
                resource_uri=resource_uri,
            )
            sessions.append(
                {
                    "session_id": current_session_id,
                    "events": events,
                    "event_count": len(events),
                    "event_type_counts": _count_event_types(events),
                }
            )
        sessions.sort(key=lambda item: str(item["session_id"]))
        return {"sessions": sessions, "session_count": len(sessions)}

    def get_overview(
        self,
        session_id: str | None = None,
        *,
        event_type: str | None = None,
        resource_uri: str | None = None,
    ) -> dict[str, object]:
        subscriptions = self.list_subscriptions()
        events = self.list_events(session_id=session_id, event_type=event_type, resource_uri=resource_uri)
        return {
            "subscriptions": subscriptions,
            "events": events,
            "session_id": session_id,
        }

    def get_dashboard(
        self,
        session_id: str | None = None,
        *,
        event_type: str | None = None,
        resource_uri: str | None = None,
    ) -> dict[str, object]:
        subscriptions = self.list_subscriptions()
        events = self.list_events(session_id=session_id, event_type=event_type, resource_uri=resource_uri)
        event_sessions = events["sessions"]
        total_subscriptions = sum(len(session.get("subscriptions", [])) for session in subscriptions["sessions"])
        total_events = sum(session.get("event_count", 0) for session in event_sessions)
        latest_event_at = _latest_event_at(event_sessions)
        return {
            "session_id": session_id,
            "summary": {
                "active_sessions": subscriptions["session_count"],
                "total_subscriptions": total_subscriptions,
                "total_events": total_events,
                "latest_event_at": latest_event_at,
                "event_type_counts": _aggregate_event_counts(event_sessions),
                "resource_event_counts": _aggregate_resource_counts(event_sessions),
                "top_resource_event_counts": _top_counts(_aggregate_resource_counts(event_sessions)),
            },
            "subscriptions": subscriptions,
            "events": events,
        }

    def _drain_session_events(self, session_id: str) -> list[dict[str, Any]]:
        events = self._events.get(session_id, [])
        self._events[session_id] = []
        return list(events)

    def _touch_session(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session["last_seen_at"] = _now()


def _accepts_streamable_http(accept: str | None) -> bool:
    if not accept:
        return False
    normalized = accept.lower()
    return "application/json" in normalized and "text/event-stream" in normalized


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _filter_events(
    events: list[dict[str, Any]],
    *,
    event_type: str | None = None,
    resource_uri: str | None = None,
) -> list[dict[str, Any]]:
    filtered = list(events)
    if event_type is not None:
        filtered = [event for event in filtered if event.get("event_type") == event_type]
    if resource_uri is not None:
        filtered = [event for event in filtered if event.get("resource_uri") == resource_uri]
    return filtered


def _count_event_types(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        key = str(event.get("event_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _aggregate_event_counts(event_sessions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for session in event_sessions:
        for event_type, count in (session.get("event_type_counts") or {}).items():
            counts[event_type] = counts.get(event_type, 0) + int(count)
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _aggregate_resource_counts(event_sessions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for session in event_sessions:
        for event in session.get("events", []):
            resource_uri = str(event.get("resource_uri") or "unknown")
            counts[resource_uri] = counts.get(resource_uri, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _top_counts(counts: dict[str, int], *, limit: int = 5) -> dict[str, int]:
    items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return dict(items[:limit])


def _latest_event_at(event_sessions: list[dict[str, Any]]) -> str | None:
    latest_event_at: str | None = None
    for session in event_sessions:
        for event in session.get("events", []):
            recorded_at = event.get("recorded_at")
            if recorded_at is None:
                continue
            if latest_event_at is None or str(recorded_at) > latest_event_at:
                latest_event_at = str(recorded_at)
    return latest_event_at
