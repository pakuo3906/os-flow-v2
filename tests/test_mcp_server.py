from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from app.mcp.server import MCPServer, run_stdio_server
from app.repositories.sqlite import SQLiteRepository


class McpServerTests(unittest.TestCase):
    def test_stdio_server_handles_initialize_and_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with SQLiteRepository(root / "app.db") as repo:
                server = MCPServer(repo)
                input_stream = io.StringIO(
                    "\n".join(
                        [
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "initialize",
                                    "params": {"protocolVersion": "2025-06-18"},
                                }
                            ),
                            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                        ]
                    )
                    + "\n"
                )
                output_stream = io.StringIO()

                run_stdio_server(server, stdin=input_stream, stdout=output_stream)

                lines = [json.loads(line) for line in output_stream.getvalue().splitlines() if line.strip()]
                self.assertEqual(2, len(lines))
                self.assertEqual("2.0", lines[0]["jsonrpc"])
                self.assertEqual(1, lines[0]["id"])
                self.assertEqual("2025-06-18", lines[0]["result"]["protocolVersion"])
                self.assertEqual(2, lines[1]["id"])
                self.assertGreaterEqual(len(lines[1]["result"]["tools"]), 1)

    def test_tools_call_returns_structured_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with SQLiteRepository(root / "app.db") as repo:
                case = repo.upsert_case(
                    case_code="CASE-MCP-SERVER-1",
                    title="Server visible case",
                    client_name="Client MCP",
                    status="in_progress",
                    due_date="2026-07-10",
                    invoice_status="pending",
                    output_status="pending",
                )
                server = MCPServer(repo)
                response = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "search_cases",
                            "arguments": {"query": "CASE-MCP-SERVER-1", "limit": 1},
                        },
                    }
                )

                self.assertIsNotNone(response)
                self.assertEqual(3, response["id"])
                self.assertFalse(response["result"]["isError"])
                self.assertEqual(1, response["result"]["structuredContent"]["total"])
                self.assertEqual(case.case_code, response["result"]["structuredContent"]["items"][0]["case_code"])
                self.assertGreaterEqual(repo.count_operation_logs(event_type="mcp_tool_called"), 1)

    def test_resources_list_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with SQLiteRepository(root / "app.db") as repo:
                case = repo.upsert_case(
                    case_code="CASE-MCP-RESOURCE-1",
                    title="Resource visible case",
                    client_name="Client MCP",
                    status="in_progress",
                    due_date="2026-07-10",
                    invoice_status="pending",
                    output_status="pending",
                )
                document = repo.register_document(
                    case_id=case.id,
                    source_type="api",
                    storage_key="originals/CASE-MCP-RESOURCE-1/doc.txt",
                    filename="doc.txt",
                    mime_type="text/plain",
                    content_hash="hash-mcp-resource-1",
                    size_bytes=12,
                )
                server = MCPServer(repo)

                listed = server.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
                self.assertIsNotNone(listed)
                self.assertGreaterEqual(len(listed["result"]["resources"]), 5)

                summary = server.handle({"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": "oflow://summary"}})
                self.assertEqual(5, summary["id"])
                self.assertIn("cases_total", summary["result"]["contents"][0]["text"])

                subscriptions = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 5_1,
                        "method": "resources/read",
                        "params": {"uri": "oflow://mcp/subscriptions"},
                    }
                )
                subscriptions_payload = json.loads(subscriptions["result"]["contents"][0]["text"])
                self.assertEqual([], subscriptions_payload["subscriptions"])

                events = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 5_2,
                        "method": "resources/read",
                        "params": {"uri": "oflow://mcp/events"},
                    }
                )
                events_payload = json.loads(events["result"]["contents"][0]["text"])
                self.assertEqual(0, events_payload["event_count"])

                overview = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 5_3,
                        "method": "resources/read",
                        "params": {"uri": "oflow://mcp/overview"},
                    }
                )
                overview_payload = json.loads(overview["result"]["contents"][0]["text"])
                self.assertEqual(0, overview_payload["subscription_count"])
                self.assertEqual(0, overview_payload["event_count"])

                dashboard = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 5_4,
                        "method": "resources/read",
                        "params": {"uri": "oflow://mcp/dashboard"},
                    }
                )
                dashboard_payload = json.loads(dashboard["result"]["contents"][0]["text"])
                self.assertEqual(0, dashboard_payload["summary"]["active_sessions"])
                self.assertEqual(0, dashboard_payload["summary"]["total_events"])
                self.assertEqual({}, dashboard_payload["summary"]["resource_event_counts"])
                self.assertEqual({}, dashboard_payload["summary"]["top_resource_event_counts"])

                detail = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "resources/read",
                        "params": {"uri": f"oflow://cases/{case.id}"},
                    }
                )
                detail_payload = json.loads(detail["result"]["contents"][0]["text"])
                self.assertEqual(case.case_code, detail_payload["case"]["case_code"])

                document_detail = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 7,
                        "method": "resources/read",
                        "params": {"uri": f"oflow://documents/{document.id}"},
                    }
                )
                document_payload = json.loads(document_detail["result"]["contents"][0]["text"])
                self.assertEqual("doc.txt", document_payload["filename"])
                self.assertGreaterEqual(repo.count_operation_logs(event_type="mcp_resource_read"), 3)

    def test_prompts_list_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with SQLiteRepository(root / "app.db") as repo:
                case = repo.upsert_case(
                    case_code="CASE-MCP-PROMPT-1",
                    title="Prompt visible case",
                    client_name="Client MCP",
                    status="in_progress",
                    due_date="2026-07-10",
                    invoice_status="pending",
                    output_status="pending",
                )
                document = repo.register_document(
                    case_id=case.id,
                    source_type="api",
                    storage_key="originals/CASE-MCP-PROMPT-1/doc.txt",
                    filename="doc.txt",
                    mime_type="text/plain",
                    content_hash="hash-mcp-prompt-1",
                    size_bytes=12,
                )
                server = MCPServer(repo)

                listed = server.handle({"jsonrpc": "2.0", "id": 8, "method": "prompts/list"})
                self.assertIsNotNone(listed)
                self.assertGreaterEqual(len(listed["result"]["prompts"]), 3)

                prompt = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "prompts/get",
                        "params": {"name": "case_review", "arguments": {"case_id": case.id, "focus": "billing"}},
                    }
                )
                self.assertEqual("Case review prompt", prompt["result"]["description"])
                self.assertIn("billing", prompt["result"]["messages"][0]["content"]["text"])
                self.assertIn(case.case_code, prompt["result"]["messages"][0]["content"]["text"])

                doc_prompt = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 10,
                        "method": "prompts/get",
                        "params": {"name": "document_review", "arguments": {"document_id": document.id}},
                    }
                )
                self.assertIn("doc.txt", doc_prompt["result"]["messages"][0]["content"]["text"])
                self.assertGreaterEqual(repo.count_operation_logs(event_type="mcp_prompts_listed"), 1)

    def test_resource_subscribe_and_unsubscribe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with SQLiteRepository(root / "app.db") as repo:
                server = MCPServer(repo)

                subscribed = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 11,
                        "method": "resources/subscribe",
                        "params": {"uri": "oflow://summary"},
                    }
                )
                self.assertTrue(subscribed["result"]["subscribed"])
                self.assertIn("oflow://summary", subscribed["result"]["subscriptions"])

                unsubscribed = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 12,
                        "method": "resources/unsubscribe",
                        "params": {"uri": "oflow://summary"},
                    }
                )
                self.assertFalse(unsubscribed["result"]["subscribed"])
                self.assertNotIn("oflow://summary", unsubscribed["result"]["subscriptions"])

                snapshot = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 13,
                        "method": "resources/read",
                        "params": {"uri": "oflow://mcp/subscriptions"},
                    }
                )
                snapshot_payload = json.loads(snapshot["result"]["contents"][0]["text"])
                self.assertEqual([], snapshot_payload["subscriptions"])
                self.assertEqual(0, snapshot_payload["subscription_count"])
                self.assertGreaterEqual(repo.count_operation_logs(event_type="mcp_resource_subscribed"), 1)
                self.assertGreaterEqual(repo.count_operation_logs(event_type="mcp_resource_unsubscribed"), 1)

                events = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 14,
                        "method": "resources/read",
                        "params": {"uri": "oflow://mcp/events"},
                    }
                )
                events_payload = json.loads(events["result"]["contents"][0]["text"])
                self.assertEqual(2, events_payload["event_count"])
                self.assertEqual(2, events_payload["event_type_counts"]["subscription_changed"])

                overview = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 15,
                        "method": "resources/read",
                        "params": {"uri": "oflow://mcp/overview"},
                    }
                )
                overview_payload = json.loads(overview["result"]["contents"][0]["text"])
                self.assertEqual(0, overview_payload["subscription_count"])
                self.assertEqual(2, overview_payload["event_count"])
                self.assertEqual(2, overview_payload["event_type_counts"]["subscription_changed"])

                dashboard = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 16,
                        "method": "resources/read",
                        "params": {"uri": "oflow://mcp/dashboard"},
                    }
                )
                dashboard_payload = json.loads(dashboard["result"]["contents"][0]["text"])
                self.assertEqual(0, dashboard_payload["summary"]["active_sessions"])
                self.assertEqual(2, dashboard_payload["summary"]["total_events"])
                self.assertEqual(2, dashboard_payload["summary"]["event_type_counts"]["subscription_changed"])
                self.assertEqual(2, dashboard_payload["summary"]["resource_event_counts"]["oflow://summary"])
                self.assertEqual(2, dashboard_payload["summary"]["top_resource_event_counts"]["oflow://summary"])


if __name__ == "__main__":
    unittest.main()
