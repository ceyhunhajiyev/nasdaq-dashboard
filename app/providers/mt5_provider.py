"""
MT5Provider — your XM feed via MetaTrader 5 (phase 2).

REQUIREMENTS (important):
  • Windows, with the MetaTrader 5 terminal installed and LOGGED IN to XM.
  • pip install MetaTrader5   (Windows-only package; will NOT install on Linux/Mac)
  • The terminal must be running while this app runs.

SYMBOL NAMES: XM/MT5 names differ from Yahoo. e.g. Apple may be "AAPL" or
"AAPL.US" or similar depending on the broker's symbol set. Fill in SYMBOL_MAP
below (Yahoo ticker -> MT5 symbol) for anything that doesn't match 1:1. Symbols
not present in your XM account will simply be skipped.

The import is lazy (inside methods) so the rest of the app runs fine without
MetaTrader5 installed.
"""
from __future__ import annotations
from typing import Dict, List
from .base import DataProvider, Quote

# Yahoo ticker -> MT5/XM symbol. Add entries as needed for your broker.
SYMBOL_MAP: Dict[str, str] = {
    # "AAPL": "AAPL.US",
    # "MSFT": "MSFT.US",
}


class MT5Provider(DataProvider):
    name = "mt5"

    def __init__(self, login: int | None = None, password: str | None = None,
                 server: str | None = None, path: str | None = None):
        # If your terminal is already logged in, you can leave these None and
        # just call mt5.initialize().
        self.login = login
        self.password = password
        self.server = server
        self.path = path
        self._mt5 = None

    def _ensure(self):
        if self._mt5 is not None:
            return self._mt5
        try:
            import MetaTrader5 as mt5  # Windows-only
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "MetaTrader5 package not available. Install it on Windows with "
                "the MT5 terminal running (pip install MetaTrader5)."
            ) from e

        kwargs = {}
        if self.path:
            kwargs["path"] = self.path
        if self.login and self.password and self.server:
            kwargs.update(login=self.login, password=self.password, server=self.server)

        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"mt5.initialize() failed: {mt5.last_error()}")
        self._mt5 = mt5
        return mt5

    def _resolve(self, yahoo_symbol: str) -> str:
        return SYMBOL_MAP.get(yahoo_symbol, yahoo_symbol)

    def get_quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        mt5 = self._ensure()
        out: Dict[str, Quote] = {}
        for s in symbols:
            mt5_sym = self._resolve(s)
            try:
                if not mt5.symbol_select(mt5_sym, True):
                    continue
                tick = mt5.symbol_info_tick(mt5_sym)
                # daily bars for prior close + MA20
                rates = mt5.copy_rates_from_pos(mt5_sym, mt5.TIMEFRAME_D1, 0, 25)
                if tick is None or rates is None or len(rates) < 2:
                    continue
                last = float(tick.bid or tick.last or rates[-1]["close"])
                prior_close = float(rates[-2]["close"])
                closes = [float(r["close"]) for r in rates]
                ma20 = sum(closes[-20:]) / min(20, len(closes))
                vol = float(rates[-1]["tick_volume"]) if "tick_volume" in rates.dtype.names else None
                out[s] = Quote(symbol=s, last=last, prior_close=prior_close,
                               volume=vol, ma20=ma20)
            except Exception:
                continue
        return out

    def healthcheck(self) -> dict:
        try:
            mt5 = self._ensure()
            info = mt5.terminal_info()
            return {"provider": self.name, "ok": bool(info),
                    "connected": bool(info and info.connected)}
        except Exception as e:
            return {"provider": self.name, "ok": False, "error": str(e)}
