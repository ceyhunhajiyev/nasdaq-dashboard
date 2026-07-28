"""
Analysis layer for the platform:

  1. compute_index_bias(...) — a TRANSPARENT, rules-based bias for the index.
     Every factor is listed with its own +/- signal and a human-readable reason.
     It is decision-support you interpret, NOT a trade signal and NOT advice.

  2. generate_ai_note(...) — an optional plain-English synthesis via Claude.
     It ONLY summarizes the numbers it is given. It does not predict prices and
     does not recommend buying or selling (enforced in the system prompt).
     Requires your own ANTHROPIC_API_KEY.

Index price history is pulled for whichever provider is active (alpaca / yfinance
/ mock). Other providers return no history, in which case the trend/momentum
factors are simply omitted and the breadth factors still work.
"""
from __future__ import annotations
import os
import json
import datetime as dt
from typing import List, Optional


# ============================ INDEX PRICE HISTORY ============================
def _alpaca_closes(provider, symbol: str, days: int) -> List[float]:
    import httpx
    start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    with httpx.Client(timeout=15, headers=provider._headers) as c:
        r = c.get(f"{provider.BASE}/v2/stocks/bars",
                  params={"symbols": symbol, "timeframe": "1Day",
                          "start": start, "limit": 10000, "feed": provider.feed})
        r.raise_for_status()
        bars = r.json().get("bars", {}).get(symbol, [])
        return [float(b["c"]) for b in bars if "c" in b]


def _yf_closes(symbol: str, days: int) -> List[float]:
    import yfinance as yf
    df = yf.download(symbol, period=f"{days}d", interval="1d",
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        return []
    return [float(x) for x in df["Close"].dropna().tolist()]


def _mock_closes(symbol: str, n: int = 260) -> List[float]:
    import random
    rng = random.Random(abs(hash(symbol)) % (2**32))
    price = rng.uniform(300, 600)
    out = []
    for _ in range(n):
        price *= (1 + rng.gauss(0.0004, 0.012))
        out.append(round(price, 2))
    return out


def get_index_closes(provider, symbol: str, days: int = 400) -> List[float]:
    name = getattr(provider, "name", "")
    try:
        if name == "alpaca":
            return _alpaca_closes(provider, symbol, days)
        if name == "yfinance":
            return _yf_closes(symbol, days)
        if name == "mock":
            return _mock_closes(symbol)
    except Exception:
        return []
    return []


# ============================ INDICATOR HELPERS =============================
def _ema_series(vals: List[float], n: int) -> List[float]:
    if not vals:
        return []
    k = 2 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _ema_last(vals: List[float], n: int) -> Optional[float]:
    s = _ema_series(vals, n)
    return s[-1] if s else None


def _rsi(vals: List[float], n: int = 14) -> Optional[float]:
    if len(vals) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[-n:]) / n
    al = sum(losses[-n:]) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def _macd(vals: List[float]):
    if len(vals) < 35:
        return (None, None)
    e12 = _ema_series(vals, 12)
    e26 = _ema_series(vals, 26)
    macd_series = [a - b for a, b in zip(e12, e26)]
    sig_series = _ema_series(macd_series, 9)
    return (macd_series[-1], sig_series[-1])


# ==================== EXTRA INDICATORS (for the deep bias) =================
def _sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def _roc(closes, n=10):
    if len(closes) < n + 1 or closes[-1 - n] == 0:
        return None
    return (closes[-1] / closes[-1 - n] - 1) * 100


def _stochastic(highs, lows, closes, n=14):
    if len(closes) < n:
        return None
    hh = max(highs[-n:]); ll = min(lows[-n:])
    if hh == ll:
        return 50.0
    return (closes[-1] - ll) / (hh - ll) * 100


def _williams_r(highs, lows, closes, n=14):
    if len(closes) < n:
        return None
    hh = max(highs[-n:]); ll = min(lows[-n:])
    if hh == ll:
        return -50.0
    return (hh - closes[-1]) / (hh - ll) * -100


def _cci(highs, lows, closes, n=20):
    if len(closes) < n:
        return None
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    sma = _sma(tp, n)
    mean_dev = sum(abs(x - sma) for x in tp[-n:]) / n
    if mean_dev == 0:
        return 0.0
    return (tp[-1] - sma) / (0.015 * mean_dev)


def _bollinger_pctb(closes, n=20, k=2):
    if len(closes) < n:
        return None
    mid = _sma(closes, n)
    var = sum((c - mid) ** 2 for c in closes[-n:]) / n
    sd = var ** 0.5
    if sd == 0:
        return 0.5
    upper = mid + k * sd; lower = mid - k * sd
    return (closes[-1] - lower) / (upper - lower)


def _atr_arr(highs, lows, closes, n=14):
    if len(closes) < n + 1:
        return None
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(closes))]
    return sum(trs[-n:]) / n


def _tf_lean(closes):
    """Simple bull/bear lean for a timeframe from EMA alignment + RSI."""
    if len(closes) < 50:
        return "neutral"
    e20 = _ema_last(closes, 20); e50 = _ema_last(closes, 50); c = closes[-1]
    rsi = _rsi(closes, 14) or 50
    votes = 0
    votes += 1 if c > e20 else -1
    votes += 1 if e20 > e50 else -1
    votes += 1 if rsi >= 50 else -1
    return "bull" if votes > 0 else "bear" if votes < 0 else "neutral"


def _score_votes(votes):
    """votes: list of 'bull'/'bear'/'neutral' -> 0..100 or None."""
    dirs = [v for v in votes if v in ("bull", "bear")]
    if not dirs:
        return None
    s = sum(1 if v == "bull" else -1 for v in dirs)
    return round((s / len(dirs) + 1) / 2 * 100, 1)


def compute_deep_bias(daily_ohlc, closes_4h, closes_1h, breadth, divergence,
                      last=None, symbol="US100"):
    """Category-based bias. Indicators are grouped so redundant momentum
    oscillators can't outvote the independent breadth/multi-timeframe reads.
    Overall = weighted blend of category sub-scores (independent categories
    weighted higher). Transparent decision-support — not a trade call."""
    highs = [b["high"] for b in daily_ohlc]
    lows = [b["low"] for b in daily_ohlc]
    closes = [b["close"] for b in daily_ohlc]
    price = last if last is not None else (closes[-1] if closes else None)
    cats = []  # {name, weight, score, factors:[{name,signal,detail}]}

    def bull(x):
        return "bull" if x else "bear"

    # ---- TREND (weight 1.0) ----
    tf = []
    if len(closes) >= 20 and price is not None:
        e20 = _ema_last(closes, 20)
        tf.append(("Price vs EMA20", bull(price > e20), f"{price:.1f} {'>' if price>e20 else '<'} EMA20 {e20:.1f}"))
        if len(closes) >= 50:
            e50 = _ema_last(closes, 50)
            tf.append(("EMA20 vs EMA50", bull(e20 > e50), f"EMA20 {'>' if e20>e50 else '<'} EMA50 {e50:.1f}"))
        if len(closes) >= 200:
            e200 = _ema_last(closes, 200)
            tf.append(("Price vs EMA200", bull(price > e200), f"{'above' if price>e200 else 'below'} EMA200 {e200:.1f}"))
        pb = _bollinger_pctb(closes)
        if pb is not None:
            tf.append(("Bollinger %B", "bull" if pb > 0.55 else "bear" if pb < 0.45 else "neutral", f"{pb*100:.0f}% of band"))
    cats.append({"name": "Trend", "weight": 1.0, "factors": [{"name": n, "signal": s, "detail": d} for n, s, d in tf],
                 "score": _score_votes([s for _, s, _ in tf])})

    # ---- MOMENTUM (weight 0.7 — many but redundant) ----
    mo = []
    rsi = _rsi(closes, 14)
    if rsi is not None:
        mo.append(("RSI(14)", "bull" if rsi >= 55 else "bear" if rsi <= 45 else "neutral", f"{rsi:.0f}"))
    macd, sigl = _macd(closes)
    if macd is not None:
        mo.append(("MACD", bull(macd > sigl), f"{'above' if macd>sigl else 'below'} signal"))
    st = _stochastic(highs, lows, closes)
    if st is not None:
        mo.append(("Stochastic", "bull" if st > 55 else "bear" if st < 45 else "neutral", f"%K {st:.0f}"))
    cci = _cci(highs, lows, closes)
    if cci is not None:
        mo.append(("CCI(20)", "bull" if cci > 0 else "bear", f"{cci:.0f}"))
    wr = _williams_r(highs, lows, closes)
    if wr is not None:
        mo.append(("Williams %R", "bull" if wr > -50 else "bear", f"{wr:.0f}"))
    roc = _roc(closes, 10)
    if roc is not None:
        mo.append(("ROC(10)", bull(roc > 0), f"{roc:+.1f}%"))
    r20 = _roc(closes, 20)
    if r20 is not None:
        mo.append(("20-bar return", bull(r20 > 0), f"{r20:+.1f}%"))
    cats.append({"name": "Momentum", "weight": 0.7, "factors": [{"name": n, "signal": s, "detail": d} for n, s, d in mo],
                 "score": _score_votes([s for _, s, _ in mo])})

    # ---- PARTICIPATION / BREADTH (weight 1.3 — independent) ----
    br = []
    if breadth and breadth.get("count", 0) > 0:
        pa = breadth["pct_advancing"]
        br.append(("Breadth participation", "bull" if pa >= 55 else "bear" if pa <= 45 else "neutral", f"{pa:.0f}% advancing"))
        if breadth.get("pct_above_ma20") is not None:
            pm = breadth["pct_above_ma20"]
            br.append(("Breadth trend", "bull" if pm >= 55 else "bear" if pm <= 45 else "neutral", f"{pm:.0f}% above 20-MA"))
    cats.append({"name": "Participation", "weight": 1.3, "factors": [{"name": n, "signal": s, "detail": d} for n, s, d in br],
                 "score": _score_votes([s for _, s, _ in br])})

    # ---- MULTI-TIMEFRAME (weight 1.3 — independent) ----
    mt = []
    dl = _tf_lean(closes); h4 = _tf_lean(closes_4h or []); h1 = _tf_lean(closes_1h or [])
    mt.append(("Daily trend", dl, dl))
    mt.append(("4H trend", h4, h4))
    mt.append(("1H trend", h1, h1))
    agree = len(set(x for x in [dl, h4, h1] if x != "neutral"))
    mt_detail = "all timeframes agree" if agree == 1 else "timeframes mixed"
    cats.append({"name": "Multi-timeframe", "weight": 1.3, "factors": [{"name": n, "signal": s, "detail": d} for n, s, d in mt],
                 "score": _score_votes([s for _, s, _ in mt]), "note": mt_detail})

    # ---- weighted overall ----
    scored = [(c["weight"], c["score"]) for c in cats if c["score"] is not None]
    if scored:
        overall = round(sum(w * s for w, s in scored) / sum(w for w, _ in scored), 1)
    else:
        overall = None
    label = ("NO DATA" if overall is None else
             "BULLISH" if overall >= 60 else "BEARISH" if overall <= 40 else "NEUTRAL")

    # context (not scored)
    context = []
    atr = _atr_arr(highs, lows, closes)
    if atr is not None and price:
        context.append({"name": "Volatility (ATR)", "signal": "neutral", "detail": f"{atr:.1f} (~{atr/price*100:.2f}% of price)"})
    ds = (divergence or {}).get("state", "")
    if ds.endswith("divergence"):
        context.append({"name": "Divergence", "signal": "caution", "detail": (divergence.get("note") or ds).strip()})

    return {"instrument": symbol, "score": overall, "bias": label,
            "categories": cats, "context": context,
            "disclaimer": "Category-weighted daily bias (independent breadth & multi-timeframe "
                          "reads weighted above redundant momentum). Decision-support, not a trade call."}


# ============================ TRANSPARENT BIAS =============================
def compute_index_bias(closes, breadth, divergence, last=None, symbol="US100"):
    factors = []

    def add(name, sig, detail):
        factors.append({"name": name, "signal": sig, "detail": detail})

    n = len(closes)
    price = last if last is not None else (closes[-1] if closes else None)

    if n >= 20 and price is not None:
        e20 = _ema_last(closes, 20)
        add("Price vs EMA20", "bull" if price > e20 else "bear",
            f"{price:.2f} {'above' if price > e20 else 'below'} EMA20 {e20:.2f}")
        if n >= 50:
            e50 = _ema_last(closes, 50)
            add("EMA20 vs EMA50", "bull" if e20 > e50 else "bear",
                f"EMA20 {e20:.2f} {'>' if e20 > e50 else '<'} EMA50 {e50:.2f}")
        if n >= 200:
            e200 = _ema_last(closes, 200)
            add("Price vs EMA200", "bull" if price > e200 else "bear",
                f"{'above' if price > e200 else 'below'} EMA200 {e200:.2f} (long-term)")
        rsi = _rsi(closes, 14)
        if rsi is not None:
            sig = "bull" if rsi >= 55 else "bear" if rsi <= 45 else "neutral"
            note = " · overbought" if rsi > 70 else " · oversold" if rsi < 30 else ""
            add("RSI(14)", sig, f"{rsi:.0f}{note}")
        macd, sigl = _macd(closes)
        if macd is not None and sigl is not None:
            add("MACD", "bull" if macd > sigl else "bear",
                f"MACD {'above' if macd > sigl else 'below'} signal line")
        if n >= 21:
            r20 = (price / closes[-21] - 1) * 100
            add("20-day return", "bull" if r20 > 0 else "bear", f"{r20:+.1f}% over ~1 month")
    else:
        add("Price trend", "neutral", "insufficient index history — trend factors unavailable")

    if breadth and breadth.get("count", 0) > 0:
        pa = breadth["pct_advancing"]
        add("Breadth participation",
            "bull" if pa >= 55 else "bear" if pa <= 45 else "neutral",
            f"{pa:.0f}% of constituents advancing")
        if breadth.get("pct_above_ma20") is not None:
            pm = breadth["pct_above_ma20"]
            add("Breadth trend",
                "bull" if pm >= 55 else "bear" if pm <= 45 else "neutral",
                f"{pm:.0f}% above their 20-day MA")

    ds = (divergence or {}).get("state", "")
    if ds.endswith("divergence"):
        add("Divergence", "caution", (divergence.get("note") or ds).strip())

    directional = [f for f in factors if f["signal"] in ("bull", "bear")]
    if directional:
        s = sum(1 if f["signal"] == "bull" else -1 for f in directional)
        score = round((s / len(directional) + 1) / 2 * 100, 1)
    else:
        score = None
    label = ("NO DATA" if score is None else
             "BULLISH" if score >= 60 else "BEARISH" if score <= 40 else "NEUTRAL")

    return {
        "instrument": symbol,
        "score": score,
        "bias": label,
        "caution": any(f["signal"] == "caution" for f in factors),
        "factors": factors,
        "disclaimer": ("Higher-timeframe (daily) read from the listed factors. "
                       "Analytical context only — not a trade signal, not financial advice."),
    }


# ============================ AI SYNTHESIS NOTE =============================
_SYSTEM = (
    "You are a market-internals analyst assistant. You are given a JSON snapshot "
    "of ALREADY-COMPUTED indicators for a stock index. Summarize, in plain English, "
    "what the data shows: the current bias, the main supporting factors, the main "
    "conflicting factors, and the key caveats.\n"
    "STRICT RULES:\n"
    "- Do NOT predict future prices or say what the market 'will' do.\n"
    "- Do NOT give buy / sell / hold recommendations or any financial advice.\n"
    "- Do NOT invent numbers or factors not present in the data.\n"
    "- Explicitly remind the reader this is analytical context, not advice, and that "
    "breadth/divergence describe current participation, not a forecast.\n"
    "Keep it under 160 words, neutral and factual, in short paragraphs."
)


def generate_ai_note(snapshot: dict) -> dict:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return {"ok": False,
                "error": "ANTHROPIC_API_KEY not set. Add your Anthropic API key "
                         "(env var) to enable the AI note."}
    model = os.getenv("AI_NOTE_MODEL", "claude-3-5-haiku-latest")
    payload = {
        "model": model,
        "max_tokens": 400,
        "system": _SYSTEM,
        "messages": [{"role": "user",
                      "content": "Summarize this market-internals snapshot:\n\n"
                                 + json.dumps(snapshot)[:6000]}],
    }
    try:
        import httpx
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=30,
                       headers={"x-api-key": key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json"},
                       json=payload)
        r.raise_for_status()
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text").strip()
        return {"ok": True, "text": text, "model": model}
    except Exception as e:
        return {"ok": False, "error": str(e)}
