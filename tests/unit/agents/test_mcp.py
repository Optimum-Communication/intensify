from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from intensify.mcp import create_server
from intensify.spec import load_and_validate
from tests.unit.agents.conftest import implement_member_discount

EXPECTED_TOOLS = {
    "search_nodes",
    "get_node",
    "get_context",
    "list_planned",
    "update_intent",
    "add_planned_node",
    "analyze_code",
    "verify_node",
    "drift_report",
}


def call(repo: Path, tool: str, **arguments: Any) -> Any:
    server = create_server(repo / "intensify.yaml")
    result = asyncio.run(server.call_tool(tool, arguments))
    if result.structured_content is not None:  # type: ignore[union-attr]
        content = result.structured_content  # type: ignore[union-attr]
        return content.get("result", content)
    return result.content[0].text  # type: ignore[union-attr]


def test_tools_are_registered_with_annotations(repo: Path) -> None:
    tools = asyncio.run(create_server(repo / "intensify.yaml").list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    by_name = {t.name: t for t in tools}
    assert by_name["get_context"].annotations is not None
    assert by_name["get_context"].annotations.read_only_hint is True
    assert by_name["update_intent"].annotations is not None
    assert by_name["update_intent"].annotations.read_only_hint is False


def test_read_tools(repo: Path) -> None:
    assert call(repo, "search_nodes", query="discount")[0]["id"] == "shop.pricing.apply_discount"
    assert {n["id"] for n in call(repo, "list_planned")} == {
        "shop.pricing.member_discount",
        "shop.loyalty",
    }
    node = call(repo, "get_node", node_id="shop.pricing")
    assert node["node"]["intent"]["summary"] == "Price calculations."
    assert "shop.pricing.apply_discount" in node["children"]
    assert call(repo, "get_context", node_id="shop.pricing.apply_discount").startswith(
        "# `shop.pricing"
    )
    assert json.loads(call(repo, "drift_report"))["ok"] is True


def test_errors_are_tool_errors(repo: Path) -> None:
    server = create_server(repo / "intensify.yaml")
    with pytest.raises(ToolError, match="node 'nope' does not exist"):
        asyncio.run(server.call_tool("get_node", {"node_id": "nope"}))


def test_write_tools(repo: Path) -> None:
    assert "Updated" in call(
        repo, "update_intent", node_id="shop.pricing.subtotal", summary="Sum of items."
    )
    assert "Added" in call(
        repo,
        "add_planned_node",
        node_id="shop.loyalty.award",
        kind="function",
        summary="Award points.",
        parent_id="shop.loyalty",
        contract={"inputs": [{"name": "total", "type": "int"}]},
    )
    nodes = {e.node.id: e.node for e in load_and_validate(repo).nodes}
    assert nodes["shop.pricing.subtotal"].intent.author == "ai"
    assert nodes["shop.loyalty.award"].lifecycle_state == "planned"

    implement_member_discount(repo)
    analyzed = call(repo, "analyze_code")
    assert analyzed["promoted"] == ["shop.pricing.member_discount"]
    result = call(repo, "verify_node", node_id="shop.pricing.member_discount")
    assert result["verified"] is True
    assert result["marked_active"] is True


def test_stdio_round_trip(repo: Path) -> None:
    async def session_calls() -> tuple[str | None, set[str], bool, str]:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "intensify", "mcp", "--spec", str(repo)]
        )
        async with (
            stdio_client(params) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            init = await session.initialize()
            tools = await session.list_tools()
            failed = await session.call_tool("get_node", {"node_id": "nope"})
            resource = await session.read_resource("intensify://spec")
            text = resource.contents[0].text  # type: ignore[union-attr]
            return init.server_info.name, {t.name for t in tools.tools}, bool(failed.is_error), text

    name, tools, failed, spec_text = asyncio.run(session_calls())
    assert name == "intensify"
    assert tools == EXPECTED_TOOLS
    assert failed
    assert "project:\n  id: py-shop" in spec_text
