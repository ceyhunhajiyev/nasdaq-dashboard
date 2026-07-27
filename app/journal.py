"""
Trade journal — log live setups and track their REAL outcomes against the
backtest's expectancy. Stored as trades.json in the project root (gitignored).

The point: the backtest is a hint from limited history. Your own logged results
are the real judge. This lets you compare live avg-R (per grade) to what the
backtest suggested, so you find out whether the A/B setups actually pay for YOU.
Not financial advice.
"""
import json, time
from pathlib import Path

JOURNAL_FILE = Path(__file__).resolve().parent.parent / "trades.json"

# map quick outcome buttons to R multiples
OUTCOME_R = {"sl": -1.0, "be": 0.0, "tp1": 1.0, "tp2": 2.0}

# rough backtest hint per grade (from the out-of-sample validation) — context only
BACKTEST_HINT = {"A": 0.25, "B": 0.10, "C": 0.0, "D": -0.15}


def _load():
    try:
        return json.loads(JOURNAL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(trades):
    JOURNAL_FILE.write_text(json.dumps(trades, indent=2), encoding="utf-8")


def add(t: dict) -> dict:
    trades = _load()
    tid = (max((x["id"] for x in trades), default=0) + 1)
    rec = {"id": tid, "ts_open": time.time(), "status": "open",
           "result_R": None, "ts_close": None,
           "instrument": t.get("instrument"), "tf": t.get("tf"),
           "grade": t.get("grade"), "direction": t.get("direction"),
           "entry": t.get("entry"), "sl": t.get("sl"),
           "tp1": t.get("tp1"), "tp2": t.get("tp2"), "note": t.get("note", "")}
    trades.append(rec)
    _save(trades)
    return rec


def close(tid: int, outcome=None, result_R=None):
    trades = _load()
    for t in trades:
        if t["id"] == tid:
            if outcome in OUTCOME_R:
                t["result_R"] = OUTCOME_R[outcome]
            elif result_R is not None:
                t["result_R"] = float(result_R)
            t["status"] = "closed"
            t["ts_close"] = time.time()
            break
    _save(trades)


def delete(tid: int):
    _save([t for t in _load() if t["id"] != tid])


def _agg(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "win_rate": None, "avg_R": None, "total_R": 0.0}
    wins = [r for r in rows if r["result_R"] > 0]
    return {"n": n, "win_rate": len(wins) / n,
            "avg_R": sum(r["result_R"] for r in rows) / n,
            "total_R": sum(r["result_R"] for r in rows)}


def stats():
    trades = _load()
    closed = [t for t in trades if t["status"] == "closed" and t["result_R"] is not None]
    by_grade = {}
    for g in ["A", "B", "C", "D"]:
        a = _agg([t for t in closed if t["grade"] == g])
        a["backtest_hint"] = BACKTEST_HINT.get(g)
        by_grade[g] = a
    return {"overall": _agg(closed),
            "by_grade": by_grade,
            "open": len([t for t in trades if t["status"] == "open"]),
            "closed": len(closed)}


def all_trades():
    return sorted(_load(), key=lambda t: t["ts_open"], reverse=True)
