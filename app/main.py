"""
FastAPI backend + static frontend server.

Run:  uvicorn app.main:app --reload
Open: http://localhost:8000
"""
from __future__ import annotations
import time
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import settings, metrics
from .constituents import load_constituents, INDEX_PROXY
from .news import NewsAggregator
from . import analysis
from . import metals as metals_mod
from . import signals as signals_mod
from . import econcal

app = FastAPI(title="Nasdaq-100 Internals Dashboard")

SYMBOLS, WEIGHTS = load_constituents()
PROVIDER = settings.make_provider()

_STATIC = Path(__file__).resolve().parent.parent / "static"

# --- tiny in-memory cache so we don't hammer the data feed ------------------
_cache = {"ts": 0.0, "data": None}


def _get_snapshot(force: bool = False) -> dict:
    now = time.time()
    if not force and _cache["data"] and now - _cache["ts"] < settings.CACHE_TTL:
        return _cache["data"]

    quotes = PROVIDER.get_quotes(SYMBOLS + [INDEX_PROXY])
    index_quote = quotes.pop(INDEX_PROXY, None)
    dash = metrics.build_dashboard(quotes, WEIGHTS, index_quote)

    # transparent, rules-based US100 bias (decision-support, not a trade call)
    try:
        closes = analysis.get_index_closes(PROVIDER, INDEX_PROXY, 400)
    except Exception:
        closes = []
    dash["signal"] = analysis.compute_index_bias(
        closes, dash["breadth"], dash["divergence"],
        last=index_quote.last if index_quote else None, symbol="US100")

    dash["meta"] = {
        "provider": PROVIDER.name,
        "constituents_requested": len(SYMBOLS),
        "constituents_returned": dash["breadth"].get("count", 0),
        "index_proxy": INDEX_PROXY,
        "timestamp": now,
        "cache_ttl": settings.CACHE_TTL,
    }
    _cache["data"] = dash
    _cache["ts"] = now
    return dash


# --- API --------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", **PROVIDER.healthcheck()}


@app.get("/api/dashboard")
def dashboard(refresh: bool = False):
    return _get_snapshot(force=refresh)


@app.get("/api/breadth")
def breadth(refresh: bool = False):
    return _get_snapshot(force=refresh)["breadth"]


@app.get("/api/contribution")
def contribution(refresh: bool = False):
    return _get_snapshot(force=refresh)["contribution"]


@app.get("/api/divergence")
def divergence(refresh: bool = False):
    return _get_snapshot(force=refresh)["divergence"]


@app.get("/api/signal")
def signal(refresh: bool = False):
    return _get_snapshot(force=refresh)["signal"]


# --- AI analyst note (on-demand; cached; needs ANTHROPIC_API_KEY) -----------
_ai_cache = {"ts": 0.0, "data": None}


@app.get("/api/ai_note")
def ai_note(refresh: bool = False):
    now = time.time()
    if refresh or not _ai_cache["data"] or now - _ai_cache["ts"] > 300:
        snap = _get_snapshot()
        compact = {
            "instrument": "US100",
            "index": snap.get("index"),
            "bias": snap.get("signal"),
            "breadth": snap.get("breadth"),
            "divergence": snap.get("divergence"),
            "estimated_index_change_pct": snap.get("contribution", {}).get("estimated_index_change_pct"),
        }
        _ai_cache["data"] = analysis.generate_ai_note(compact)
        _ai_cache["ts"] = now
    return _ai_cache["data"]


# --- news -------------------------------------------------------------------
_news_agg = NewsAggregator()
_news_cache = {"ts": 0.0, "data": None}


@app.get("/api/news")
def news(refresh: bool = False, high_impact: bool = False, relevant_only: bool = False):
    now = time.time()
    if refresh or not _news_cache["data"] or now - _news_cache["ts"] > settings.NEWS_TTL:
        try:
            items = _news_agg.fetch()
            _news_cache["data"] = [i.to_dict() for i in items]
            _news_cache["ts"] = now
        except Exception as e:
            return {"items": [], "error": str(e),
                    "sources": list(_news_agg.feeds.keys())}
    data = _news_cache["data"] or []
    if high_impact:
        data = [d for d in data if d.get("impact")]
    if relevant_only:
        data = [d for d in data if d.get("relevant")]
    # coarse lean tally across the relevant set — CONTEXT ONLY, not folded into bias
    rel = [d for d in data if d.get("relevant")]
    lean = {"bullish": sum(1 for d in rel if d.get("lean") == "bullish"),
            "bearish": sum(1 for d in rel if d.get("lean") == "bearish"),
            "neutral": sum(1 for d in rel if d.get("lean") == "neutral")}
    return {"items": data, "count": len(data), "lean": lean,
            "sources": list(_news_agg.feeds.keys()),
            "timestamp": _news_cache["ts"]}


# --- metals + SMT (own page; own data source) -------------------------------
_metals_cache = {"ts": 0.0, "data": None}


@app.get("/api/metals")
def metals(refresh: bool = False):
    now = time.time()
    if refresh or not _metals_cache["data"] or now - _metals_cache["ts"] > 60:
        try:
            _metals_cache["data"] = metals_mod.build_metals()
            _metals_cache["ts"] = now
        except Exception as e:
            return {"error": str(e), "metals": {}, "smt": {}}
    out = dict(_metals_cache["data"])
    out["timestamp"] = _metals_cache["ts"]
    return out


@app.get("/metals")
def metals_page():
    return FileResponse(_STATIC / "metals.html")


# --- signals (custom chart w/ entry/SL/TP) ----------------------------------
_sig_cache: dict = {}


@app.get("/api/signals")
def api_signals(instrument: str = "US100", tf: str = "1h", refresh: bool = False):
    key = f"{instrument.upper()}:{tf}"
    entry = _sig_cache.get(key)
    now = time.time()
    if refresh or not entry or now - entry["ts"] > 30:
        try:
            data = signals_mod.build_signals(instrument.upper(), PROVIDER, tf=tf)
        except Exception as e:
            return {"error": str(e), "instrument": instrument.upper(), "tf": tf, "ohlc": [], "signal": {}}
        _sig_cache[key] = {"ts": now, "data": data}
    out = dict(_sig_cache[key]["data"])
    out["timestamp"] = _sig_cache[key]["ts"]
    return out


@app.get("/signals")
def signals_page():
    return FileResponse(_STATIC / "signals.html")


# --- economic calendar (ForexFactory-style) ---------------------------------
_cal_cache = {"ts": 0.0, "data": None}


@app.get("/api/calendar")
def api_calendar(min_impact: str = "Medium", refresh: bool = False):
    now = time.time()
    if refresh or not _cal_cache["data"] or now - _cal_cache["ts"] > 300:
        _cal_cache["data"] = econcal.build_calendar(min_impact=min_impact)
        _cal_cache["ts"] = now
    out = dict(_cal_cache["data"])
    out["timestamp"] = _cal_cache["ts"]
    return out


# --- frontend ---------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
