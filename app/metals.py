"""
Metals page: XAU / XAG / XCU bias + SMT (inter-market divergence).

DATA: uses Yahoo futures (GC=F gold, SI=F silver, HG=F copper) — free, real,
and directionally aligned with the spot metals you trade (which is what SMT
needs). To match your XM execution prices exactly, swap _fetch_all_closes to
pull from the MT5 provider later (hook noted below).

SMT here is a TRANSPARENT proxy: over a lookback window, if one metal prints a
new high/low while a correlated one does NOT, that's flagged as divergence.
It's inter-market confirmation context — not a trade signal, not advice.
"""
from __future__ import annotations
from typing import Dict, List

METALS = {
    "XAU": {"yf": "GC=F", "name": "Gold"},
    "XAG": {"yf": "SI=F", "name": "Silver"},
    "XCU": {"yf": "HG=F", "name": "Copper"},
}


def _yf_closes_single(symbol: str, days: int) -> List[float]:
    import yfinance as yf
    import pandas as pd
    try:
        df = yf.download(symbol, period=f"{days}d", interval="1d",
                         progress=False, auto_adjust=False)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    df = df.loc[:, ~df.columns.duplicated()]
    try:
        return [float(x) for x in df["Close"].dropna().tolist()]
    except Exception:
        return []


def _fetch_all_closes(days: int = 220) -> Dict[str, List[float]]:
    """Daily closes for the three metals (per-symbol; robust to yfinance column
    format differences). Empty list on failure."""
    return {k: _yf_closes_single(meta["yf"], days) for k, meta in METALS.items()}


def compute_smt(closes_by_metal: Dict[str, List[float]], L: int = 10) -> dict:
    """Transparent SMT: new L-bar extreme in one metal but not a correlated one."""
    newhigh, newlow = {}, {}
    for k, c in closes_by_metal.items():
        if len(c) < L + 1:
            continue
        prior = c[-L - 1:-1]
        newhigh[k] = c[-1] >= max(prior)
        newlow[k] = c[-1] <= min(prior)

    hs = [k for k, v in newhigh.items() if v]
    ls = [k for k, v in newlow.items() if v]
    state, details = "aligned", []

    if hs and len(hs) < len(newhigh):
        missed = [k for k in newhigh if k not in hs]
        state = "bearish_divergence"
        details.append(f"{', '.join(hs)} made a new {L}-bar high but {', '.join(missed)} did not "
                       f"— SMT divergence at the highs (a bearish tell).")
    if ls and len(ls) < len(newlow):
        missed = [k for k in newlow if k not in ls]
        details.append(f"{', '.join(ls)} made a new {L}-bar low but {', '.join(missed)} did not "
                       f"— SMT divergence at the lows (a bullish tell).")
        if state == "aligned":
            state = "bullish_divergence"

    if not details:
        details.append("The metals are confirming each other — no SMT divergence over the lookback.")

    return {"state": state, "lookback": L, "detail": " ".join(details),
            "new_highs": hs, "new_lows": ls}


def build_metals(smt_lookback: int = 10) -> dict:
    from . import analysis
    closes = _fetch_all_closes()
    metals = {}
    for k, meta in METALS.items():
        c = closes.get(k, [])
        last = c[-1] if c else None
        change = None
        if len(c) >= 2 and c[-2]:
            change = (c[-1] / c[-2] - 1) * 100
        bias = analysis.compute_index_bias(c, None, None, last=last, symbol=k)
        metals[k] = {
            "name": meta["name"],
            "last": round(last, 4) if last is not None else None,
            "change_pct": round(change, 2) if change is not None else None,
            "bias": bias,
            "spark": [round(x, 2) for x in c[-30:]],
            "bars": len(c),
        }
    smt = compute_smt(closes, smt_lookback)
    return {"metals": metals, "smt": smt}
