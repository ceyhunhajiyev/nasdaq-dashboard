"""
Analytics engine.

Turns a dict of Quotes + weights into:
  • breadth      — how broadly the constituents are participating
  • contribution — which names are actually moving the (cap-weighted) index
  • divergence   — index direction vs internal breadth (the useful edge)
  • bias         — a simple combined read

NOTE: these are analytical context, NOT trade signals or advice. A green
dashboard is not a reason to buy. Use it to inform your own process.
"""
from __future__ import annotations
from typing import Dict, List
from statistics import median
from .providers.base import Quote


def _valid(quotes: Dict[str, Quote]) -> List[Quote]:
    return [q for q in quotes.values() if q.change_pct is not None]


def compute_breadth(quotes: Dict[str, Quote]) -> dict:
    qs = _valid(quotes)
    n = len(qs)
    if n == 0:
        return {"count": 0}

    advancers = sum(1 for q in qs if q.change_pct > 0)
    decliners = sum(1 for q in qs if q.change_pct < 0)
    unchanged = n - advancers - decliners
    above_ma = sum(1 for q in qs if q.ma20 and q.last and q.last > q.ma20)
    ma_count = sum(1 for q in qs if q.ma20 and q.last)

    up_vol = sum((q.volume or 0) for q in qs if q.change_pct > 0)
    dn_vol = sum((q.volume or 0) for q in qs if q.change_pct < 0)
    tot_vol = up_vol + dn_vol

    pct_adv = advancers / n * 100.0
    changes = [q.change_pct for q in qs]

    return {
        "count": n,
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "pct_advancing": round(pct_adv, 1),
        "pct_above_ma20": round(above_ma / ma_count * 100.0, 1) if ma_count else None,
        "adv_decl_ratio": round(advancers / decliners, 2) if decliners else None,
        "up_volume_pct": round(up_vol / tot_vol * 100.0, 1) if tot_vol else None,
        "avg_change_pct": round(sum(changes) / n, 2),
        "median_change_pct": round(median(changes), 2),
    }


def compute_contribution(quotes: Dict[str, Quote], weights: Dict[str, float],
                         top: int = 10) -> dict:
    rows = []
    weighted_sum = 0.0
    for sym, q in quotes.items():
        if q.change_pct is None:
            continue
        w = weights.get(sym, 0.0)
        contrib = w * q.change_pct  # approx points of index-% from this name
        weighted_sum += contrib
        rows.append({
            "symbol": sym,
            "weight_pct": round(w * 100.0, 2),
            "change_pct": round(q.change_pct, 2),
            "contribution": round(contrib, 4),
        })

    rows.sort(key=lambda r: r["contribution"])
    laggards = rows[:top]
    leaders = list(reversed(rows[-top:]))
    all_rows = sorted(rows, key=lambda r: r["change_pct"], reverse=True)

    return {
        "estimated_index_change_pct": round(weighted_sum, 3),
        "leaders": leaders,     # biggest positive contributors
        "laggards": laggards,   # biggest negative contributors
        "all": all_rows,        # every tracked constituent (for the full table)
    }


def compute_divergence(index_quote: Quote | None, breadth: dict,
                       contribution: dict) -> dict:
    """
    Compare index direction against internal breadth.

    Classic tells:
      • Index UP but <50% advancing / negative median  -> narrow, weak rally
        (a few mega-caps carrying) -> bearish divergence.
      • Index DOWN but >50% advancing / positive median -> hidden strength
        -> bullish divergence.
    """
    if breadth.get("count", 0) == 0:
        return {"state": "no_data"}

    idx_chg = (index_quote.change_pct if index_quote and index_quote.change_pct
               is not None else contribution.get("estimated_index_change_pct"))
    if idx_chg is None:
        return {"state": "no_data"}

    pct_adv = breadth["pct_advancing"]
    med = breadth["median_change_pct"]

    state = "aligned"
    note = "Index move confirmed by breadth."
    if idx_chg > 0 and (pct_adv < 45 or med < 0):
        state = "bearish_divergence"
        note = "Index up but breadth weak — narrow rally, few names carrying it."
    elif idx_chg < 0 and (pct_adv > 55 or med > 0):
        state = "bullish_divergence"
        note = "Index down but breadth positive — selling concentrated, hidden strength."

    return {
        "state": state,
        "index_change_pct": round(idx_chg, 3),
        "pct_advancing": pct_adv,
        "median_change_pct": med,
        "note": note,
    }


def compute_bias(breadth: dict, divergence: dict) -> dict:
    """Simple 0-100 breadth-bias score (NOT a trade signal)."""
    if breadth.get("count", 0) == 0:
        return {"score": None, "label": "NO DATA"}

    # Blend: participation + trend + volume.
    parts = []
    parts.append(breadth["pct_advancing"])                    # 0..100
    if breadth.get("pct_above_ma20") is not None:
        parts.append(breadth["pct_above_ma20"])               # 0..100
    if breadth.get("up_volume_pct") is not None:
        parts.append(breadth["up_volume_pct"])                # 0..100
    score = sum(parts) / len(parts)

    label = ("STRONG BULL" if score >= 70 else "BULL" if score >= 57 else
             "BEAR" if score <= 30 else "WEAK BEAR" if score <= 43 else "NEUTRAL")

    # Divergence downgrades confidence.
    flag = divergence.get("state", "aligned")
    caution = flag in ("bearish_divergence", "bullish_divergence")

    return {"score": round(score, 1), "label": label,
            "divergence_flag": flag, "caution": caution}


def build_dashboard(quotes: Dict[str, Quote], weights: Dict[str, float],
                    index_quote: Quote | None) -> dict:
    breadth = compute_breadth(quotes)
    contribution = compute_contribution(quotes, weights)
    divergence = compute_divergence(index_quote, breadth, contribution)
    bias = compute_bias(breadth, divergence)
    return {
        "index": index_quote.to_dict() if index_quote else None,
        "breadth": breadth,
        "contribution": contribution,
        "divergence": divergence,
        "bias": bias,
    }
