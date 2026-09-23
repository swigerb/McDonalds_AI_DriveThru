"""A failing tool call must never leave the crew member silent.

Before this seam, an exception from a tool (e.g. an Azure AI Search 400 from a
bad field name -- the earlier "McDonald's goes silent" bug) or malformed tool
arguments escaped `_process_message_to_client`, tore down the whole relay, and
the model never received a `function_call_output`. An unknown tool name left the
call unanswered too. Every call now gets an output; errors become an apology.
"""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.append(str(Path(__file__).resolve().parents[1]))

from azure.core.credentials import AzureKeyCredential

from rtmt import RTMiddleTier, RTToolCall, Tool, ToolResult, ToolResultDirection


class ToolErrorTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.rtmt = RTMiddleTier("https://fake.openai.azure.com", "gpt-realtime-2.1", AzureKeyCredential("k"))
        self.server_ws = MagicMock()
        self.server_ws.send_json = AsyncMock()
        self.server_ws.send_str = AsyncMock()
        self.client_ws = MagicMock()
        self.client_ws.send_json = AsyncMock()
        self.tools_pending = {"call_1": RTToolCall("call_1", "prev_1")}

    def _add_tool(self, name, target):
        self.rtmt.tools[name] = Tool(target=target, schema={"type": "function", "name": name})

    async def _deliver(self, name, arguments='{"query": "big mac"}'):
        event = {"type": "response.output_item.done",
                 "item": {"type": "function_call", "call_id": "call_1", "name": name, "arguments": arguments}}
        with self.assertLogs("mcdonalds-drive-thru", level="INFO") as logs:
            result = await self.rtmt._process_message_to_client(
                SimpleNamespace(data=json.dumps(event)), self.client_ws, self.server_ws, self.tools_pending)
        self.assertIsNone(result, "function_call items are never forwarded to the browser")
        return logs.output

    def _outputs(self):
        return [c.args[0] for c in self.server_ws.send_json.call_args_list
                if c.args[0].get("item", {}).get("type") == "function_call_output"]

    async def test_tool_exception_is_answered_not_raised(self):
        self._add_tool("search", AsyncMock(side_effect=RuntimeError("Could not find a property named 'foo'")))
        logs = await self._deliver("search")

        outputs = self._outputs()
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["item"]["call_id"], "call_1")
        self.assertIn("search tool failed", outputs[0]["item"]["output"])
        self.client_ws.send_json.assert_not_awaited()
        self.assertTrue(any("Tool 'search' failed" in line for line in logs))

    async def test_malformed_arguments_are_answered(self):
        target = AsyncMock()
        self._add_tool("update_order", target)
        await self._deliver("update_order", arguments='{"action": "add", ')

        target.assert_not_awaited()
        self.assertEqual(len(self._outputs()), 1)
        self.assertIn("update_order tool failed", self._outputs()[0]["item"]["output"])

    async def test_unknown_tool_is_answered(self):
        logs = await self._deliver("order_pizza")

        self.assertEqual(len(self._outputs()), 1)
        self.assertIn("order_pizza tool failed", self._outputs()[0]["item"]["output"])
        self.assertTrue(any("Unknown tool requested: order_pizza" in line for line in logs))

    async def test_successful_tool_result_is_unchanged(self):
        self._add_tool("search", AsyncMock(return_value=ToolResult("Big Mac $5.99", ToolResultDirection.TO_SERVER)))
        await self._deliver("search")

        self.assertEqual([o["item"]["output"] for o in self._outputs()], ["Big Mac $5.99"])
        self.rtmt.tools["search"].target.assert_awaited_once_with({"query": "big mac"})

    async def test_response_done_after_a_failed_tool_asks_the_model_to_continue(self):
        self._add_tool("search", AsyncMock(side_effect=RuntimeError("boom")))
        await self._deliver("search")
        done = {"type": "response.done", "response": {"output": [{"type": "function_call", "name": "search"}]}}
        await self.rtmt._process_message_to_client(
            SimpleNamespace(data=json.dumps(done)), self.client_ws, self.server_ws, self.tools_pending)

        self.server_ws.send_str.assert_awaited_once()
        self.assertEqual(json.loads(self.server_ws.send_str.await_args.args[0])["type"], "response.create")


if __name__ == "__main__":
    unittest.main()
