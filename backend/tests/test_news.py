import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.news import NewsAggregator, aggregate_news, canonical_url, parse_okx, parse_rss


NOW = datetime(2026, 7, 12, 3, 0, tzinfo=timezone.utc)


def rss(*items):
    body = "".join(
        f"<item><title>{title}</title><link>{url}</link><pubDate>{published}</pubDate>"
        f"<description>this summary must not be retained</description></item>"
        for title, url, published in items
    )
    return f"<?xml version='1.0'?><rss><channel>{body}</channel></rss>"


def test_canonical_url_removes_tracking_but_keeps_meaningful_query():
    assert canonical_url("HTTPS://Example.com/a/?utm_source=x&b=2&a=1#frag") == "https://example.com/a?a=1&b=2"
    assert canonical_url("javascript:alert(1)") == ""


def test_rss_uses_only_metadata_and_filters_irrelevant_items():
    payload = rss(
        ("Bitcoin ETF records net inflow", "https://x.test/btc?utm_medium=rss", "Sun, 12 Jul 2026 01:00:00 GMT"),
        ("A football result", "https://x.test/sport", "Sun, 12 Jul 2026 01:00:00 GMT"),
    )
    items = parse_rss("coindesk", payload, NOW - timedelta(minutes=1))
    assert len(items) == 1
    assert items[0]["url"] == "https://x.test/btc"
    assert "summary" not in items[0]
    json.dumps(items, allow_nan=False)


def test_okx_parser_and_future_cutoffs_are_strict():
    payload = {"code": "0", "data": [{"details": [
        {"title": "OKX Bitcoin contract system upgrade", "url": "https://okx.com/help/a", "pTime": str(int((NOW-timedelta(hours=1)).timestamp()*1000))},
        {"title": "OKX Bitcoin future announcement", "url": "https://okx.com/help/b", "pTime": str(int((NOW+timedelta(seconds=1)).timestamp()*1000))},
    ]}]}
    items = parse_okx(payload, NOW - timedelta(minutes=2))
    result = aggregate_news(items, NOW)
    assert result["itemCount"] == 1
    assert result["clusters"][0]["direction"] == 0

    # Known publication is not enough: information observed after the cutoff is future data too.
    late = dict(items[0], observedAt=(NOW + timedelta(seconds=1)).isoformat())
    assert aggregate_news([late], NOW)["itemCount"] == 0


def test_cross_source_near_duplicate_is_clustered_and_weight_is_auditable():
    observed = NOW - timedelta(minutes=10)
    published = "Sun, 12 Jul 2026 01:00:00 GMT"
    a = parse_rss("coindesk", rss(("Bitcoin ETF posts record net inflow", "https://a.test/1", published)), observed)
    b = parse_rss("decrypt", rss(("Bitcoin ETF records record net inflows", "https://b.test/2", published)), observed)
    result = aggregate_news(a + b, NOW)
    assert result["clusterCount"] == 1
    cluster = result["clusters"][0]
    assert cluster["sourceCount"] == 2
    assert cluster["duplicateCount"] == 2
    assert cluster["direction"] == 1
    assert 1 <= cluster["importance"] <= 5
    assert 0 <= cluster["importanceScore"] <= 100
    assert 0 < result["newsScore"] <= 15


def test_score_is_strictly_capped_in_both_directions():
    positive, negative = [], []
    for index in range(30):
        at = NOW - timedelta(minutes=index + 1)
        positive.extend(parse_rss("coindesk", rss((f"Company {index} buys Bitcoin for treasury", f"https://p.test/{index}", at.strftime("%a, %d %b %Y %H:%M:%S GMT"))), at))
        negative.extend(parse_rss("coindesk", rss((f"Company {index} sells Bitcoin treasury", f"https://n.test/{index}", at.strftime("%a, %d %b %Y %H:%M:%S GMT"))), at))
    assert aggregate_news(positive, NOW)["newsScore"] == 15
    assert aggregate_news(negative, NOW)["newsScore"] == -15


def test_asset_direction_is_not_confused_by_positive_sounding_treasury_words():
    at = NOW - timedelta(minutes=2)
    items = parse_rss("coindesk", rss((
        "Bitcoin treasury company sold about half of its BTC stack",
        "https://x.test/sale", at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
    )), at)
    result = aggregate_news(items, NOW)
    assert result["clusters"][0]["direction"] == -1
    assert result["newsScore"] < 0


@pytest.mark.asyncio
async def test_async_fetch_partial_and_unavailable_degrade_safely():
    good = rss(("Bitcoin ETF net inflow", "https://news.test/a", "Sun, 12 Jul 2026 01:00:00 GMT"))

    def partial_handler(request):
        if "coindesk" in request.url.host:
            return httpx.Response(200, text=good)
        return httpx.Response(503, text="down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(partial_handler)) as client:
        result = await NewsAggregator(client).fetch(NOW)
    assert result["status"] == "partial"
    assert -15 <= result["newsScore"] <= 15

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        unavailable = await NewsAggregator(client).fetch()
    assert unavailable["status"] == "unavailable"
    assert unavailable["newsScore"] == 0
    assert unavailable["clusters"] == []
    json.dumps(unavailable, allow_nan=False)
