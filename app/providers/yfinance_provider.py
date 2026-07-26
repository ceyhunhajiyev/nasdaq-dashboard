"""
YFinanceProvider — free/delayed data via Yahoo Finance (unofficial).

Great for the prototype. NOT for production: it's unofficial, rate-limited, and
can break without notice. When phase 1 is validated, swap to a paid feed or MT5.
"""
from __future__ import annotations
from typing import Dict, List
from .base import DataProvider, Quote


class YFinanceProvider(DataProvider):
    name = "yfinance"

    def __init__(self, history_days: int = 40):
        self.history_days = history_days

    def get_quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        import yfinance as yf
        import pandas as pd

        out: Dict[str, Quote] = {}
        if not symbols:
            return out

        # One batched download for daily bars (fast, one HTTP round trip-ish).
        data = yf.download(
            tickers=" ".join(symbols),
            period=f"{self.history_days}d",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
        )

        for s in symbols:
            try:
                df = data[s] if len(symbols) > 1 else data
                df = df.dropna()
                if len(df) < 2:
                    continue
                last = float(df["Close"].iloc[-1])
                prior = float(df["Close"].iloc[-2])
                vol = float(df["Volume"].iloc[-1]) if "Volume" in df else None
                ma20 = float(df["Close"].tail(20).mean()) if len(df) >= 20 else None
                out[s] = Quote(symbol=s, last=last, prior_close=prior,
                               volume=vol, ma20=ma20)
            except Exception:
                # skip symbols Yahoo couldn't return; the metrics layer tolerates gaps
                continue
        return out
