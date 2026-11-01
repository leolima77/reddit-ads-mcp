"""Tool logic. Each function takes a RedditAdsClient and returns plain JSON-serializable data."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from reddit_ads_mcp.client import RedditAdsClient
from reddit_ads_mcp.errors import RedditAdsError
from reddit_ads_mcp.reports import (
    ADDITIVE_BREAKDOWNS,
    DEFAULT_FIELDS,
    build_body,
    clean_names,
    fetch_rows,
    max_breakdowns,
    resolve_window,
    totals_of,
)

UNITS_NOTE = (
    "Money fields (spend, cpc, ecpm, *_ecpa) are in the ad account's currency, not micros. "
    "ctr is a ratio (0.01 = 1%)."
)
_ASCENDING_KEYS = {"date", "datetime", "hour"}
# breakdown column -> (entity kind, name column added to the rows)
_NAME_JOINS = {
    "campaign_id": ("campaigns", "campaign_name"),
    "ad_group_id": ("ad_groups", "ad_group_name"),
    "ad_id": ("ads", "ad_name"),
}


def slim(obj: dict[str, Any], raw: bool = False) -> dict[str, Any]:
    """Keep scalar fields only; nested creative payloads would flood the model's context."""
    if raw:
        return obj
    return {k: v for k, v in obj.items() if isinstance(v, (str, int, float, bool))}


# --- account resolution ---------------------------------------------------------------------


async def resolve_account(client: RedditAdsClient, ad_account_id: str | None) -> str:
    account = ad_account_id or client.settings.account_id
    if account:
        return account
    business = client.settings.business_id
    if not business:
        raise RedditAdsError(
            "ad_account_id is required. Call list_ad_accounts, or set REDDIT_ADS_ACCOUNT_ID "
            "(or REDDIT_ADS_BUSINESS_ID) in the server configuration."
        )
    accounts = await client.paged(f"businesses/{business}/ad_accounts")
    if len(accounts) == 1:
        return accounts[0]["id"]
    raise RedditAdsError(
        f"The business has {len(accounts)} ad accounts; pass ad_account_id "
        "(see list_ad_accounts) or set REDDIT_ADS_ACCOUNT_ID."
    )


async def account_info(client: RedditAdsClient, account_id: str) -> dict[str, Any]:
    """Currency and time zone of the account. Best effort: reports still work without them."""
    key = ("account", account_id)
    if key not in client.cache:
        try:
            payload = await client.get(f"ad_accounts/{account_id}")
            data = payload.get("data") if isinstance(payload, dict) else None
            client.cache[key] = data if isinstance(data, dict) else {}
        except RedditAdsError:
            client.cache[key] = {}
    return client.cache[key]


async def list_ad_accounts(client: RedditAdsClient, business_id: str | None) -> dict[str, Any]:
    business = business_id or client.settings.business_id
    if not business:
        raise RedditAdsError("business_id is required (or set REDDIT_ADS_BUSINESS_ID).")
    accounts = await client.paged(f"businesses/{business}/ad_accounts")
    keep = ("id", "name", "type", "currency", "time_zone_id", "admin_approval", "created_at")
    return {"ad_accounts": [{k: a[k] for k in keep if k in a} for a in accounts]}


# --- campaigns, ad groups, ads --------------------------------------------------------------


async def _entities(client: RedditAdsClient, account: str, kind: str) -> list[dict[str, Any]]:
    key = (kind, account)
    if key not in client.cache:
        client.cache[key] = await client.paged(f"ad_accounts/{account}/{kind}", max_items=5000)
    return client.cache[key]


def _matches(item: dict[str, Any], status: str | None, equals: dict[str, str | None]) -> bool:
    for field, wanted in equals.items():
        if wanted and str(item.get(field)) != wanted:
            return False
    if status:
        statuses = {str(item.get("effective_status")), str(item.get("configured_status"))}
        return status.upper() in statuses
    return True


async def list_entities(
    client: RedditAdsClient,
    kind: str,
    ad_account_id: str | None,
    *,
    status: str | None = None,
    campaign_id: str | None = None,
    ad_group_id: str | None = None,
    limit: int = 200,
    raw: bool = False,
) -> dict[str, Any]:
    account = await resolve_account(client, ad_account_id)
    equals = {"campaign_id": campaign_id, "ad_group_id": ad_group_id}
    items = [i for i in await _entities(client, account, kind) if _matches(i, status, equals)]
    return {
        "ad_account_id": account,
        kind: [slim(i, raw) for i in items[:limit]],
        "total": len(items),
        "truncated": len(items) > limit,
        "note": "Budget, goal and bid values are in micros of the account currency.",
    }


async def _add_names(client: RedditAdsClient, account: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    for column, (kind, name_column) in _NAME_JOINS.items():
        if column not in rows[0]:
            continue
        by_id = {str(e.get("id")): e for e in await _entities(client, account, kind)}
        for row in rows:
            entity = by_id.get(str(row.get(column)))
            if entity:
                row[name_column] = entity.get("name")
                if kind == "ads" and entity.get("click_url"):
                    row["click_url"] = entity["click_url"]


# --- reports --------------------------------------------------------------------------------


@dataclass
class _Collected:
    account: str
    info: dict[str, Any]
    tz: str
    start: date
    end: date
    breakdowns: list[str]
    rows: list[dict[str, Any]]
    complete: bool


async def _collect(
    client: RedditAdsClient,
    ad_account_id: str | None,
    *,
    breakdowns: list[str] | None,
    fields: list[str] | None,
    days: int | None,
    start_date: str | None,
    end_date: str | None,
    time_zone: str | None,
    max_rows: int,
) -> _Collected:
    account = await resolve_account(client, ad_account_id)
    info = await account_info(client, account)
    tz = time_zone or info.get("time_zone_id") or "UTC"
    group_by = clean_names(breakdowns, "breakdown", 4)
    if len(group_by) > max_breakdowns(group_by):
        raise RedditAdsError("At most 3 breakdowns per report (4 with COUNTRY and REGION).")
    if not group_by:
        raise RedditAdsError(
            "Provide at least one breakdown, e.g. ['CAMPAIGN_ID'] or ['COMMUNITY']."
        )
    metrics = clean_names(fields, "field", 60) or DEFAULT_FIELDS
    start, end = resolve_window(days, start_date, end_date, tz)
    body = build_body(start, end, metrics, group_by, tz)
    rows, complete = await fetch_rows(client, account, body, max_rows)
    return _Collected(account, info, tz, start, end, group_by, rows, complete)


def _sort_rows(rows: list[dict[str, Any]], key: str) -> None:
    """Metrics sort descending; time keys ascending. Rows lacking the key are left alone."""
    values = [r.get(key) for r in rows]
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
    if rows and (numeric or all(isinstance(v, str) for v in values)):
        rows.sort(key=lambda r: r[key], reverse=key not in _ASCENDING_KEYS)


async def run_report(
    client: RedditAdsClient,
    ad_account_id: str | None,
    *,
    breakdowns: list[str] | None,
    fields: list[str] | None = None,
    days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    time_zone: str | None = None,
    sort_by: str | None = None,
    limit: int = 50,
    max_rows: int = 20000,
    with_names: bool = True,
) -> dict[str, Any]:
    got = await _collect(
        client, ad_account_id, breakdowns=breakdowns, fields=fields, days=days,
        start_date=start_date, end_date=end_date, time_zone=time_zone, max_rows=max_rows,
    )  # fmt: skip
    rows = got.rows
    if with_names:
        await _add_names(client, got.account, rows)
    _sort_rows(rows, (sort_by or ("date" if "DATE" in got.breakdowns else "spend")).lower())
    result: dict[str, Any] = {
        "ad_account_id": got.account,
        "period": {"start": got.start.isoformat(), "end": got.end.isoformat(), "time_zone": got.tz},
        "currency": got.info.get("currency"),
        "breakdowns": got.breakdowns,
        "rows": rows[:limit],
        "rows_fetched": len(rows),
        "rows_returned": min(len(rows), limit),
        "complete": got.complete,
        "note": UNITS_NOTE,
    }
    unverified = sorted(set(got.breakdowns) - ADDITIVE_BREAKDOWNS)
    if unverified:
        result["note"] += (
            f" Totals are omitted because {', '.join(unverified)} rows can overlap (COMMUNITY and "
            "LANGUAGE summed to about twice the real spend); compare rows with each other, "
            "not with the account total."
        )
    elif got.complete:
        result["totals"] = totals_of(rows)
    if not got.complete:
        result["warning"] = (
            f"Stopped at max_rows={max_rows}. Reddit returns rows in key order, not by spend, "
            "so the ranking below may miss entries. Use fewer breakdowns or a shorter period."
        )
    return result


_OVERVIEW_KEYS = (
    "campaign_name", "ad_name", "click_url", "placement", "community", "impressions", "clicks",
    "spend", "ctr", "cpc", "key_conversion_total_count", "key_conversion_ecpa",
)  # fmt: skip


async def performance_overview(
    client: RedditAdsClient,
    ad_account_id: str | None,
    *,
    days: int | None = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """One call for the usual first question: where did the money go and what did it buy."""
    window = {"days": days, "start_date": start_date, "end_date": end_date}

    async def cut(breakdowns: list[str], limit: int) -> dict[str, Any]:
        return await run_report(client, ad_account_id, breakdowns=breakdowns, limit=limit, **window)

    def trim(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: r[k] for k in _OVERVIEW_KEYS if k in r} for r in rows]

    campaigns = await cut(["CAMPAIGN_ID"], 50)
    ads = await cut(["AD_ID"], top_n)
    placements = await cut(["PLACEMENT"], 10)
    communities = await cut(["COMMUNITY"], top_n)
    daily = await _collect(
        client, ad_account_id, breakdowns=["DATE"], fields=["IMPRESSIONS", "CLICKS", "SPEND"],
        time_zone=None, max_rows=2000, **window,
    )  # fmt: skip
    months: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in daily.rows:
        months[str(row.get("date", ""))[:7]].append(row)
    return {
        "ad_account_id": campaigns["ad_account_id"],
        "period": campaigns["period"],
        "currency": campaigns["currency"],
        "totals": campaigns.get("totals"),
        "by_month": {m: totals_of(r) for m, r in sorted(months.items())},
        "campaigns": campaigns["rows"],
        "top_ads_by_spend": trim(ads["rows"]),
        "placements": trim(placements["rows"]),
        "top_communities_by_spend": trim(communities["rows"]),
        "note": UNITS_NOTE,
    }
