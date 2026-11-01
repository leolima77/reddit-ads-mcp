"""MCP server wiring. Every tool is read-only; the OAuth scope requested is `adsread` only."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import httpx
from mcp.types import ToolAnnotations
from pydantic import Field

from reddit_ads_mcp import __version__, service
from reddit_ads_mcp.auth import TokenProvider
from reddit_ads_mcp.client import RedditAdsClient
from reddit_ads_mcp.config import Settings
from reddit_ads_mcp.errors import RedditAdsError

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError

INSTRUCTIONS = """\
Read-only access to a Reddit Ads account. Start with performance_overview for a first look, then \
drill down with get_report (breakdowns such as CAMPAIGN_ID, AD_GROUP_ID, AD_ID, COMMUNITY, \
PLACEMENT, COUNTRY, INTEREST, DATE). Money values are already in the account currency. \
Call list_report_fields if unsure which metrics or breakdowns exist."""

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)

AccountId = Annotated[
    str | None,
    Field(description="Ad account id (e.g. a2_xxx). Optional if REDDIT_ADS_ACCOUNT_ID is set."),
]
Days = Annotated[
    int | None,
    Field(description="Look back this many days ending today (default 30). Ignored with dates."),
]
StartDate = Annotated[str | None, Field(description="Inclusive start, YYYY-MM-DD.")]
EndDate = Annotated[str | None, Field(description="Inclusive end, YYYY-MM-DD.")]

REPORT_FIELDS = {
    "delivery": ["IMPRESSIONS", "CLICKS", "SPEND", "CTR", "CPC", "ECPM", "REACH", "FREQUENCY"],
    "conversions (pixel/CAPI)": [
        "KEY_CONVERSION_TOTAL_COUNT", "KEY_CONVERSION_ECPA", "KEY_CONVERSION_RATE",
        "CONVERSION_SIGN_UP_CLICKS", "CONVERSION_SIGN_UP_VIEWS", "CONVERSION_SIGN_UP_ECPA",
        "CONVERSION_LEAD_CLICKS", "CONVERSION_PAGE_VISIT_CLICKS", "CONVERSION_PURCHASE_CLICKS",
        "CONVERSION_PURCHASE_TOTAL_VALUE", "CONVERSION_ROAS",
    ],
    "video": ["VIDEO_STARTED", "VIDEO_VIEWABLE_IMPRESSIONS", "VIDEO_FULLY_VIEWABLE_IMPRESSIONS"],
}  # fmt: skip
REPORT_BREAKDOWNS = [
    "DATE", "HOUR", "CAMPAIGN_ID", "AD_GROUP_ID", "AD_ID", "PLACEMENT", "COMMUNITY", "COUNTRY",
    "REGION", "DMA", "METRO", "INTEREST", "KEYWORD", "AGE", "GENDER", "LANGUAGE", "OS_TYPE",
    "CUSTOM_AUDIENCE", "IN_MARKET_AUDIENCE", "CAROUSEL_CARD", "GALLERY_ITEM_ID", "ASSET_ID",
]  # fmt: skip


class Runtime:
    """Creates the HTTP client lazily so importing or listing tools never needs credentials."""

    def __init__(
        self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._client: RedditAdsClient | None = None
        self._lock = asyncio.Lock()

    async def client(self) -> RedditAdsClient:
        async with self._lock:
            if self._client is None:
                settings = self._settings or Settings.from_env()
                http = httpx.AsyncClient(timeout=30, transport=self._transport)
                self._client = RedditAdsClient(settings, TokenProvider(settings, http), http)
            return self._client


def create_server(runtime: Runtime | None = None) -> Any:
    runtime = runtime or Runtime()
    server = _Server("reddit-ads", instructions=INSTRUCTIONS)

    def tool(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        """Register a read-only tool; expected failures reach the model as a readable error."""

        @functools.wraps(fn)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except RedditAdsError as exc:
                raise ToolError(str(exc)) from exc

        return server.tool(annotations=READ_ONLY)(guarded)

    @tool
    async def list_ad_accounts(
        business_id: Annotated[
            str | None,
            Field(description="Reddit business id. Optional if REDDIT_ADS_BUSINESS_ID is set."),
        ] = None,
    ) -> dict[str, Any]:
        """List the ad accounts of a business (id, name, currency, time zone)."""
        return await service.list_ad_accounts(await runtime.client(), business_id)

    @tool
    async def list_campaigns(
        ad_account_id: AccountId = None,
        status: Annotated[
            str | None, Field(description="Filter by status, e.g. ACTIVE, PAUSED, ARCHIVED.")
        ] = None,
        limit: int = 200,
        raw: Annotated[bool, Field(description="Include nested fields (large).")] = False,
    ) -> dict[str, Any]:
        """List campaigns with objective, status and budget."""
        return await service.list_entities(
            await runtime.client(), "campaigns", ad_account_id, status=status, limit=limit, raw=raw
        )

    @tool
    async def list_ad_groups(
        ad_account_id: AccountId = None,
        campaign_id: Annotated[str | None, Field(description="Only this campaign.")] = None,
        status: str | None = None,
        limit: int = 200,
        raw: bool = False,
    ) -> dict[str, Any]:
        """List ad groups (targeting container: bid, goal, schedule) of an account or campaign."""
        return await service.list_entities(
            await runtime.client(), "ad_groups", ad_account_id,
            status=status, campaign_id=campaign_id, limit=limit, raw=raw,
        )  # fmt: skip

    @tool
    async def list_ads(
        ad_account_id: AccountId = None,
        campaign_id: str | None = None,
        ad_group_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
        raw: bool = False,
    ) -> dict[str, Any]:
        """List ads with name, status and destination click_url (useful to audit UTMs)."""
        return await service.list_entities(
            await runtime.client(), "ads", ad_account_id, status=status,
            campaign_id=campaign_id, ad_group_id=ad_group_id, limit=limit, raw=raw,
        )  # fmt: skip

    @tool
    async def get_report(
        breakdowns: Annotated[
            list[str],
            Field(description="1-3 breakdowns, e.g. ['CAMPAIGN_ID'] or ['AD_ID', 'DATE']"),
        ],
        ad_account_id: AccountId = None,
        fields: Annotated[
            list[str] | None,
            Field(
                description="Metrics; see list_report_fields. Default: delivery + key conversions."
            ),
        ] = None,
        days: Days = None,
        start_date: StartDate = None,
        end_date: EndDate = None,
        sort_by: Annotated[
            str | None,
            Field(description="Metric to sort by, descending (default spend; date ascending)."),
        ] = None,
        limit: Annotated[
            int, Field(description="Rows to return (default 50).", ge=1, le=1000)
        ] = 50,
        max_rows: Annotated[
            int,
            Field(
                description="Rows to fetch before stopping (max and default 20000).",
                ge=1,
                le=20000,
            ),
        ] = 20000,
        time_zone: Annotated[
            str | None, Field(description="IANA zone; default is the account's.")
        ] = None,
    ) -> dict[str, Any]:
        """Performance report with breakdowns. Adds campaign/ad group/ad names and totals."""
        return await service.run_report(
            await runtime.client(), ad_account_id, breakdowns=breakdowns, fields=fields, days=days,
            start_date=start_date, end_date=end_date, time_zone=time_zone, sort_by=sort_by,
            limit=limit, max_rows=max_rows,
        )  # fmt: skip

    @tool
    async def performance_overview(
        ad_account_id: AccountId = None,
        days: Days = 30,
        start_date: StartDate = None,
        end_date: EndDate = None,
        top_n: Annotated[
            int, Field(description="Rows for the top ads/communities lists.", ge=1, le=50)
        ] = 10,
    ) -> dict[str, Any]:
        """Totals, monthly trend, campaigns, top ads, placements and top communities in one call."""
        return await service.performance_overview(
            await runtime.client(), ad_account_id, days=days, start_date=start_date,
            end_date=end_date, top_n=top_n,
        )  # fmt: skip

    @tool
    async def list_report_fields() -> dict[str, Any]:
        """Common report metrics and breakdowns. The full list is in Reddit's metric reference."""
        return {
            "fields": REPORT_FIELDS,
            "breakdowns": REPORT_BREAKDOWNS,
            "reference": "https://ads-api.reddit.com/docs/v3/guides/programs/reporting/metric-reference",
            "notes": [
                "Max 3 breakdowns (4 with COUNTRY and REGION).",
                "Verified to sum to the account total: DATE, CAMPAIGN_ID, AD_GROUP_ID, AD_ID, "
                "PLACEMENT, COUNTRY, OS_TYPE. COMMUNITY and LANGUAGE rows overlap (about 2x).",
                "Metrics can take up to 6 hours to settle; recent conversions may still change.",
                service.UNITS_NOTE,
            ],
        }

    server._reddit_ads_version = __version__
    return server
