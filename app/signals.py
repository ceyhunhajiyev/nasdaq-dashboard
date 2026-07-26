"""
Signal engine — transparent, rules-based entry / SL / TP on 1-hour bars.

THE RULE (long; short mirrors):
  Price sweeps a recent swing low (prints a low below it) and then CLOSES back
  above it on the same 1H bar — a liquidity grab / stop-run reversal. That bar's
  close is the entry, the stop sits just beyond the swept extreme, and targets
  are R multiples of that risk.

This is the core of your sweep-reclaim / turtle-soup model, made mechanical so it
produces concrete levels. It is DECISION-SUPPORT you interpret, NOT a validated
edge and NOT financial advice — the backtest is what tells you if it pays.

Data: US100 via Alpaca (QQQ 1H), metals via Yahoo (GC/SI/HG 1H). Mock provider
gets synthetic bars so the page works with no keys.
"""
from __future__ import annotations
import datetime as dt
from typing import List, Dict

# instrument -> Yahoo symbol at the SCALE YOU TRADE (index futures, not ETFs)
DATA_MAP = {
    "US100": "NQ=F",  # Nasdaq-100 futures (~ matches US100 index scale)
    "SP500": "ES=F",  # S&P 500 futures
    "US30":  "YM=F",  # Dow futures
    "XAU":   "GC=F",  # Gold
    "XAG":   "SI=F",  # Silver
    "XCU":   "HG=F",  # Copper
}


# ============================ OHLC SOURCES =================================
def _alpaca_ohlc(provider, symbol: str, bars: int) -> List[dict]:
    import httpx
    start = (dt.datetime.utcnow() - dt.timedelta(days=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with httpx.Client(timeout=15, headers=provider._headers) as c:
        r = c.get(f"{provider.BASE}/v2/stocks/bars",
                  params={"symbols": symbol, "timeframe": "1Hour",
                          "start": start, "limit": 10000, "feed": provider.feed})
        r.raise_for_status()
        arr = r.json().get("bars", {}).get(symbol, [])
    out = []
    for b in arr[-bars:]:
        ts = int(dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp())
        out.append({"time": ts, "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"]})
    return out


def _yf_ohlc(symbol: str, bars: int) -> List[dict]:
    import yfinance as yf
    df = yf.download(symbol, period="30d", interval="1h", progress=False, auto_adjust=False)
    if df is None or df.empty:
        return []
    out = []
    for idx, row in df.tail(bars).iterrows():
        try:
            ts = int(idx.timestamp())
            out.append({"time": ts, "open": float(row["Open"]), "high": float(row["High"]),
                        "low": float(row["Low"]), "close": float(row["Close"])})
        except Exception:
            continue
    return out


def _synth_ohlc(symbol: str, bars: int = 150) -> List[dict]:
    import random, time
    rng = random.Random(abs(hash(symbol)) % (2**32))
    price = rng.uniform(300, 600)
    now = int(time.time())
    out = []
    for i in range(bars):
        o = price
        price *= (1 + rng.gauss(0.0002, 0.006))
        c = price
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.002)))
        l = min(o, c) * (1 - abs(rng.gauss(0, 0.002)))
        out.append({"time": now - (bars - i) * 3600, "open": round(o, 2),
                    "high": round(h, 2), "low": round(l, 2), "close": round(c, 2)})
    return out


def get_ohlc(instrument: str, provider, bars: int = 150) -> List[dict]:
    sym = DATA_MAP.get(instrument)
    if not sym:
        return []
    try:
        if getattr(provider, "name", "") == "mock":
            return _synth_ohlc(sym, bars)
        return _yf_ohlc(sym, bars)
    except Exception:
        return []


# ============================ SIGNAL LOGIC =================================
def _atr(ohlc: List[dict], n: int = 14) -> float:
    if len(ohlc) < n + 1:
        return 0.0
    trs = []
    for i in range(1, len(ohlc)):
        h, l, pc = ohlc[i]["high"], ohlc[i]["low"], ohlc[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n


def detect_signals(ohlc: List[dict], lookback: int = 20, buffer_atr: float = 0.1,
                   rr2: float = 2.0) -> dict:
    n = len(ohlc)
    if n < lookback + 3:
        return {"last": None, "fresh": False, "count": 0}
    atr = _atr(ohlc, 14)
    buf = atr * buffer_atr
    sigs = []
    for i in range(lookback + 1, n):
        prior_low = min(ohlc[j]["low"] for j in range(i - lookback, i))
        prior_high = max(ohlc[j]["high"] for j in range(i - lookback, i))
        bar = ohlc[i]
        if bar["low"] < prior_low and bar["close"] > prior_low:
            entry = bar["close"]
            stop = min(bar["low"], prior_low) - buf
            risk = entry - stop
            if risk > 0:
                sigs.append({"i": i, "time": bar["time"], "signal": "long",
                             "entry": round(entry, 4), "sl": round(stop, 4),
                             "tp1": round(entry + risk, 4), "tp2": round(entry + risk * rr2, 4),
                             "rr": round(rr2, 2),
                             "reason": f"Swept swing low {prior_low:.2f} and reclaimed"})
        elif bar["high"] > prior_high and bar["close"] < prior_high:
            entry = bar["close"]
            stop = max(bar["high"], prior_high) + buf
            risk = stop - entry
            if risk > 0:
                sigs.append({"i": i, "time": bar["time"], "signal": "short",
                             "entry": round(entry, 4), "sl": round(stop, 4),
                             "tp1": round(entry - risk, 4), "tp2": round(entry - risk * rr2, 4),
                             "rr": round(rr2, 2),
                             "reason": f"Swept swing high {prior_high:.2f} and reclaimed"})
    last = sigs[-1] if sigs else None
    return {"last": last, "fresh": bool(last and last["i"] == n - 1), "count": len(sigs)}


def build_signals(instrument: str, provider, bars: int = 150) -> dict:
    ohlc = get_ohlc(instrument, provider, bars)
    det = detect_signals(ohlc)
    return {"instrument": instrument, "ohlc": ohlc, "signal": det,
            "disclaimer": "Rules-based 1H sweep-reclaim levels. Decision-support, "
                          "not a validated edge and not financial advice."}
