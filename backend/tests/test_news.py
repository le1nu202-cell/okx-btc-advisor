import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.news import SOURCES, NewsAggregator, aggregate_news, canonical_url, parse_okx, parse_rss


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
    assert cluster["weightBreakdown"]["corroboration"] == pytest.approx(.5)
    assert cluster["halfLifeHours"] == 24
    assert "重要度=相关度" in cluster["weightFormula"]
    assert 0 < result["newsScore"] <= 15


def test_cluster_rejects_opposite_direction_and_distant_repeated_headlines():
    observed = NOW - timedelta(minutes=5)
    published = (NOW - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    positive = parse_rss("coindesk", rss(("Bitcoin ETF records net inflow", "https://a.test/in", published)), observed)
    negative = parse_rss("decrypt", rss(("Bitcoin ETF records net outflow", "https://b.test/out", published)), observed)
    assert aggregate_news(positive + negative, NOW)["clusterCount"] == 2

    first_at = NOW - timedelta(hours=25)
    second_at = NOW - timedelta(hours=1)
    first = parse_rss("coindesk", rss(("Company buys Bitcoin for treasury", "https://a.test/day1", first_at.strftime("%a, %d %b %Y %H:%M:%S GMT"))), first_at)
    second = parse_rss("coindesk", rss(("Company buys Bitcoin for treasury", "https://a.test/day2", second_at.strftime("%a, %d %b %Y %H:%M:%S GMT"))), second_at)
    assert aggregate_news(first + second, NOW)["clusterCount"] == 2


def test_later_corroboration_does_not_reset_event_decay():
    first_at = NOW - timedelta(hours=10)
    later_at = NOW - timedelta(hours=1)
    first = parse_rss("coindesk", rss(("Bitcoin ETF posts record net inflow", "https://a.test/first", first_at.strftime("%a, %d %b %Y %H:%M:%S GMT"))), first_at)
    later = parse_rss("decrypt", rss(("Bitcoin ETF records record net inflows", "https://b.test/later", later_at.strftime("%a, %d %b %Y %H:%M:%S GMT"))), later_at)
    cluster = aggregate_news(first + later, NOW)["clusters"][0]
    assert cluster["sourceCount"] == 2
    assert cluster["ageHours"] == pytest.approx(10)
    assert cluster["timeDecay"] == pytest.approx(2 ** (-10 / 24), rel=1e-3)


def test_source_coverage_scales_score_and_explicit_empty_status_is_unavailable():
    at = NOW - timedelta(minutes=5)
    items = parse_rss("coindesk", rss(("Bitcoin ETF records net inflow", "https://a.test/coverage", at.strftime("%a, %d %b %Y %H:%M:%S GMT"))), at)
    full = aggregate_news(items, NOW, [{"source":"a","ok":True},{"source":"b","ok":True}])
    partial = aggregate_news(items, NOW, [{"source":"a","ok":True},{"source":"b","ok":False}])
    unavailable = aggregate_news(items, NOW, [])
    assert full["sourceCoverage"] == 1
    assert partial["sourceCoverage"] == .5
    assert 0 <= partial["newsScore"] <= full["newsScore"]
    assert unavailable["status"] == "unavailable" and unavailable["newsScore"] == 0


def test_corrupt_persisted_row_is_skipped_without_poisoning_valid_news():
    at = NOW - timedelta(minutes=5)
    valid = parse_rss("coindesk", rss(("Bitcoin ETF records net inflow", "https://a.test/valid", at.strftime("%a, %d %b %Y %H:%M:%S GMT"))), at)
    result = aggregate_news([{"title":"Bitcoin", "url":"javascript:bad", "publishedAt":"bad"}, *valid], NOW)
    assert result["itemCount"] == 1 and result["clusterCount"] == 1


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


@pytest.mark.asyncio
async def test_news_redirects_are_rejected_without_following_the_target():
    requests=[]
    def redirect_handler(request):
        requests.append(str(request.url))
        return httpx.Response(302,headers={"location":"http://127.0.0.1/private"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect_handler)) as client:
        result=await NewsAggregator(client).fetch()
    assert result["status"]=="unavailable"
    assert len(requests)==len(SOURCES)
    assert all("127.0.0.1" not in request for request in requests)


@pytest.mark.asyncio
async def test_fetch_exposes_stable_raw_articles_for_storage():
    feed=rss(("Bitcoin ETF net inflow","https://news.test/a","Sun, 12 Jul 2026 01:00:00 GMT"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,text=feed))) as client:
        result=await NewsAggregator(client).fetch(NOW)
    assert result["rawItems"]
    assert result["rawItems"][0]["url"]=="https://news.test/a"


@pytest.mark.asyncio
async def test_slow_fetch_is_observed_only_after_response_and_parsing(monkeypatch):
    feed=rss(("Bitcoin ETF net inflow","https://news.test/slow","Sun, 12 Jul 2026 01:00:00 GMT"))
    completed=NOW+timedelta(minutes=5)
    clock={"now":NOW}

    async def delayed_handler(request):
        # Simulate a decision cutoff passing while every request is in flight.
        clock["now"]=completed
        if request.url.host=="openapi.okx.com":
            return httpx.Response(200,json={"code":"0","data":[]})
        return httpx.Response(200,text=feed)

    monkeypatch.setattr("backend.news._utc_now",lambda:clock["now"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(delayed_handler)) as client:
        result=await NewsAggregator(client).fetch(NOW)

    assert result["rawItems"]
    assert {item["observedAt"] for item in result["rawItems"]}=={"2026-07-12T03:05:00Z"}
    assert {status["observedAt"] for status in result["sourceStatus"]}=={"2026-07-12T03:05:00Z"}
    assert result["itemCount"]==0


@pytest.mark.asyncio
async def test_fast_source_cannot_backdate_across_slow_batch_commit(monkeypatch):
    before=NOW-timedelta(seconds=10);after=NOW+timedelta(seconds=10);clock={"now":before}
    article=parse_rss("coindesk",rss(("Bitcoin ETF net inflow","https://news.test/fast","Sun, 12 Jul 2026 01:00:00 GMT")),before)[0]
    aggregator=NewsAggregator(client=object())
    async def fake_one(client,source):
        if source=="okx":return [dict(article)],{"source":source,"ok":True,"itemCount":1,"observedAt":before.isoformat(),"error":None}
        clock["now"]=after
        return [],{"source":source,"ok":True,"itemCount":0,"observedAt":after.isoformat(),"error":None}
    monkeypatch.setattr(aggregator,"_one",fake_one)
    monkeypatch.setattr("backend.news._utc_now",lambda:clock["now"])
    result=await aggregator.fetch(NOW)
    assert {item["observedAt"] for item in result["rawItems"]}=={"2026-07-12T03:00:10Z"}
    assert {status["observedAt"] for status in result["sourceStatus"]}=={"2026-07-12T03:00:10Z"}
    assert result["itemCount"]==0
