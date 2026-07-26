"""
MockProvider — deterministic synthetic data so the app runs anywhere with no
network, no broker, no API key. Use it to develop/test the UI and metrics.
"""
from __future__ import annotations
import random
from typing import Dict, List
from .base import DataProvider, Quote


class MockProvider(DataProvider):
    name = "mock"

    def __init__(self, seed: int = 42, drift: float = 0.15):
        # drift > 0 biases the synthetic market slightly bullish; < 0 bearish.
        self._rng = random.Random(seed)
        self._drift = drift
        self._base: Dict[str, float] = {}

    def _price_for(self, symbol: str) -> float:
        if symbol not in self._base:
            self._base[symbol] = self._rng.uniform(40, 900)
        return self._base[symbol]

    def get_quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        out: Dict[str, Quote] = {}
        for s in symbols:
            prior = self._price_for(s)
            # daily move ~ N(drift, 1.3%)
            pct = self._rng.gauss(self._drift, 1.3) / 100.0
            last = prior * (1 + pct)
            vol = self._rng.uniform(1e5, 5e7)
            ma20 = prior * (1 + self._rng.gauss(0, 0.5) / 100.0)
            out[s] = Quote(symbol=s, last=round(last, 2),
                           prior_close=round(prior, 2), volume=round(vol),
                           ma20=round(ma20, 2))
        return out
