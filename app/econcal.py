"""
Economic calendar — ForexFactory-style events with impact levels and
actual vs forecast, via ForexFactory's data-partner JSON feed.

Feed: https://nfs.faireconomy.media/ff_calendar_thisweek.json
Fields per event: title, country, date (ISO), impact (High/Medium/Low),
forecast, previous, actual (actual is empty until the event releases).

Impact -> colour, exactly like ForexFactory: High=red, Medium=orange, Low=yellow.
Once 'actual' is present we flag beat / miss / inline vs forecast. We do NOT
judge whether that's bullish or bearish for price — that depends on the
indicator (e.g. hot CPI is bad for stocks, strong jobs is mixed), and pretending
otherwise would mislead. The numbers are shown; interpretation is yours.

NOT financial advice.
"""
from __future__ import annotations
from typing import List, Optional

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Currencies most relevant to US indices (US100/US30/SP500) and metals (XAU/XAG).
DEFAULT_CCYS = ["USD", "EUR", "CNY", "GBP", "JPY"]

IMPACT_COLOR = {"High": "red", "Medium": "orange", "Low": "yellow", "Holiday": "grey"}


def _num(x) -> Optional[float]:
    if x in (None, "", "-"):
        return None
    try:
        return float(str(x).replace("%", "").replace(",", "").replace("K", "e3").replace("M", "e6").replace("B", "e9"))
    except Exception:
        return None


def parse_events(raw_json: list) -> List[dict]:
    """Normalize the FF feed. Pure/offline — unit-testable."""
    out = []
    for e in raw_json or []:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        impact = e.get("impact") or "Low"
        forecast = e.get("forecast")
        previous = e.get("previous")
        actual = e.get("actual")
        beat = None
        f, a = _num(forecast), _num(actual)
        if a is not None and f is not None:
            beat = "beat" if a > f else "miss" if a < f else "inline"
        out.append({
            "time": e.get("date"),                 # ISO 8601 with tz
            "currency": e.get("country") or "",     # feed uses ISO ccy in 'country'
            "title": title,
            "impact": impact,
            "color": IMPACT_COLOR.get(impact, "yellow"),
            "forecast": forecast if forecast not in (None, "") else None,
            "previous": previous if previous not in (None, "") else None,
            "actual": actual if actual not in (None, "") else None,
            "released": actual not in (None, ""),
            "beat": beat,   # beat / miss / inline / None (not yet released)
        })
    return out


def build_calendar(currencies: List[str] = None, min_impact: str = "Low",
                   timeout: int = 10) -> dict:
    import httpx
    ccys = currencies or DEFAULT_CCYS
    rank = {"Low": 0, "Medium": 1, "High": 2, "Holiday": 0}
    floor = rank.get(min_impact, 0)
    try:
        r = httpx.get(FEED_URL, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                                             "Chrome/124.0 Safari/537.36",
                               "Accept": "application/json, text/plain, */*",
                               "Referer": "https://www.forexfactory.com/"})
        r.raise_for_status()
        events = parse_events(r.json())
    except Exception as e:
        return {"events": [], "error": str(e), "currencies": ccys}

    filtered = [ev for ev in events
                if ev["currency"] in ccys and rank.get(ev["impact"], 0) >= floor]
    # sort by time (ISO strings sort chronologically)
    filtered.sort(key=lambda ev: ev["time"] or "")
    return {"events": filtered, "count": len(filtered), "currencies": ccys}
