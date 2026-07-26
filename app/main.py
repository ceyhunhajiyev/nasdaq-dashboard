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


# --- news -------------------------------------------------------------------
_news_agg = NewsAggregator()
_news_cache = {"ts": 0.0, "data": None}


@app.get("/api/news")
def news(refresh: bool = False, high_impact: bool = False):
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
    return {"items": data, "count": len(data),
            "sources": list(_news_agg.feeds.keys()),
            "timestamp": _news_cache["ts"]}


# --- frontend ---------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
