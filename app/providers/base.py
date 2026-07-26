"""
Data-provider abstraction.

Everything the dashboard needs from a data source is defined here. Swap the
concrete provider (Mock / YFinance / MT5) in settings.py — the rest of the app
never changes.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


@dataclass
class Quote:
    symbol: str
    last: Optional[float]          # latest price
    prior_close: Optional[float]   # previous session close
    volume: Optional[float]        # session volume (may be None for some feeds)
    ma20: Optional[float] = None   # 20-bar moving average (optional context)

    @property
    def change(self) -> Optional[float]:
        if self.last is None or self.prior_close is None:
            return None
        return self.last - self.prior_close

    @property
    def change_pct(self) -> Optional[float]:
        if self.last is None or self.prior_close in (None, 0):
            return None
        return (self.last - self.prior_close) / self.prior_close * 100.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["change"] = self.change
        d["change_pct"] = self.change_pct
        return d


class DataProvider(ABC):
    """Implement these two methods for any data source."""

    name: str = "abstract"

    @abstractmethod
    def get_quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        """Return a Quote for each requested symbol (missing ones may be omitted)."""
        raise NotImplementedError

    def healthcheck(self) -> dict:
        return {"provider": self.name, "ok": True}
