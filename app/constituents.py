"""
Nasdaq-100 constituents with APPROXIMATE index weights.

IMPORTANT: index membership and weights change frequently. These are a static
snapshot for prototyping. Update WEIGHTS periodically (or wire a live source in
phase 2). Contribution analysis is only as accurate as these weights.

Yahoo tickers are used here. For MT5/XM the symbol names differ (e.g. "AAPL.US"
or broker-specific suffixes) — map them in providers/mt5_provider.py.
"""

# Approximate weights (%) for the largest constituents. The tail gets a small
# default weight. Everything is normalised at load time so it sums to 1.0.
WEIGHTS = {
    "AAPL": 9.0, "MSFT": 8.0, "NVDA": 8.0, "AMZN": 5.5, "AVGO": 4.5,
    "META": 4.5, "TSLA": 3.0, "GOOGL": 2.6, "GOOG": 2.5, "COST": 2.6,
    "NFLX": 2.5, "TMUS": 1.6, "ADBE": 1.4, "PEP": 1.4, "CSCO": 1.3,
    "LIN": 1.3, "AMD": 1.5, "INTU": 1.3, "TXN": 1.2, "QCOM": 1.1,
    "ISRG": 1.2, "AMGN": 1.1, "BKNG": 1.2, "HON": 1.0, "CMCSA": 1.0,
    "AMAT": 1.0, "VRTX": 0.9, "ADP": 0.9, "PANW": 0.9, "MU": 0.9,
    "ADI": 0.8, "GILD": 0.8, "REGN": 0.7, "SBUX": 0.7, "LRCX": 0.8,
    "MDLZ": 0.7, "KLAC": 0.7, "SNPS": 0.7, "CDNS": 0.7, "MELI": 0.7,
    "CRWD": 0.7, "MAR": 0.6, "ABNB": 0.6, "CTAS": 0.6, "ORLY": 0.6,
    "CEG": 0.6, "NXPI": 0.5, "PDD": 0.6, "ASML": 0.6, "FTNT": 0.5,
    "PLTR": 1.0, "APP": 0.8, "MRVL": 0.6,
}

# Remaining constituents (get the default tail weight).
TAIL = [
    "DASH", "WDAY", "TTD", "ROP", "MNST", "ADSK", "PCAR", "CPRT", "PAYX",
    "KDP", "ROST", "ODFL", "CHTR", "PYPL", "AEP", "FANG", "FAST", "EXC",
    "DDOG", "CSGP", "GEHC", "KHC", "VRSK", "CTSH", "BKR", "XEL", "IDXX",
    "TTWO", "ANSS", "ON", "CDW", "MCHP", "DXCM", "GFS", "BIIB", "ZS",
    "TEAM", "ILMN", "WBD", "MDB", "ARM", "LULU",
    "AXON", "CSX", "MSTR", "CCEP",
]

DEFAULT_TAIL_WEIGHT = 0.25  # % each, before normalisation


def load_constituents():
    """Return (symbols: list[str], weights: dict[str, float] normalised to 1.0)."""
    raw = dict(WEIGHTS)
    for t in TAIL:
        raw.setdefault(t, DEFAULT_TAIL_WEIGHT)
    total = sum(raw.values())
    weights = {k: v / total for k, v in raw.items()}
    symbols = list(weights.keys())
    return symbols, weights


# The index proxy symbol (Yahoo). US100/NDX cash index isn't always free on
# Yahoo; QQQ (the ETF) is a reliable, free stand-in for the prototype.
INDEX_PROXY = "QQQ"
