# NDX Internals — Nasdaq-100 market-internals dashboard

A local web app that shows what's happening *inside* the Nasdaq-100: breadth
(how broadly stocks are participating), contribution (which mega-caps are moving
the index), and divergence (is the index move real or narrow?). Built so the
data feed is swappable — prototype on free data now, plug in your XM/MT5 feed later.

> Analytical context only — **not a trade signal and not financial advice.**
> Breadth confirms or questions a move; it does not predict one.

---

## Quick start (2 commands)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open **http://localhost:8000**. On first run it uses the built-in **mock
feed** (synthetic data) so you can see the whole thing work with no network,
no account, no keys.

---

## Switching data feeds — the one place you change

Set the `DASHBOARD_PROVIDER` environment variable (or edit `app/settings.py`):

| Phase | Provider | Command | Notes |
|------|----------|---------|-------|
| test | `mock` | `uvicorn app.main:app` | synthetic, works anywhere |
| 1 | `alpaca` | `DASHBOARD_PROVIDER=alpaca uvicorn app.main:app` | **real equities, free IEX feed (recommended)** |
| 1 | `yfinance` | `DASHBOARD_PROVIDER=yfinance uvicorn app.main:app` | free/delayed, quick but unofficial/flaky |
| 2 | `mt5` | `DASHBOARD_PROVIDER=mt5 uvicorn app.main:app` | your XM feed (Windows) |

On Windows PowerShell: `$env:DASHBOARD_PROVIDER="alpaca"; uvicorn app.main:app`

Nothing else in the app changes — the metrics, API, and UI are feed-agnostic.

### Phase 1 (recommended): Alpaca — real breadth, free
Best coverage of all ~100 constituents in one call.
1. Create a free account at **alpaca.markets** and generate API keys
   (paper-trading keys work fine for market data).
2. Set env vars, then run:
   ```bash
   export ALPACA_KEY_ID=your_key
   export ALPACA_SECRET_KEY=your_secret
   DASHBOARD_PROVIDER=alpaca uvicorn app.main:app
   ```
   PowerShell: `$env:ALPACA_KEY_ID="..."; $env:ALPACA_SECRET_KEY="..."`
3. Free data is the **IEX feed** (one exchange): great for breadth *direction*;
   prints/volume are IEX-only, not full-market consolidated. For consolidated
   (SIP) data, upgrade the Alpaca plan and set `feed="sip"` in the provider.

### Phase 2: XM via MetaTrader 5
1. Windows only. Install the MT5 terminal and **log in to your XM account**.
2. `pip install MetaTrader5` (Windows-only package).
3. Keep the terminal **running**.
4. XM symbol names differ from Yahoo (e.g. `AAPL.US`). Fill in `SYMBOL_MAP` in
   `app/providers/mt5_provider.py` for any that don't match.
5. Note: XM offers your traded instruments (US100, XAUUSD, etc.) but may not
   carry every individual Nasdaq constituent — names it doesn't have are skipped.
   For full breadth you may still want an equities data API alongside MT5.

---

## What each panel means

- **Breadth verdict** — a 0–100 blend of % advancing, % above 20-day MA, and
  up-volume share. A quick read of participation. Not a signal.
- **Divergence** — compares the index's direction to internal breadth. "Index up
  but breadth weak" = narrow rally carried by a few mega-caps (bearish tell);
  "index down but breadth positive" = concentrated selling (bullish tell).
- **Participation ribbon** — advancers vs decliners at a glance.
- **Tug-of-war** — each name's `weight × %change`, i.e. how much it's actually
  pushing the cap-weighted index. Leaders pull up, laggards drag down.

---

## API endpoints

- `GET /api/dashboard` — everything (the UI uses this)
- `GET /api/breadth`, `GET /api/contribution`, `GET /api/divergence`
- `GET /api/health`
- add `?refresh=true` to bypass the cache

---

## Project layout

```
app/
  main.py            FastAPI app, routes, cache, static serving
  settings.py        <-- swap the data feed here
  constituents.py    Nasdaq-100 list + approx weights (update periodically)
  metrics.py         breadth / contribution / divergence / bias
  providers/
    base.py            DataProvider interface + Quote
    mock_provider.py   synthetic (default)
    alpaca_provider.py     real equities, free IEX feed (phase 1, recommended)
    yfinance_provider.py   free/delayed (phase 1, quick)
    mt5_provider.py        XM via MetaTrader 5 (phase 2)
static/
  index.html         single-page dashboard
```

---

## Known limits (by design, so you build with eyes open)
- Constituent list & weights are a **static snapshot** — refresh them over time.
- `yfinance` is unofficial/rate-limited — fine to prototype, not for production.
- Divergence/bias are **heuristics for context**, deliberately simple. Tune the
  thresholds in `metrics.py` to your own model.
