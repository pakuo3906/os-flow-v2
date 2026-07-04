from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.repositories.sqlite import SQLiteRepository


class McpHttpTests(unittest.TestCase):
    def test_initialize_sets_session_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "data" / "app.db"
            with SQLiteRepository(database_path) as repo:
                repo.upsert_case(
                    case_code="CASE-MCP-HTTP-1",
                    title="HTTP case",
                    client_name="Client HTTP",
                    status="in_progress",
                    due_date="2026-07-10",
                    invoice_status="pending",
                    output_status="pending",
                )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(database_path)
            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp",
                        headers={"Accept": "application/json, text/event-stream"},
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18"},
                        },
                    )

                    self.assertEqual(200, response.status_code)
                    self.assertEqual("2025-06-18", response.json()["result"]["protocolVersion"])
                    self.assertIsNotNone(response.headers.get("Mcp-Session-Id"))
                    self.assertEqual("2025-06-18", response.headers.get("MCP-Protocol-Version"))
            finally:
                app.state.repository.close()

    def test_tools_call_returns_json_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "data" / "app.db"
            with SQLiteRepository(database_path) as repo:
                repo.upsert_case(
                    case_code="CASE-MCP-HTTP-2",
                    title="HTTP case two",
                    client_name="Client HTTP",
                    status="in_progress",
                    due_date="2026-07-11",
                    invoice_status="pending",
                    output_status="pending",
                )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(database_path)
            try:
                with TestClient(app) as client:
                    init = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18"},
                        },
                    )
                    session_id = init.headers.get("Mcp-Session-Id")
                    response = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                            "Mcp-Session-Id": session_id or "",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": "search_cases",
                                "arguments": {"query": "CASE-MCP-HTTP-2", "limit": 1},
                            },
                        },
                    )

                    self.assertEqual(200, response.status_code)
                    self.assertFalse(response.json()["result"]["isError"])
                    self.assertEqual(1, response.json()["result"]["structuredContent"]["total"])
                    self.assertEqual(session_id, response.headers.get("Mcp-Session-Id"))
                    self.assertGreaterEqual(app.state.repository.count_operation_logs(event_type="mcp_tool_called"), 1)
            finally:
                app.state.repository.close()

    def test_notification_posts_return_202(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(root / "app.db")
            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                        },
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    )

                    self.assertEqual(202, response.status_code)
                    self.assertEqual("", response.text)
            finally:
                app.state.repository.close()

    def test_post_requires_session_after_initialize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(root / "app.db")
            try:
                with TestClient(app) as client:
                    client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18"},
                        },
                    )
                    response = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                        },
                        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                    )

                    self.assertEqual(400, response.status_code)
                    self.assertTrue(response.json()["error"]["message"].startswith("Missing or unknown MCP session"))
            finally:
                app.state.repository.close()

    def test_post_requires_session_without_initialize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(root / "app.db")
            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                        },
                        json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
                    )

                    self.assertEqual(400, response.status_code)
                    self.assertTrue(response.json()["error"]["message"].startswith("Missing or unknown MCP session"))
            finally:
                app.state.repository.close()

    def test_resources_list_and_read_over_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "data" / "app.db"
            with SQLiteRepository(database_path) as repo:
                case = repo.upsert_case(
                    case_code="CASE-MCP-HTTP-RESOURCE-1",
                    title="HTTP resource case",
                    client_name="Client HTTP",
                    status="in_progress",
                    due_date="2026-07-12",
                    invoice_status="pending",
                    output_status="pending",
                )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(database_path)
            try:
                with TestClient(app) as client:
                    init = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18"},
                        },
                    )
                    session_id = init.headers.get("Mcp-Session-Id") or ""
                    listed = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                            "Mcp-Session-Id": session_id,
                        },
                        json={"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
                    )
                    self.assertEqual(200, listed.status_code)
                    self.assertGreaterEqual(len(listed.json()["result"]["resources"]), 2)

                    read = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                            "Mcp-Session-Id": session_id,
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "resources/read",
                            "params": {"uri": f"oflow://cases/{case.id}"},
                        },
                    )
                    self.assertEqual(case.case_code, json.loads(read.json()["result"]["contents"][0]["text"])["case"]["case_code"])
            finally:
                app.state.repository.close()

    def test_prompts_list_and_get_over_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "data" / "app.db"
            with SQLiteRepository(database_path) as repo:
                case = repo.upsert_case(
                    case_code="CASE-MCP-HTTP-PROMPT-1",
                    title="HTTP prompt case",
                    client_name="Client HTTP",
                    status="in_progress",
                    due_date="2026-07-12",
                    invoice_status="pending",
                    output_status="pending",
                )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(database_path)
            try:
                with TestClient(app) as client:
                    init = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18"},
                        },
                    )
                    session_id = init.headers.get("Mcp-Session-Id") or ""
                    listed = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                            "Mcp-Session-Id": session_id,
                        },
                        json={"jsonrpc": "2.0", "id": 2, "method": "prompts/list"},
                    )
                    self.assertEqual(200, listed.status_code)
                    self.assertGreaterEqual(len(listed.json()["result"]["prompts"]), 3)

                    prompt = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                            "Mcp-Session-Id": session_id,
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "prompts/get",
                            "params": {"name": "case_review", "arguments": {"case_id": case.id}},
                        },
                    )
                    self.assertIn(case.case_code, prompt.json()["result"]["messages"][0]["content"]["text"])
            finally:
                app.state.repository.close()

    def test_resource_subscribe_and_unsubscribe_over_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "data" / "app.db"
            with SQLiteRepository(database_path) as repo:
                case = repo.upsert_case(
                    case_code="CASE-MCP-HTTP-SUB-1",
                    title="HTTP subscription case",
                    client_name="Client HTTP",
                    status="in_progress",
                    due_date="2026-07-13",
                    invoice_status="pending",
                    output_status="pending",
                )

            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(database_path)
            try:
                with TestClient(app) as client:
                    init = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18"},
                        },
                    )
                    session_id = init.headers.get("Mcp-Session-Id") or ""
                    subscribed = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                            "Mcp-Session-Id": session_id,
                        },
                        json={"jsonrpc": "2.0", "id": 2, "method": "resources/subscribe", "params": {"uri": "oflow://summary"}},
                    )
                    self.assertEqual(200, subscribed.status_code)
                    self.assertTrue(subscribed.json()["result"]["subscribed"])
                    self.assertIn("oflow://summary", app.state.mcp_transport._subscriptions[session_id])

                    subscribed_case = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                            "Mcp-Session-Id": session_id,
                        },
                        json={"jsonrpc": "2.0", "id": 3, "method": "resources/subscribe", "params": {"uri": f"oflow://cases/{case.id}"}},
                    )
                    self.assertEqual(200, subscribed_case.status_code)
                    self.assertIn(f"oflow://cases/{case.id}", app.state.mcp_transport._subscriptions[session_id])

                    snapshot = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                            "Mcp-Session-Id": session_id,
                        },
                        json={"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "oflow://mcp/subscriptions"}},
                    )
                    self.assertEqual(200, snapshot.status_code)
                    snapshot_payload = json.loads(snapshot.json()["result"]["contents"][0]["text"])
                    self.assertEqual(sorted(["oflow://summary", f"oflow://cases/{case.id}"]), snapshot_payload["subscriptions"])
                    self.assertEqual(2, snapshot_payload["subscription_count"])

                    streamed = client.get(
                        "/mcp",
                        headers={
                            "Accept": "text/event-stream",
                            "Mcp-Session-Id": session_id,
                        },
                    )
                    self.assertEqual(200, streamed.status_code)
                    self.assertIn("resources/subscribe", streamed.text)
                    self.assertIn(f"oflow://cases/{case.id}", streamed.text)

                    updated = client.patch(
                        f"/cases/{case.id}",
                        json={"title": "HTTP subscription case updated"},
                    )
                    self.assertEqual(200, updated.status_code)

                    events = client.get("/mcp/events", params={"session_id": session_id})
                    self.assertEqual(200, events.status_code)
                    events_body = events.json()
                    self.assertEqual(1, events_body["session_count"])
                    self.assertEqual(session_id, events_body["sessions"][0]["session_id"])
                    self.assertGreaterEqual(events_body["sessions"][0]["event_count"], 1)

                    filtered_events = client.get(
                        "/mcp/events",
                        params={"session_id": session_id, "event_type": "case_updated"},
                    )
                    self.assertEqual(200, filtered_events.status_code)
                    filtered_body = filtered_events.json()
                    self.assertEqual(2, filtered_body["sessions"][0]["event_count"])
                    self.assertEqual("case_updated", filtered_body["sessions"][0]["events"][0]["event_type"])
                    self.assertEqual(2, filtered_body["sessions"][0]["event_type_counts"]["case_updated"])

                    resource_filtered = client.get(
                        "/mcp/events",
                        params={"session_id": session_id, "resource_uri": f"oflow://cases/{case.id}"},
                    )
                    self.assertEqual(200, resource_filtered.status_code)
                    resource_body = resource_filtered.json()
                    self.assertEqual(1, resource_body["sessions"][0]["event_count"])
                    self.assertEqual(f"oflow://cases/{case.id}", resource_body["sessions"][0]["events"][0]["resource_uri"])
                    self.assertEqual(1, resource_body["sessions"][0]["event_type_counts"]["case_updated"])

                    overview = client.get("/mcp/overview", params={"session_id": session_id})
                    self.assertEqual(200, overview.status_code)
                    overview_body = overview.json()
                    self.assertEqual(session_id, overview_body["session_id"])
                    self.assertGreaterEqual(overview_body["subscriptions"]["session_count"], 1)
                    self.assertEqual(1, overview_body["events"]["session_count"])

                    filtered_overview = client.get(
                        "/mcp/overview",
                        params={"session_id": session_id, "event_type": "case_updated"},
                    )
                    self.assertEqual(200, filtered_overview.status_code)
                    filtered_overview_body = filtered_overview.json()
                    self.assertEqual(2, filtered_overview_body["events"]["sessions"][0]["event_count"])
                    self.assertEqual(2, filtered_overview_body["events"]["sessions"][0]["event_type_counts"]["case_updated"])

                    resource_overview = client.get(
                        "/mcp/overview",
                        params={"session_id": session_id, "resource_uri": f"oflow://cases/{case.id}"},
                    )
                    self.assertEqual(200, resource_overview.status_code)
                    resource_overview_body = resource_overview.json()
                    self.assertEqual(1, resource_overview_body["events"]["sessions"][0]["event_count"])
                    self.assertEqual(1, resource_overview_body["events"]["sessions"][0]["event_type_counts"]["case_updated"])

                    dashboard = client.get("/mcp/dashboard", params={"session_id": session_id})
                    self.assertEqual(200, dashboard.status_code)
                    dashboard_body = dashboard.json()
                    self.assertEqual(session_id, dashboard_body["session_id"])
                    self.assertEqual(1, dashboard_body["summary"]["active_sessions"])
                    self.assertEqual(2, dashboard_body["summary"]["event_type_counts"]["case_updated"])
                    self.assertEqual(1, dashboard_body["summary"]["resource_event_counts"][f"oflow://cases/{case.id}"])
                    self.assertEqual(1, dashboard_body["summary"]["top_resource_event_counts"][f"oflow://cases/{case.id}"])
                    self.assertIsNotNone(dashboard_body["summary"]["latest_event_at"])

                    changed = client.get(
                        "/mcp",
                        headers={
                            "Accept": "text/event-stream",
                            "Mcp-Session-Id": session_id,
                        },
                    )
                    self.assertEqual(200, changed.status_code)
                    self.assertIn("case_updated", changed.text)
                    self.assertIn(f"oflow://cases/{case.id}", changed.text)

                    drained_events = client.get("/mcp/events", params={"session_id": session_id})
                    self.assertEqual(200, drained_events.status_code)
                    drained_body = drained_events.json()
                    self.assertEqual(0, drained_body["sessions"][0]["event_count"])

                    unsubscribed = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                            "Mcp-Session-Id": session_id,
                        },
                        json={"jsonrpc": "2.0", "id": 5, "method": "resources/unsubscribe", "params": {"uri": "oflow://summary"}},
                    )
                    self.assertEqual(200, unsubscribed.status_code)
                    self.assertFalse(unsubscribed.json()["result"]["subscribed"])
                    self.assertNotIn("oflow://summary", app.state.mcp_transport._subscriptions[session_id])

                    snapshot = client.get("/mcp/subscriptions")
                    self.assertEqual(200, snapshot.status_code)
                    body = snapshot.json()
                    self.assertEqual(1, body["session_count"])
                    self.assertEqual(session_id, body["sessions"][0]["session_id"])
                    self.assertEqual([f"oflow://cases/{case.id}"], body["sessions"][0]["subscriptions"])

                    delete_response = client.delete(
                        "/mcp",
                        headers={"Mcp-Session-Id": session_id},
                    )
                    self.assertEqual(202, delete_response.status_code)

                    cleared = client.get("/mcp/subscriptions")
                    self.assertEqual(200, cleared.status_code)
                    self.assertEqual(0, cleared.json()["session_count"])
            finally:
                app.state.repository.close()

    def test_get_returns_event_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(root / "app.db")
            try:
                with TestClient(app) as client:
                    response = client.get("/mcp", headers={"Accept": "text/event-stream"})

                    self.assertEqual(200, response.status_code)
                    self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            finally:
                app.state.repository.close()

    def test_get_accepts_known_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(root / "app.db")
            try:
                with TestClient(app) as client:
                    init = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18"},
                        },
                    )
                    session_id = init.headers.get("Mcp-Session-Id") or ""
                    response = client.get(
                        "/mcp",
                        headers={
                            "Accept": "text/event-stream",
                            "Mcp-Session-Id": session_id,
                        },
                    )

                    self.assertEqual(200, response.status_code)
                    self.assertEqual(session_id, response.headers.get("Mcp-Session-Id"))
            finally:
                app.state.repository.close()

    def test_get_rejects_unknown_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(root / "app.db")
            try:
                with TestClient(app) as client:
                    response = client.get(
                        "/mcp",
                        headers={
                            "Accept": "text/event-stream",
                            "Mcp-Session-Id": "missing-session",
                        },
                    )

                    self.assertEqual(404, response.status_code)
            finally:
                app.state.repository.close()

    def test_delete_closes_known_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(root / "app.db")
            try:
                with TestClient(app) as client:
                    init = client.post(
                        "/mcp",
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "MCP-Protocol-Version": "2025-06-18",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18"},
                        },
                    )
                    session_id = init.headers.get("Mcp-Session-Id") or ""
                    delete_response = client.delete(
                        "/mcp",
                        headers={"Mcp-Session-Id": session_id},
                    )
                    self.assertEqual(202, delete_response.status_code)

                    follow_up = client.get(
                        "/mcp",
                        headers={
                            "Accept": "text/event-stream",
                            "Mcp-Session-Id": session_id,
                        },
                    )
                    self.assertEqual(404, follow_up.status_code)
            finally:
                app.state.repository.close()

    def test_delete_rejects_unknown_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(root / "app.db")
            try:
                with TestClient(app) as client:
                    response = client.delete("/mcp", headers={"Mcp-Session-Id": "missing-session"})

                    self.assertEqual(404, response.status_code)
            finally:
                app.state.repository.close()

    def test_get_requires_event_stream_accept_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(root / "app.db")
            try:
                with TestClient(app) as client:
                    response = client.get("/mcp")

                    self.assertEqual(406, response.status_code)
            finally:
                app.state.repository.close()

    def test_post_requires_mcp_accept_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app()
            app.state.repository.close()
            app.state.repository = SQLiteRepository(root / "app.db")
            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp",
                        headers={"MCP-Protocol-Version": "2025-06-18"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    )

                    self.assertEqual(400, response.status_code)
                    self.assertIn(
                        "Accept must include application/json and text/event-stream",
                        response.json()["error"]["message"],
                    )
            finally:
                app.state.repository.close()


if __name__ == "__main__":
    unittest.main()
