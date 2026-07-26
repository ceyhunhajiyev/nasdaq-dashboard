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
