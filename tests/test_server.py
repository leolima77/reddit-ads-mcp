import pytest

from reddit_ads_mcp.server import Runtime, create_server

EXPECTED = {
    "list_ad_accounts",
    "list_campaigns",
    "list_ad_groups",
    "list_ads",
    "get_report",
    "performance_overview",
    "list_report_fields",
}


@pytest.mark.anyio
async def test_tools_are_registered_and_all_read_only():
    server = create_server(Runtime())  # no credentials needed just to list tools
    tools = await server.list_tools()
    assert {t.name for t in tools} == EXPECTED
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.description
