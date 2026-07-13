from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx


SOURCES = {
    "okx": "https://openapi.okx.com/api/v5/support/announcements",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss",
    "cointelegraph": "https://cointelegraph.com/rss/tag/bitcoin",
    "decrypt": "https://decrypt.co/feed",
}
SOURCE_QUALITY = {"okx": 1.0, "coindesk": 0.85, "decrypt": 0.78, "cointelegraph": 0.76}
SOURCE_PRIORITY = {"okx": 0, "coindesk": 1, "decrypt": 2, "cointelegraph": 3}
TRACKING_QUERY = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
NEWS_WINDOW_HOURS = 48
CROSS_SOURCE_CLUSTER_HOURS = 12
SAME_SOURCE_CLUSTER_HOURS = 2
BTC_WORDS = ("bitcoin", "btc", "比特币")
MACRO_WORDS = (
    "crypto market", "cryptocurrency market", "digital asset market", "加密市场", "数字资产市场",
    "federal reserve", "fed ", "fomc", "inflation", "cpi", "interest rate", "sec ", "美联储",
    "通胀", "利率", "etf", "stablecoin", "流动性",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _dt(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value) -> str | None:
    parsed = _dt(value)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z") if parsed else None


def canonical_url(url: str) -> str:
    """Normalize a link without ever following or fetching it."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_QUERY]
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    host = parts.hostname.lower() if parts.hostname else ""
    if parts.port and not ((parts.scheme == "https" and parts.port == 443) or (parts.scheme == "http" and parts.port == 80)):
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(sorted(query)), ""))


def _title_tokens(title: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", title).lower()
    normalized = re.sub(r"\s*[-|–—]\s*(coindesk|cointelegraph|decrypt|okx).*$", "", normalized)
    words = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", normalized)
    out: set[str] = set()
    stop = {"the", "a", "an", "to", "of", "for", "in", "on", "and", "is", "as", "with"}
    for word in words:
        if re.fullmatch(r"[\u3400-\u9fff]+", word) and len(word) > 1:
            out.update(word[i:i + 2] for i in range(len(word) - 1))
        elif word not in stop:
            out.add(word)
    return out


def _similarity(a: str, b: str) -> float:
    left, right = _title_tokens(a), _title_tokens(b)
    return len(left & right) / len(left | right) if left and right else 0.0


def _contains(text: str, terms) -> bool:
    lowered = f" {unicodedata.normalize('NFKC', text).lower()} "
    return any(term in lowered for term in terms)


@dataclass(frozen=True)
class EventAssessment:
    category: str
    severity: float
    direction: int
    confidence: float
    half_life_hours: int
    reason: str


def _assess(title: str) -> EventAssessment:
    t = unicodedata.normalize("NFKC", title).lower()
    sell_pattern = r"\b(sold|sells|selling|dumps?|liquidat\w*)\b.{0,100}\b(bitcoin|btc)\b"
    buy_pattern = r"\b(buys?|bought|purchases?|adds?)\b.{0,100}\b(bitcoin|btc)\b"
    if re.search(sell_pattern, t) or re.search(r"\b(bitcoin|btc)\b.{0,100}\b(sale|selloff|dump)\b", t):
        return EventAssessment("fundFlow", .75, -1, .8, 24, "大额出售可能增加供给压力")
    if re.search(buy_pattern, t):
        return EventAssessment("adoption", .75, 1, .8, 72, "机构或政府购买可能改善中期需求预期")
    rules = [
        (("hack", "exploit", "stolen", "breach", "黑客", "被盗", "漏洞"),
         EventAssessment("security", .95, -1, .9, 6, "安全事件可能提高避险与抛售压力")),
        (("withdrawal suspended", "halts withdrawals", "bankruptcy", "insolven", "暂停提现", "破产", "资不抵债"),
         EventAssessment("exchangeRisk", .95, -1, .9, 6, "交易平台或偿付风险通常压制市场风险偏好")),
        (("outage", "system disruption", "service unavailable", "宕机", "系统中断", "服务异常"),
         EventAssessment("operations", .8, -1, .75, 6, "交易基础设施异常会增加短期执行风险")),
        (("etf outflow", "record outflow", "net outflow", "etf流出", "净流出"),
         EventAssessment("fundFlow", .85, -1, .85, 24, "ETF或机构资金流出偏空")),
        (("etf inflow", "record inflow", "net inflow", "etf流入", "净流入"),
         EventAssessment("fundFlow", .85, 1, .85, 24, "ETF或机构资金流入偏多")),
        (("etf approved", "etf approval", "approves bitcoin", "批准比特币", "批准现货"),
         EventAssessment("regulation", .95, 1, .85, 24, "重大监管批准通常改善市场准入预期")),
        (("ban bitcoin", "crypto ban", "禁止比特币", "加密禁令", "crackdown"),
         EventAssessment("regulation", .95, -1, .85, 24, "重大禁令或监管打击通常偏空")),
        (("rate hike", "hawkish", "higher rates", "加息", "鹰派"),
         EventAssessment("macro", .9, -1, .8, 24, "流动性收紧通常不利于高波动风险资产")),
        (("rate cut", "dovish", "liquidity injection", "降息", "鸽派", "释放流动性"),
         EventAssessment("macro", .9, 1, .8, 24, "流动性宽松通常有利于风险资产")),
        (("sells bitcoin", "sold bitcoin", "liquidates bitcoin", "出售比特币", "抛售比特币"),
         EventAssessment("fundFlow", .75, -1, .75, 24, "大额出售可能增加供给压力")),
        (("buys bitcoin", "bitcoin reserve", "bitcoin treasury", "adopts bitcoin", "购买比特币", "比特币储备"),
         EventAssessment("adoption", .75, 1, .75, 72, "机构或政府采用可能改善中期需求预期")),
        (("maintenance", "migration", "upgrade", "维护", "迁移", "升级"),
         EventAssessment("operations", .45, 0, .7, 6, "计划性维护主要影响执行风险，方向不明确")),
        (("price prediction", "analyst predicts", "analysts predict", "price forecast", "could reach", "price analysis", "价格预测", "分析师预计"),
         EventAssessment("opinion", .2, 0, .5, 3, "观点或价格预测不直接作为方向证据")),
    ]
    for terms, result in rules:
        if any(term in t for term in terms):
            return result
    return EventAssessment("general", .2, 0, .45, 12, "未识别到可审计的明确价格方向事件")


def _relevance(title: str) -> float:
    if _contains(title, BTC_WORDS):
        return 1.0
    if _contains(title, MACRO_WORDS):
        return .75
    return 0.0


def _raw_item(source: str, title: str, url: str, published, observed) -> dict | None:
    title = re.sub(r"\s+", " ", (title or "").strip())[:500]
    link = canonical_url(url)
    published_dt, observed_dt = _dt(published), _dt(observed)
    if not title or not link or not published_dt or not observed_dt:
        return None
    relevance = _relevance(title)
    if relevance <= 0:
        return None
    return {
        "id": hashlib.sha256(f"{source}|{link}|{published_dt.isoformat()}".encode()).hexdigest()[:20],
        "source": source,
        "title": title,
        "url": link,
        "publishedAt": _iso(published_dt),
        "observedAt": _iso(observed_dt),
        "relevance": relevance,
    }


def parse_rss(source: str, payload: bytes | str, observed_at=None) -> list[dict]:
    observed_at = _dt(observed_at) or _utc_now()
    raw = payload.encode() if isinstance(payload, str) else payload
    if len(raw) > 2_000_000:
        raise ValueError("RSS response exceeds 2 MB")
    root = ElementTree.fromstring(raw)
    items = []
    for node in root.findall(".//item")[:100]:
        def value(name):
            child = node.find(name)
            return (child.text or "").strip() if child is not None else ""
        item = _raw_item(source, value("title"), value("link") or value("guid"),
                         value("pubDate") or value("{http://purl.org/dc/elements/1.1/}date"), observed_at)
        if item:
            items.append(item)
    return items


def parse_okx(payload: dict, observed_at=None) -> list[dict]:
    observed_at = _dt(observed_at) or _utc_now()
    if str(payload.get("code")) != "0":
        raise ValueError(payload.get("msg") or "OKX announcement response failed")
    rows = []
    for block in payload.get("data") or []:
        rows.extend(block.get("details") or [])
    items = []
    for row in rows[:100]:
        item = _raw_item("okx", row.get("title", ""), row.get("url", ""),
                         row.get("pTime") or row.get("businessPTime"), observed_at)
        if item:
            items.append(item)
    return items


def _cluster(items: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for item in sorted(items, key=lambda x: (_dt(x["publishedAt"]), x["source"])):
        target = None
        for group in groups:
            if any(item["url"] == other["url"] for other in group):
                target = group
                break
            similarities = []
            for other in group:
                same_source = item["source"] == other["source"]
                hours_apart = abs((_dt(item["publishedAt"]) - _dt(other["publishedAt"])).total_seconds()) / 3600
                if hours_apart > (SAME_SOURCE_CLUSTER_HOURS if same_source else CROSS_SOURCE_CLUSTER_HOURS):
                    continue
                left_assessment, right_assessment = _assess(item["title"]), _assess(other["title"])
                # Similar wording such as "inflow" and "outflow" must not allow the
                # higher-priority publisher to decide the direction of a mixed cluster.
                if (left_assessment.direction and right_assessment.direction and
                        left_assessment.direction != right_assessment.direction):
                    continue
                similarity = _similarity(item["title"], other["title"])
                overlap = len(_title_tokens(item["title"]) & _title_tokens(other["title"]))
                similarities.append((similarity, same_source, overlap))
            # Cross-publisher wording varies. Within one feed require a much closer match so recurring
            # but distinct events (for example separate treasury purchases) are not collapsed.
            if any(similarity >= (.85 if same_source else .50) or
                   (not same_source and similarity >= .35 and overlap >= 4)
                   for similarity, same_source, overlap in similarities):
                target = group
                break
        if target is None:
            groups.append([item])
        else:
            target.append(item)
    return groups


def _eligible_item(raw: object, cutoff_dt: datetime) -> dict | None:
    """Validate persisted/feed data so one corrupt legacy row cannot poison the module."""
    if not isinstance(raw, dict):
        return None
    published, observed = _dt(raw.get("publishedAt")), _dt(raw.get("observedAt"))
    if not published or not observed:
        return None
    start = cutoff_dt.timestamp() - NEWS_WINDOW_HOURS * 3600
    if not (start <= published.timestamp() <= cutoff_dt.timestamp()) or observed > cutoff_dt:
        return None
    title = re.sub(r"\s+", " ", str(raw.get("title") or "").strip())[:500]
    url = canonical_url(str(raw.get("url") or ""))
    source = str(raw.get("source") or "unknown").strip().lower()[:50]
    relevance = _relevance(title)
    if not title or not url or relevance <= 0:
        return None
    item_id = str(raw.get("id") or "").strip()
    if not item_id:
        item_id = hashlib.sha256(f"{source}|{url}|{published.isoformat()}".encode()).hexdigest()[:20]
    return {
        "id": item_id[:128], "source": source, "title": title, "url": url,
        "publishedAt": _iso(published), "observedAt": _iso(observed), "relevance": relevance,
    }


def _cluster_assessment(group: list[dict]) -> tuple[dict, EventAssessment]:
    assessed = [(item, _assess(item["title"])) for item in group]
    directions = {assessment.direction for _, assessment in assessed if assessment.direction}
    if len(directions) > 1:
        # This should normally be prevented by _cluster, but the neutral fallback keeps
        # the direction deterministic if a future rule change creates a mixed group.
        representative, strongest = max(
            assessed,
            key=lambda pair: (pair[1].severity * pair[1].confidence,
                              SOURCE_QUALITY.get(pair[0]["source"], .4),
                              -SOURCE_PRIORITY.get(pair[0]["source"], 99)),
        )
        return representative, EventAssessment(
            strongest.category, strongest.severity, 0, 0,
            min(assessment.half_life_hours for _, assessment in assessed),
            "跨来源方向表述冲突，已按中性处理",
        )
    return max(
        assessed,
        key=lambda pair: (pair[1].severity * pair[1].confidence,
                          SOURCE_QUALITY.get(pair[0]["source"], .4),
                          -SOURCE_PRIORITY.get(pair[0]["source"], 99)),
    )


def aggregate_news(items: list[dict], cutoff=None, source_status: list[dict] | None = None) -> dict:
    cutoff_dt = _dt(cutoff) or _utc_now()
    eligible = [item for raw in items if (item := _eligible_item(raw, cutoff_dt)) is not None]

    clusters = []
    total_impact = 0.0
    for group in _cluster(eligible):
        primary, assessment = _cluster_assessment(group)
        relevance = max(float(i["relevance"]) for i in group)
        quality = max(SOURCE_QUALITY.get(i["source"], .4) for i in group)
        sources = sorted({i["source"] for i in group}, key=lambda s: SOURCE_PRIORITY.get(s, 99))
        # One publisher is not corroboration. Two, three and four independent
        # publishers contribute approximately .50, .79 and 1.00 respectively.
        corroboration = 0.0 if len(sources) <= 1 else min(1.0, math.log(len(sources)) / math.log(4))
        severity_component = .4 * assessment.severity
        quality_component = .25 * quality
        corroboration_component = .2 * corroboration
        baseline_component = .15
        importance_score = round(100 * relevance * (
            severity_component + quality_component + corroboration_component + baseline_component
        ))
        importance_score = max(0, min(100, importance_score))
        if assessment.category == "opinion":
            importance_score = min(30, importance_score)
        elif assessment.category == "general" and assessment.direction == 0:
            importance_score = min(55, importance_score)
        importance = min(5, max(1, math.ceil(importance_score / 20)))
        # Anchor decay to the first publication. A later rewrite/reprint may add
        # corroboration, but it must not reset an old event's age.
        event_time = min(_dt(i["publishedAt"]) for i in group)
        latest_time = max(_dt(i["publishedAt"]) for i in group)
        age_hours = max(0.0, (cutoff_dt - event_time).total_seconds() / 3600)
        decay = math.exp(-math.log(2) * age_hours / assessment.half_life_hours)
        impact = assessment.direction * assessment.confidence * importance_score / 100 * decay
        total_impact += impact
        clusters.append({
            "clusterId": hashlib.sha256("|".join(sorted(i["id"] for i in group)).encode()).hexdigest()[:20],
            "title": primary["title"], "url": primary["url"], "source": primary["source"],
            "sources": sources, "sourceCount": len(sources), "publishedAt": _iso(event_time),
            "latestPublishedAt": _iso(latest_time),
            "observedAt": min(i["observedAt"] for i in group), "category": assessment.category,
            "importance": importance, "importanceScore": importance_score, "direction": assessment.direction,
            "directionConfidence": assessment.confidence, "relevance": relevance, "reason": assessment.reason,
            "ageHours": round(age_hours, 3), "halfLifeHours": assessment.half_life_hours,
            "timeDecay": round(decay, 4), "effectiveImpact": round(impact, 4), "duplicateCount": len(group),
            "weightBreakdown": {
                "relevance": round(relevance, 4), "severity": round(assessment.severity, 4),
                "sourceQuality": round(quality, 4), "corroboration": round(corroboration, 4),
                "directionConfidence": round(assessment.confidence, 4),
                "severityComponent": round(severity_component, 4),
                "qualityComponent": round(quality_component, 4),
                "corroborationComponent": round(corroboration_component, 4),
                "baselineComponent": baseline_component,
            },
            "weightFormula": "重要度=相关度×(40%事件严重度+25%来源质量+20%独立来源印证+15%基础项)；影响=方向×方向置信度×重要度×时效系数",
        })
    clusters.sort(key=lambda x: (abs(x["effectiveImpact"]), x["importanceScore"], x["publishedAt"]), reverse=True)

    statuses = [] if source_status is None else [dict(row) for row in source_status if isinstance(row, dict)]
    successes = sum(1 for row in statuses if row.get("ok"))
    if source_status is None:
        status, coverage = "complete", 1.0
    elif not statuses or not successes:
        status, coverage = "unavailable", 0.0
    else:
        coverage = successes / len(statuses)
        status = "complete" if successes == len(statuses) else "partial"
    adjusted_impact = total_impact * coverage
    score = max(-15, min(15, round(15 * max(-1.0, min(1.0, adjusted_impact))))) if status != "unavailable" else 0
    return {
        "asOf": _iso(cutoff_dt), "windowHours": NEWS_WINDOW_HOURS, "status": status, "newsScore": score,
        "rawImpact": round(total_impact, 4), "itemCount": len(eligible), "clusterCount": len(clusters),
        "sourceCoverage": round(coverage, 4), "coverageAdjustedImpact": round(adjusted_impact, 4),
        "clusters": clusters, "sourceStatus": statuses,
        "reason": "新闻源均不可用或状态缺失，新闻分不参与合成" if status == "unavailable" else "新闻分已按来源覆盖率修正并限制在 -15 到 +15，技术分析仍是主信号",
    }


class NewsAggregator:
    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 8.0):
        self.client = client
        self.timeout = timeout

    async def _one(self, client: httpx.AsyncClient, source: str) -> tuple[list[dict], dict]:
        try:
            headers = {"Accept": "application/json" if source == "okx" else "application/rss+xml, application/xml",
                       "Accept-Language": "zh-CN" if source == "okx" else "en-US",
                       "User-Agent": "okx-btc-advisor/1.0 (local educational analysis)"}
            expected_host=(urlsplit(SOURCES[source]).hostname or "").lower()
            content=bytearray()
            async with client.stream("GET",SOURCES[source],headers=headers,timeout=self.timeout,follow_redirects=True) as response:
                response.raise_for_status()
                if (response.url.host or "").lower()!=expected_host:
                    raise ValueError("news redirect left the configured source host")
                length=response.headers.get("content-length")
                if length and int(length)>2_000_000:
                    raise ValueError("response exceeds 2 MB")
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content)>2_000_000:
                        raise ValueError("response exceeds 2 MB")
            # Do not stamp an observation at request start: a slow response must
            # not become visible to a decision made while it was still in flight.
            # Parsing needs a fallback timestamp, then every retained article is
            # stamped again only after parsing has completed.
            parsing_fallback = _utc_now()
            parsed = (parse_okx(json.loads(content), parsing_fallback)
                      if source == "okx" else parse_rss(source, content, parsing_fallback))
            observed = _utc_now()
            observed_iso = _iso(observed)
            for item in parsed:
                item["observedAt"] = observed_iso
            return parsed, {"source": source, "ok": True, "itemCount": len(parsed), "observedAt": _iso(observed), "error": None}
        except (httpx.HTTPError, ValueError, ElementTree.ParseError) as exc:
            observed = _utc_now()
            return [], {"source": source, "ok": False, "itemCount": 0, "observedAt": _iso(observed),
                        "error": f"{type(exc).__name__}: {str(exc)[:160]}"}

    async def fetch(self, cutoff=None) -> dict:
        async def run(client):
            results = await asyncio.gather(*(self._one(client, source) for source in SOURCES))
            items = [item for batch, _ in results for item in batch]
            statuses = [status for _, status in results]
            # Persistence happens only after the complete multi-source batch is
            # available.  Use that commit barrier for every source so a fast
            # response cannot be back-dated across a candle close while a slow
            # sibling request is still keeping the batch out of the database.
            committed = _iso(_utc_now())
            for item in items:item["observedAt"]=committed
            for source_status in statuses:source_status["observedAt"]=committed
            # A normal live call uses a cutoff after observation. An explicit cutoff remains strict for audit/backtests.
            effective_cutoff = cutoff if cutoff is not None else _utc_now()
            result = aggregate_news(items, effective_cutoff, statuses)
            # Persist immutable source articles, never the dynamic clusters whose ids can
            # change as corroborating publishers enter or leave a feed window.
            result["rawItems"] = items
            return result
        if self.client is not None:
            return await run(self.client)
        async with httpx.AsyncClient() as client:
            return await run(client)
