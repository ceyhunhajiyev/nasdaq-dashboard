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

# Alpaca ETF equivalents for the indices — used as a FALLBACK when Yahoo is
# unreachable (e.g. a corporate network blocks it). ETF price scale differs from
# the futures scale, so this is for keeping charts alive, not for exact levels.
ALPACA_ETF = {"US100": "QQQ", "SP500": "SPY", "US30": "DIA"}

# timeframe -> yfinance (interval, period) [+ optional resample from a base interval]
TF_MAP = {
    "1m":  {"interval": "1m",  "period": "5d"},
    "5m":  {"interval": "5m",  "period": "5d"},
    "15m": {"interval": "15m", "period": "1mo"},
    "1h":  {"interval": "1h",  "period": "30d"},
    "4h":  {"interval": "1h",  "period": "60d", "resample": "4h"},  # yfinance has no 4h
    "1d":  {"interval": "1d",  "period": "1y"},
}
ALPACA_TF = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour", "4h": "4Hour", "1d": "1Day"}
TF_SECS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
TF_DAYS = {"1m": 2, "5m": 5, "15m": 20, "1h": 30, "4h": 90, "1d": 365}

# ---- MT5 (XM) live feed -----------------------------------------------------
# instrument -> your XM/MT5 symbol name. These are GUESSES — XM naming varies.
# Run the discovery command in the README to list your exact names, then edit.
MT5_SYMBOLS = {
    "US100": "US100Cash",
    "SP500": "US500Cash",
    "US30":  "US30Cash",
    "XAU":   "GOLD",
    "XAG":   "SILVER",
    "XCU":   "COPPER",
}
MT5_TF = {"1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15",
          "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1"}


def _mt5_ohlc(mt5_symbol: str, bars: int, tf: str = "1h") -> List[dict]:
    import MetaTrader5 as mt5  # Windows-only; lazy import
    if not mt5.initialize():
        return []
    tf_const = getattr(mt5, MT5_TF.get(tf, "TIMEFRAME_H1"))
    mt5.symbol_select(mt5_symbol, True)
    rates = mt5.copy_rates_from_pos(mt5_symbol, tf_const, 0, bars)
    if rates is None:
        return []
    out = []
    for r in rates:
        out.append({"time": int(r["time"]), "open": float(r["open"]),
                    "high": float(r["high"]), "low": float(r["low"]),
                    "close": float(r["close"])})
    return out


# ============================ OHLC SOURCES =================================
def _alpaca_ohlc(provider, symbol: str, bars: int, tf: str = "1h") -> List[dict]:
    import httpx
    days = TF_DAYS.get(tf, 30)
    start = (dt.datetime.utcnow() - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with httpx.Client(timeout=15, headers=provider._headers) as c:
        r = c.get(f"{provider.BASE}/v2/stocks/bars",
                  params={"symbols": symbol, "timeframe": ALPACA_TF.get(tf, "1Hour"),
                          "start": start, "limit": 10000, "feed": provider.feed})
        r.raise_for_status()
        arr = r.json().get("bars", {}).get(symbol, [])
    out = []
    for b in arr[-bars:]:
        ts = int(dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp())
        out.append({"time": ts, "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"]})
    return out


def _yf_ohlc(symbol: str, bars: int, tf: str = "1h") -> List[dict]:
    import yfinance as yf
    import pandas as pd
    cfg = TF_MAP.get(tf, TF_MAP["1h"])
    df = yf.download(symbol, period=cfg["period"], interval=cfg["interval"],
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    df = df.loc[:, ~df.columns.duplicated()]
    if cfg.get("resample"):
        df = df.resample(cfg["resample"], label="left", closed="left").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    out = []
    for idx, row in df.tail(bars).iterrows():
        try:
            ts = int(pd.Timestamp(idx).timestamp())
            out.append({"time": ts, "open": float(row["Open"]), "high": float(row["High"]),
                        "low": float(row["Low"]), "close": float(row["Close"])})
        except Exception:
            continue
    return out


def _synth_ohlc(symbol: str, bars: int = 150, tf: str = "1h") -> List[dict]:
    import random, time
    rng = random.Random(abs(hash(symbol)) % (2**32))
    price = rng.uniform(300, 600)
    step = TF_SECS.get(tf, 3600)
    now = int(time.time())
    out = []
    for i in range(bars):
        o = price
        price *= (1 + rng.gauss(0.0002, 0.006))
        c = price
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.002)))
        l = min(o, c) * (1 - abs(rng.gauss(0, 0.002)))
        out.append({"time": now - (bars - i) * step, "open": round(o, 2),
                    "high": round(h, 2), "low": round(l, 2), "close": round(c, 2)})
    return out


def get_ohlc(instrument: str, provider, bars: int = 150, tf: str = "1h"):
    """Returns (ohlc_list, source_label) at the requested timeframe.
    If CHART_SOURCE=mt5, pulls LIVE from your XM MT5 terminal first (real-time,
    correct scale). Otherwise / on failure, uses Yahoo futures, then Alpaca ETF."""
    sym = DATA_MAP.get(instrument)
    if not sym:
        return [], None
    name = getattr(provider, "name", "")

    # 0) live MT5/XM feed (opt-in via CHART_SOURCE=mt5)
    import os
    if os.getenv("CHART_SOURCE", "yahoo").lower() == "mt5":
        msym = MT5_SYMBOLS.get(instrument)
        if msym:
            try:
                d = _mt5_ohlc(msym, bars, tf)
                if d:
                    return d, f"MT5 live ({msym})"
            except Exception:
                pass  # fall through to Yahoo if MT5 unavailable

    if name == "mock":
        return _synth_ohlc(sym, bars, tf), "synthetic"

    # 1) preferred: Yahoo futures (correct trading scale)
    try:
        data = _yf_ohlc(sym, bars, tf)
    except Exception:
        data = []
    if data:
        return data, f"futures {sym}"

    # 2) fallback: Alpaca ETF for indices (works when Yahoo is blocked)
    etf = ALPACA_ETF.get(instrument)
    if etf and name == "alpaca":
        try:
            d2 = _alpaca_ohlc(provider, etf, bars, tf)
            if d2:
                return d2, f"ETF {etf} (Alpaca fallback — ETF price scale)"
        except Exception:
            pass
    return [], None


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


def build_signals(instrument: str, provider, bars: int = 150, tf: str = "1h") -> dict:
    ohlc, source = get_ohlc(instrument, provider, bars, tf)
    det = detect_signals(ohlc)
    return {"instrument": instrument, "tf": tf, "ohlc": ohlc, "source": source, "signal": det,
            "disclaimer": f"Rules-based {tf} sweep-reclaim levels. Decision-support, "
                          "not a validated edge and not financial advice."}
