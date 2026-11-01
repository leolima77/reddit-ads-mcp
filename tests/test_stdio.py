"""End to end over the real MCP stdio transport, as an MCP client would launch it."""

import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .test_server import EXPECTED


@pytest.mark.anyio
async def test_server_speaks_mcp_over_stdio(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "reddit_ads_mcp"],
        env={"REDDIT_ADS_TOKEN_FILE": str(tmp_path / "none.json"), "PATH": ""},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        assert {t.name for t in listed.tools} == EXPECTED

        # Without credentials a tool call must fail cleanly with a helpful message.
        result = await session.call_tool("list_campaigns", {"ad_account_id": "a2_x"})
        assert result.is_error if hasattr(result, "is_error") else result.isError
        text = "".join(getattr(c, "text", "") for c in result.content)
        assert "REDDIT_ADS_CLIENT_ID" in text or "refresh token" in text
