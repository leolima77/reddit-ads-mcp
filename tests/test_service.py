import pytest

from reddit_ads_mcp import service
from reddit_ads_mcp.config import Settings
from reddit_ads_mcp.errors import RedditAdsError

from .conftest import API


def with_settings(client, **changes):
    values = {**client.settings.__dict__, **changes}
    client.settings = Settings(**values)
    return client


def report_route(fake, metrics, account="acc"):
    fake.on(f"POST /ad_accounts/{account}/reports", {"data": {"metrics": metrics}})
    fake.on(
        f"GET /ad_accounts/{account}",
        {"data": {"currency": "EUR", "time_zone_id": "Europe/London"}},
    )


@pytest.mark.anyio
async def test_account_resolution_order(client, fake):
    assert await service.resolve_account(client, "explicit") == "explicit"
    with pytest.raises(RedditAdsError, match="ad_account_id is required"):
        await service.resolve_account(client, None)

    with_settings(client, account_id="from_env")
    assert await service.resolve_account(client, None) == "from_env"

    with_settings(client, account_id=None, business_id="biz")
    fake.on("GET /businesses/biz/ad_accounts", {"data": [{"id": "only"}]})
    assert await service.resolve_account(client, None) == "only"

    fake.on("GET /businesses/biz/ad_accounts", {"data": [{"id": "a"}, {"id": "b"}]})
    with pytest.raises(RedditAdsError, match="2 ad accounts"):
        await service.resolve_account(client, None)


@pytest.mark.anyio
async def test_list_entities_filters_slims_and_limits(client, fake):
    fake.on(
        "GET /ad_accounts/acc/ads",
        {
            "data": [
                {
                    "id": "1",
                    "campaign_id": "c1",
                    "effective_status": "ACTIVE",
                    "creative": {"big": 1},
                    "click_url": "u",
                },
                {
                    "id": "2",
                    "campaign_id": "c2",
                    "effective_status": "PAUSED",
                    "configured_status": "ACTIVE",
                },
                {"id": "3", "campaign_id": "c1", "effective_status": "PAUSED"},
            ]
        },
    )
    out = await service.list_entities(client, "ads", "acc", campaign_id="c1")
    assert [a["id"] for a in out["ads"]] == ["1", "3"]
    assert "creative" not in out["ads"][0]  # nested payloads are dropped unless raw=True
    assert out["ads"][0]["click_url"] == "u"

    paused = await service.list_entities(client, "ads", "acc", status="paused")
    assert [a["id"] for a in paused["ads"]] == ["2", "3"]

    limited = await service.list_entities(client, "ads", "acc", limit=1)
    assert limited["truncated"] and limited["total"] == 3

    raw = await service.list_entities(client, "ads", "acc", limit=1, raw=True)
    assert "creative" in raw["ads"][0]


@pytest.mark.anyio
async def test_report_adds_names_totals_currency_and_sorts_by_spend(client, fake):
    report_route(
        fake,
        [
            {"campaign_id": "c1", "impressions": 1000, "clicks": 10, "spend": 1_000_000},
            {"campaign_id": "c2", "impressions": 3000, "clicks": 30, "spend": 3_000_000},
        ],
    )
    fake.on(
        "GET /ad_accounts/acc/campaigns",
        {"data": [{"id": "c1", "name": "One"}, {"id": "c2", "name": "Two"}]},
    )
    out = await service.run_report(client, "acc", breakdowns=["campaign_id"], days=7)
    assert [r["campaign_name"] for r in out["rows"]] == ["Two", "One"]
    assert out["totals"]["spend"] == 4.0
    assert out["currency"] == "EUR"
    assert out["period"]["time_zone"] == "Europe/London"
    assert out["complete"] is True
    assert "micros" in out["note"]


@pytest.mark.anyio
async def test_overlapping_breakdowns_never_get_totals(client, fake):
    report_route(
        fake, [{"community": "a", "spend": 1_000_000}, {"community": "b", "spend": 1_000_000}]
    )
    out = await service.run_report(client, "acc", breakdowns=["COMMUNITY"], days=7)
    assert "totals" not in out
    assert "COMMUNITY" in out["note"] and "overlap" in out["note"]


@pytest.mark.anyio
async def test_unverified_breakdowns_get_no_totals_but_verified_ones_do(client, fake):
    report_route(fake, [{"os_type": "IOS", "spend": 1_000_000}])
    assert "totals" in await service.run_report(client, "acc", breakdowns=["OS_TYPE"], days=7)
    assert "totals" not in await service.run_report(client, "acc", breakdowns=["AGE"], days=7)


@pytest.mark.anyio
async def test_breakdown_limit_is_three_or_four_with_country_and_region(client, fake):
    report_route(fake, [{"spend": 1}])
    four = ["DATE", "AD_ID", "COMMUNITY", "PLACEMENT"]
    with pytest.raises(RedditAdsError, match="At most 3"):
        await service.run_report(client, "acc", breakdowns=four, days=7)
    await service.run_report(client, "acc", breakdowns=["COUNTRY", "REGION", "DMA", "DATE"], days=7)


@pytest.mark.anyio
async def test_date_reports_sort_ascending_and_limit_rows(client, fake):
    report_route(
        fake,
        [{"date": f"2026-07-0{d}", "spend": d * 1_000_000, "clicks": d} for d in (3, 1, 2)],
    )
    out = await service.run_report(client, "acc", breakdowns=["DATE"], days=7, limit=2)
    assert [r["date"] for r in out["rows"]] == ["2026-07-01", "2026-07-02"]
    assert out["rows_fetched"] == 3 and out["rows_returned"] == 2


@pytest.mark.anyio
async def test_incomplete_report_warns_instead_of_claiming_totals(client, fake):
    fake.on("GET /ad_accounts/acc", {"data": {}})
    fake.on(
        "POST /ad_accounts/acc/reports",
        {
            "data": {"metrics": [{"campaign_id": str(i), "spend": 1} for i in range(10)]},
            "pagination": {"next_url": f"{API}/ad_accounts/acc/reports?page.token=N"},
        },
    )
    out = await service.run_report(
        client, "acc", breakdowns=["CAMPAIGN_ID"], days=7, max_rows=5, with_names=False
    )
    assert out["complete"] is False
    assert "totals" not in out
    assert "warning" in out


@pytest.mark.anyio
async def test_report_requires_a_breakdown(client, fake):
    with pytest.raises(RedditAdsError, match="at least one breakdown"):
        await service.run_report(client, "acc", breakdowns=[], days=7)
