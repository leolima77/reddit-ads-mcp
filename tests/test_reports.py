from datetime import date

import pytest

from reddit_ads_mcp.errors import RedditAdsError
from reddit_ads_mcp.reports import (
    build_body,
    clean_names,
    divisor,
    normalize_row,
    resolve_window,
    totals_of,
)


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("SPEND", 1_000_000),
        ("cpc", 1_000_000),
        ("ECPM", 1_000_000),
        ("KEY_CONVERSION_ECPA", 1_000_000),
        ("CONVERSION_SIGN_UP_ECPA", 1_000_000),
        ("CONVERSION_PURCHASE_TOTAL_VALUE", 100),
        ("CLICKS", 1),
        ("CTR", 1),
        ("REACH", 1),
    ],
)
def test_divisor(field, expected):
    assert divisor(field) == expected


def test_normalize_row_scales_money_only_and_lowercases_keys():
    row = {"SPEND": 2_500_000, "clicks": 10, "cpc": 250_000, "community": "portugal", "ctr": 0.01}
    assert normalize_row(row) == {
        "spend": 2.5,
        "clicks": 10,
        "cpc": 0.25,
        "community": "portugal",
        "ctr": 0.01,
    }


def test_clean_names_uppercases_and_validates():
    assert clean_names([" campaign_id ", "date"], "breakdown", 3) == ["CAMPAIGN_ID", "DATE"]
    with pytest.raises(RedditAdsError):
        clean_names(["bad name;drop"], "breakdown", 3)
    with pytest.raises(RedditAdsError):
        clean_names(["A1", "B1", "C1", "D1", "E1"], "breakdown", 4)


def test_window_with_dates_is_inclusive_and_end_is_sent_as_is():
    start, end = resolve_window(None, "2026-07-14", "2026-07-14", "UTC")
    assert (start, end) == (date(2026, 7, 14), date(2026, 7, 14))
    body = build_body(start, end, ["CLICKS"], ["DATE"], "Europe/London")["data"]
    assert body["starts_at"] == "2026-07-14T00:00:00Z"
    assert body["ends_at"] == "2026-07-14T00:00:00Z"  # the API treats the end date as inclusive
    assert body["time_zone_id"] == "Europe/London"


def test_window_defaults_to_last_n_days_and_validates():
    start, end = resolve_window(7, None, None, "UTC")
    assert (end - start).days == 6
    with pytest.raises(RedditAdsError):
        resolve_window(None, "2026-07-15", "2026-07-14", "UTC")
    with pytest.raises(RedditAdsError):
        resolve_window(None, "14/07/2026", None, "UTC")
    with pytest.raises(RedditAdsError):
        resolve_window(0, None, None, "UTC")
    resolve_window(7, None, None, "Not/AZone")  # unknown zone falls back to UTC


def test_totals_of_derives_ratios_and_handles_zero():
    rows = [
        {"impressions": 1000, "clicks": 10, "spend": 2.0},
        {"impressions": 3000, "clicks": 30, "spend": 6.0},
    ]
    assert totals_of(rows) == {
        "impressions": 4000,
        "clicks": 40,
        "spend": 8.0,
        "ctr": 0.01,
        "cpc": 0.2,
        "ecpm": 2.0,
    }
    assert totals_of([])["ctr"] is None
