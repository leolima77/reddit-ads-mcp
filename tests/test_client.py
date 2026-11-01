import httpx
import pytest

from reddit_ads_mcp.errors import RedditAdsError
from reddit_ads_mcp.reports import PAGE_SIZE, fetch_rows

from .conftest import API, json_response


@pytest.mark.anyio
async def test_access_token_is_cached_across_requests(client, fake):
    fake.on("GET /ping", {"data": []})
    await client.get("ping")
    await client.get("ping")
    assert fake.token_calls == 1
    assert fake.calls[-1].headers["authorization"] == "Bearer AT1"
    assert "reddit-ads-mcp" in fake.calls[-1].headers["user-agent"]


@pytest.mark.anyio
async def test_429_honours_retry_after_then_succeeds(client, fake):
    answers = iter([json_response({}, 429, {"Retry-After": "3"}), json_response({"data": [1]})])
    fake.on("GET /ping", lambda _r: next(answers))
    assert await client.get("ping") == {"data": [1]}
    assert fake.sleeps == [3.0]


@pytest.mark.anyio
async def test_rate_limit_header_hint_is_used_without_retry_after(client, fake):
    answers = iter(
        [httpx.Response(429, headers={"RateLimit": '"ads-reporting";r=0;t=7'}), json_response({})]
    )
    fake.on("GET /ping", lambda _r: next(answers))
    await client.get("ping")
    assert fake.sleeps == [7.5]


@pytest.mark.anyio
async def test_401_refreshes_token_once(client, fake):
    answers = iter([json_response({}, 401), json_response({"ok": True})])
    fake.on("GET /ping", lambda _r: next(answers))
    assert await client.get("ping") == {"ok": True}
    assert fake.token_calls == 2


@pytest.mark.anyio
async def test_errors_are_readable_and_leak_nothing(client, fake):
    fake.on("GET /ping", json_response({"message": "Forbidden thing"}, 403))
    with pytest.raises(RedditAdsError) as excinfo:
        await client.get("ping")
    text = str(excinfo.value)
    assert "403" in text
    assert "Forbidden thing" in text
    for secret in ("csecret", "RT-secret", "AT1"):
        assert secret not in text


@pytest.mark.anyio
async def test_gives_up_after_max_attempts(client, fake):
    fake.on("GET /ping", json_response({}, 503))
    with pytest.raises(RedditAdsError):
        await client.get("ping")
    assert len(fake.sleeps) == 4


@pytest.mark.anyio
async def test_paged_follows_next_url_and_stops_at_max_items(client, fake):
    def page(request):
        token = request.url.params.get("page.token")
        if token is None:
            return {
                "data": [{"id": 1}, {"id": 2}],
                "pagination": {"next_url": f"{API}/items?page.token=p2"},
            }
        return {"data": [{"id": 3}], "pagination": {"next_url": None}}

    fake.on("GET /items", page)
    assert [i["id"] for i in await client.paged("items")] == [1, 2, 3]
    assert [i["id"] for i in await client.paged("items", max_items=2)] == [1, 2]


@pytest.mark.anyio
async def test_never_sends_the_token_to_another_host(client, fake):
    fake.on(
        "GET /items",
        {"data": [{"id": 1}], "pagination": {"next_url": "https://evil.example/steal"}},
    )
    with pytest.raises(RedditAdsError, match="different host"):
        await client.paged("items")
    assert all(r.url.host != "evil.example" for r in fake.calls)


@pytest.mark.anyio
async def test_report_pagination_keeps_page_token(client, fake):
    """Regression: passing params= on the follow-up request used to drop page.token and
    re-fetch page one forever."""
    seen_tokens = []

    def report(request):
        token = request.url.params.get("page.token")
        seen_tokens.append((token, request.url.params.get("page.size")))
        if token is None:
            return {
                "data": {"metrics": [{"community": "a", "spend": 1_000_000}]},
                "pagination": {"next_url": f"{API}/ad_accounts/acc/reports?page.token=T2"},
            }
        return {"data": {"metrics": [{"community": "b", "spend": 2_000_000}]}, "pagination": {}}

    fake.on("POST /ad_accounts/acc/reports", report)
    rows, complete = await fetch_rows(client, "acc", {"data": {}}, 1000)
    assert complete
    assert [(r["community"], r["spend"]) for r in rows] == [("a", 1.0), ("b", 2.0)]
    assert seen_tokens == [(None, str(PAGE_SIZE)), ("T2", str(PAGE_SIZE))]


@pytest.mark.anyio
async def test_report_pagination_detects_a_loop(client, fake):
    fake.on(
        "POST /ad_accounts/acc/reports",
        {
            "data": {"metrics": [{"community": "a"}]},
            "pagination": {"next_url": f"{API}/ad_accounts/acc/reports?page.token=SAME"},
        },
    )
    with pytest.raises(RedditAdsError, match="did not advance"):
        await fetch_rows(client, "acc", {"data": {}}, 1000)


@pytest.mark.anyio
async def test_report_max_rows_marks_incomplete(client, fake):
    fake.on(
        "POST /ad_accounts/acc/reports",
        {
            "data": {"metrics": [{"community": str(i)} for i in range(5)]},
            "pagination": {"next_url": f"{API}/ad_accounts/acc/reports?page.token=N"},
        },
    )
    rows, complete = await fetch_rows(client, "acc", {"data": {}}, 3)
    assert len(rows) == 3
    assert not complete


@pytest.mark.anyio
async def test_validation_errors_from_reddit_are_passed_on(client, fake):
    fake.on(
        "GET /ping",
        json_response(
            {
                "error": {
                    "code": 400,
                    "message": "Bad Request",
                    "fields": [
                        {"field": "data/breakdowns/0", "message": "'X' is not one of ['DATE']"}
                    ],
                }
            },
            400,
        ),
    )
    with pytest.raises(RedditAdsError) as excinfo:
        await client.get("ping")
    assert "data/breakdowns/0: 'X' is not one of ['DATE']" in str(excinfo.value)
