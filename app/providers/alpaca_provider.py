"""
AlpacaProvider — real US-equities data on Alpaca's FREE tier (IEX feed).

Why Alpaca for breadth: the snapshots endpoint returns latest price + today's
and yesterday's daily bar for MANY symbols in ONE request, so all ~100
constituents come back in a single (multi-symbol) call. A second bars call adds
the 20-day MA. Free tier rate limits (200 req/min) are far more than we need.

SETUP (free):
  1. Create a free account at alpaca.markets and generate API keys
     (paper-trading keys work fine for market data).
  2. Set environment variables before launching:
       ALPACA_KEY_ID, ALPACA_SECRET_KEY
  3. Run:  DASHBOARD_PROVIDER=alpaca uvicorn app.main:app

Notes:
  • Free data is the IEX feed (one exchange) — excellent for breadth direction,
    though prints/volume are IEX-only, not full-market consolidated.
  • "last" is the latest trade during RTH; when the market is closed it falls
    back to the last daily close. Day-change is always last vs prevDailyBar.
"""
from __future__ import annotations
import os
import datetime as dt
from typing import Dict, List, Optional
from .base import DataProvider, Quote


class AlpacaProvider(DataProvider):
    name = "alpaca"
    BASE = "https://data.alpaca.markets"

    def __init__(self, key_id: Optional[str] = None, secret: Optional[str] = None,
                 feed: str = "iex", chunk: int = 100, add_ma: bool = True):
        self.key_id = key_id or os.getenv("ALPACA_KEY_ID")
        self.secret = secret or os.getenv("ALPACA_SECRET_KEY")
        self.feed = feed
        self.chunk = chunk
        self.add_ma = add_ma
        if not (self.key_id and self.secret):
            raise RuntimeError(
                "Alpaca keys missing. Set ALPACA_KEY_ID and ALPACA_SECRET_KEY "
                "(free at alpaca.markets), then run with DASHBOARD_PROVIDER=alpaca."
            )
        self._headers = {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret,
            "accept": "application/json",
        }

    def _chunks(self, xs: List[str]):
        for i in range(0, len(xs), self.chunk):
            yield xs[i:i + self.chunk]

    # ---- parsing (kept static so it's unit-testable without network) --------
    @staticmethod
    def parse_snapshot(sym: str, snap: dict) -> Optional[Quote]:
        if not isinstance(snap, dict):
            return None
        lt = snap.get("latestTrade") or {}
        db = snap.get("dailyBar") or {}
        pb = snap.get("prevDailyBar") or {}
        last = lt.get("p")
        if last is None:
            last = db.get("c")
        prior = pb.get("c")
        vol = db.get("v")
        if last is None or prior is None:
            return None
        return Quote(symbol=sym, last=float(last), prior_close=float(prior),
                     volume=float(vol) if vol is not None else None)

    @staticmethod
    def parse_snapshots_payload(data: dict) -> Dict[str, Quote]:
        # Alpaca returns a flat {symbol: snapshot} object; some versions wrap it.
        snaps = data.get("snapshots", data) if isinstance(data, dict) else {}
        out: Dict[str, Quote] = {}
        for sym, snap in snaps.items():
            q = AlpacaProvider.parse_snapshot(sym, snap)
            if q:
                out[sym] = q
        return out

    @staticmethod
    def apply_ma_from_bars(bars_by_symbol: dict, quotes: Dict[str, Quote]) -> None:
        for sym, arr in (bars_by_symbol or {}).items():
            closes = [b["c"] for b in arr if isinstance(b, dict) and "c" in b]
            if sym in quotes and closes:
                quotes[sym].ma20 = sum(closes[-20:]) / min(20, len(closes))

    # ---- network ------------------------------------------------------------
    def get_quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        import httpx
        out: Dict[str, Quote] = {}
        with httpx.Client(timeout=20, headers=self._headers) as client:
            for grp in self._chunks(symbols):
                r = client.get(
                    f"{self.BASE}/v2/stocks/snapshots",
                    params={"symbols": ",".join(grp), "feed": self.feed},
                )
                r.raise_for_status()
                out.update(self.parse_snapshots_payload(r.json()))

            if self.add_ma:
                start = (dt.date.today() - dt.timedelta(days=45)).isoformat()
                for grp in self._chunks(symbols):
                    try:
                        r = client.get(
                            f"{self.BASE}/v2/stocks/bars",
                            params={"symbols": ",".join(grp), "timeframe": "1Day",
                                    "start": start, "limit": 10000, "feed": self.feed},
                        )
                        r.raise_for_status()
                        self.apply_ma_from_bars(r.json().get("bars", {}), out)
                    except Exception:
                        continue  # MA is optional context; breadth still works
        return out

    def healthcheck(self) -> dict:
        try:
            import httpx
            with httpx.Client(timeout=10, headers=self._headers) as client:
                r = client.get(f"{self.BASE}/v2/stocks/snapshots",
                               params={"symbols": "AAPL", "feed": self.feed})
                return {"provider": self.name, "ok": r.status_code == 200,
                        "status": r.status_code, "feed": self.feed}
        except Exception as e:
            return {"provider": self.name, "ok": False, "error": str(e)}
