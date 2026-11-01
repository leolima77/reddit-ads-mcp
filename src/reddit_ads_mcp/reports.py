"""Report building: validation, unit normalization and totals (I/O only in fetch_rows)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reddit_ads_mcp.client import RedditAdsClient
from reddit_ads_mcp.errors import RedditAdsError

DEFAULT_FIELDS = [
    "IMPRESSIONS", "CLICKS", "SPEND", "CTR", "CPC", "ECPM", "REACH", "FREQUENCY",
    "KEY_CONVERSION_TOTAL_COUNT", "KEY_CONVERSION_ECPA",
]  # fmt: skip
# Breakdowns whose rows were verified against a real account to sum to the account total.
# Others (COMMUNITY and LANGUAGE summed to ~2x the real spend) may overlap, so no totals for them.
ADDITIVE_BREAKDOWNS = {
    "DATE", "HOUR", "CAMPAIGN_ID", "AD_GROUP_ID", "AD_ID", "PLACEMENT", "COUNTRY", "OS_TYPE",
}  # fmt: skip
HARD_ROW_CAP = 20_000
PAGE_SIZE = 500  # the API default is 50; 500 is accepted and keeps big breakdowns to a few calls
_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_MICROS = {"SPEND", "CPC", "CPV", "ECPM"}


def divisor(field: str) -> int:
    """Per the API docs: money fields are micros, CONVERSION*TOTAL_VALUE is in cents."""
    name = field.upper()
    if name in _MICROS or name.endswith("_ECPA"):
        return 1_000_000
    if name.startswith("CONVERSION") and name.endswith("TOTAL_VALUE"):
        return 100
    return 1


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        div = divisor(key)
        if div != 1 and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = round(value / div, 6)
        out[key.lower()] = value
    return out


def max_breakdowns(breakdowns: list[str]) -> int:
    """The API allows 3 breakdowns, or 4 when COUNTRY and REGION are both among them."""
    return 4 if {"COUNTRY", "REGION"} <= set(breakdowns) else 3


def clean_names(values: list[str] | None, what: str, maximum: int) -> list[str]:
    cleaned = [v.strip().upper() for v in values or [] if v and v.strip()]
    for value in cleaned:
        if not _NAME.match(value):
            raise RedditAdsError(f"Invalid {what} name: {value!r}")
    if len(cleaned) > maximum:
        raise RedditAdsError(f"At most {maximum} {what}s per report.")
    return cleaned


def _parse_day(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RedditAdsError(f"{label} must be YYYY-MM-DD, got {value!r}.") from exc


def resolve_window(
    days: int | None, start_date: str | None, end_date: str | None, tz_name: str | None
) -> tuple[date, date]:
    """Inclusive [start, end]. Defaults to the last `days` days ending today (account time zone)."""
    if start_date or end_date:
        start = _parse_day(start_date or end_date or "", "start_date")
        end = _parse_day(end_date or start_date or "", "end_date")
    else:
        try:
            today = datetime.now(ZoneInfo(tz_name or "UTC")).date()
        except ZoneInfoNotFoundError:
            today = datetime.now(timezone.utc).date()
        span = 30 if days is None else days
        if not 1 <= span <= 730:
            raise RedditAdsError("days must be between 1 and 730 (Reddit keeps 24 months).")
        start, end = today - timedelta(days=span - 1), today
    if start > end:
        raise RedditAdsError("start_date must not be after end_date.")
    return start, end


def build_body(
    start: date, end: date, fields: list[str], breakdowns: list[str], tz_name: str | None
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "starts_at": f"{start.isoformat()}T00:00:00Z",
        "ends_at": f"{end.isoformat()}T00:00:00Z",  # the API treats the end date as inclusive
        "fields": fields,
        "breakdowns": breakdowns,
    }
    if tz_name:
        data["time_zone_id"] = tz_name
    return {"data": data}


def totals_of(rows: list[dict[str, Any]]) -> dict[str, Any]:
    impressions = sum(r.get("impressions") or 0 for r in rows)
    clicks = sum(r.get("clicks") or 0 for r in rows)
    spend = round(sum(r.get("spend") or 0 for r in rows), 6)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend": spend,
        "ctr": round(clicks / impressions, 6) if impressions else None,
        "cpc": round(spend / clicks, 6) if clicks else None,
        "ecpm": round(spend / impressions * 1000, 6) if impressions else None,
    }


async def fetch_rows(
    client: RedditAdsClient, account_id: str, body: dict[str, Any], max_rows: int
) -> tuple[list[dict[str, Any]], bool]:
    """POST the report and follow pagination. Returns (normalized rows, complete)."""
    max_rows = min(max(max_rows, 1), HARD_ROW_CAP)
    rows: list[dict[str, Any]] = []
    url = f"ad_accounts/{account_id}/reports"
    params: dict[str, Any] | None = {"page.size": PAGE_SIZE}
    seen: set[str] = set()
    while True:
        payload = await client.request("POST", url, params=params, json=body)
        data = payload.get("data") or {}
        batch = data.get("metrics") if isinstance(data, dict) else data
        rows.extend(normalize_row(r) for r in batch or [])
        next_url = (payload.get("pagination") or {}).get("next_url")
        if not next_url:
            return rows, True
        if len(rows) >= max_rows:
            return rows[:max_rows], False
        if next_url in seen:
            raise RedditAdsError("Reddit pagination did not advance; aborting to avoid a loop.")
        seen.add(next_url)
        # Keep next_url exactly as given (it carries page.token); passing `params` would
        # replace its query string and silently re-fetch the first page.
        sep = "&" if "?" in next_url else "?"
        url = next_url if "page.size=" in next_url else f"{next_url}{sep}page.size={PAGE_SIZE}"
        params = None
