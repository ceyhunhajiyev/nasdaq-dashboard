"""
News aggregator — pulls headlines from multiple financial RSS feeds relevant to
US indices (US100 / SP500 / US30), dedupes, sorts by recency, and flags likely
high-impact items with a keyword heuristic.

HONEST NOTES:
  • ForexFactory has no clean news RSS (only an economic calendar), so it's not
    here. Its trader audience is covered by ForexLive + FXStreet instead. A FF
    economic-calendar module can be added separately.
  • Investing.com publishes RSS but sometimes blocks automated requests; if a
    feed fails it's skipped gracefully (the others still load).
  • "High impact" is a KEYWORD heuristic (Fed/CPI/earnings/mega-cap names, etc.),
    not real market-impact scoring. Treat the flag as a hint, not a verdict.
  • RSS gives headline + link + time, not full articles.
"""
from __future__ import annotations
import time
import concurrent.futures
from dataclasses import dataclass, asdict
from typing import List, Dict

# source name -> RSS url
FEEDS: Dict[str, str] = {
    "CNBC Markets":  "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "MarketWatch":   "http://feeds.marketwatch.com/marketwatch/topstories/",
    "Investing.com": "https://www.investing.com/rss/news.rss",
    "Nasdaq":        "https://www.nasdaq.com/feed/rssoutbound?category=Markets",
    "Seeking Alpha": "https://seekingalpha.com/market_news/all.xml",
    "FXStreet":      "https://www.fxstreet.com/rss/news",
    "ForexLive":     "https://www.forexlive.com/feed/news",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
}

IMPACT_KEYWORDS = [
    "fed", "fomc", "powell", "rate", "rates", "hike", "cut", "inflation", "cpi",
    "ppi", "pce", "jobs", "payroll", "payrolls", "nfp", "unemployment", "jobless",
    "gdp", "recession", "treasury", "yield", "yields", "tariff", "earnings",
    "guidance", "downgrade", "upgrade", "selloff", "sell-off", "rally", "crash",
    "default", "debt ceiling", "opec", "oil", "war", "sanction",
    # index / mega-cap movers
    "nasdaq", "s&p", "dow", "nvidia", "apple", "microsoft", "tesla", "amazon",
    "meta", "google", "alphabet", "broadcom", "netflix",
]


@dataclass
class NewsItem:
    title: str
    source: str
    link: str
    published: float  # epoch seconds
    impact: bool

    def to_dict(self) -> dict:
        return asdict(self)


import re

_IMPACT_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in IMPACT_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def _is_impact(title: str) -> bool:
    return bool(_IMPACT_RE.search(title))


def parse_feed(source: str, raw_text: str) -> List[NewsItem]:
    """Parse RSS/Atom text into NewsItems. Pure/offline — unit-testable."""
    import feedparser
    d = feedparser.parse(raw_text)
    out: List[NewsItem] = []
    for e in d.entries:
        title = (getattr(e, "title", "") or "").strip()
        if not title:
            continue
        link = getattr(e, "link", "") or ""
        pub = None
        for attr in ("published_parsed", "updated_parsed"):
            tt = getattr(e, attr, None)
            if tt:
                try:
                    pub = time.mktime(tt)
                except Exception:
                    pub = None
                break
        if pub is None:
            pub = time.time()
        out.append(NewsItem(title=title, source=source, link=link,
                            published=pub, impact=_is_impact(title)))
    return out


class NewsAggregator:
    def __init__(self, feeds: Dict[str, str] = None, timeout: int = 6,
                 max_items: int = 60):
        self.feeds = feeds or FEEDS
        self.timeout = timeout
        self.max_items = max_items

    def fetch(self) -> List[NewsItem]:
        import httpx
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ndx-dashboard/1.0)"}

        def _one(src: str, url: str) -> List[NewsItem]:
            try:
                r = httpx.get(url, timeout=self.timeout, headers=headers,
                              follow_redirects=True)
                r.raise_for_status()
                return parse_feed(src, r.text)
            except Exception:
                return []  # skip a failing feed; others still load

        items: List[NewsItem] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(_one, s, u) for s, u in self.feeds.items()]
            for f in futures:
                items.extend(f.result())

        # dedupe by title, newest first
        seen = set()
        deduped: List[NewsItem] = []
        for it in sorted(items, key=lambda x: x.published, reverse=True):
            key = it.title.lower()[:90]
            if key in seen:
                continue
            seen.add(key)
            deduped.append(it)
        return deduped[: self.max_items]
