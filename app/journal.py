"""
Trade journal — log live setups and track their REAL outcomes against the
backtest's expectancy. Stored as trades.json in the project root (gitignored).

The point: the backtest is a hint from limited history. Your own logged results
are the real judge. This lets you compare live avg-R (per grade) to what the
backtest suggested, so you find out whether the A/B setups actually pay for YOU.
Not financial advice.
"""
import json, time, threading, os
from pathlib import Path
from . import settings as _settings  # noqa: F401  (ensures .env is loaded)

# Journal location: set JOURNAL_PATH in .env to a cloud-synced folder (OneDrive,
# Dropbox, Google Drive) to share the journal across machines. Falls back to a
# local file in the project folder if unset.
_jp = os.getenv("JOURNAL_PATH")
JOURNAL_FILE = Path(_jp).expanduser() if _jp else (Path(__file__).resolve().parent.parent / "trades.json")
_LOCK = threading.Lock()

# instruments/timeframes the auto-scanner watches (only these can grade A or B)
AUTO_INSTRUMENTS = ["US100", "SP500", "US30", "XAU", "XAG"]
AUTO_TFS = ["15m", "4h"]

# map quick outcome buttons to R multiples
OUTCOME_R = {"sl": -1.0, "be": 0.0, "tp1": 1.0, "tp2": 2.0}

# rough backtest hint per grade (from the out-of-sample validation) — context only
BACKTEST_HINT = {"A": 0.25, "B": 0.10, "C": 0.0, "D": -0.15}
# ORB (trend + ATR1.0 stop + 1R target): ~+0.05R avg across indices, out-of-sample
ORB_HINT = 0.05


def _load():
    try:
        return json.loads(JOURNAL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(trades):
    try:
        JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    JOURNAL_FILE.write_text(json.dumps(trades, indent=2), encoding="utf-8")


def add(t: dict) -> dict:
    with _LOCK:
        trades = _load()
        tid = (max((x["id"] for x in trades), default=0) + 1)
        rec = {"id": tid, "ts_open": time.time(), "status": "open",
               "result_R": None, "ts_close": None, "auto": bool(t.get("auto")),
               "bar_time": t.get("bar_time"), "strategy": t.get("strategy", "sweep"),
               "instrument": t.get("instrument"), "tf": t.get("tf"),
               "grade": t.get("grade"), "direction": t.get("direction"),
               "entry": t.get("entry"), "sl": t.get("sl"),
               "tp1": t.get("tp1"), "tp2": t.get("tp2"), "note": t.get("note", "")}
        trades.append(rec)
        _save(trades)
        return rec


def _exists(trades, instrument, tf, bar_time, direction) -> bool:
    return any(x["instrument"] == instrument and x["tf"] == tf
               and x.get("bar_time") == bar_time and x["direction"] == direction
               for x in trades)


def auto_scan_and_log(provider) -> int:
    """Scan 15M/4H on the watched instruments; auto-log fresh A/B setups (deduped)."""
    from . import signals as S
    added = 0
    with _LOCK:
        trades = _load()
        for inst in AUTO_INSTRUMENTS:
            for tf in AUTO_TFS:
                try:
                    d = S.build_signals(inst, provider, tf=tf)
                except Exception:
                    continue
                det = d["signal"]; sig = det.get("last")
                if not sig or not det.get("fresh"):
                    continue
                grade = S.grade_setup(inst, tf, det, d["validated"])["grade"]
                if grade not in ("A", "B"):
                    continue
                if _exists(trades, inst, tf, sig.get("time"), sig["signal"]):
                    continue
                tid = (max((x["id"] for x in trades), default=0) + 1)
                trades.append({"id": tid, "ts_open": time.time(), "status": "open",
                               "result_R": None, "ts_close": None, "auto": True,
                               "bar_time": sig.get("time"), "strategy": "sweep",
                               "instrument": inst, "tf": tf,
                               "grade": grade, "direction": sig["signal"],
                               "entry": sig["entry"], "sl": sig["sl"],
                               "tp1": sig["tp1"], "tp2": sig["tp2"], "note": ""})
                added += 1
        # --- ORB forward-tracking: auto-capture fresh trend/ATR/1R ORB signals
        # (ungraded — this builds a live record to prove/disprove it forward)
        for inst in ["US100", "SP500", "US30"]:
            try:
                d = S.build_signals(inst, provider, tf="15m", strategy="orb")
            except Exception:
                continue
            det = d["signal"]; sig = det.get("last")
            if not sig or not det.get("fresh"):
                continue
            if _exists(trades, inst, "15m", sig.get("time"), sig["signal"]):
                continue
            tid = (max((x["id"] for x in trades), default=0) + 1)
            trades.append({"id": tid, "ts_open": time.time(), "status": "open",
                           "result_R": None, "ts_close": None, "auto": True,
                           "bar_time": sig.get("time"), "strategy": "orb",
                           "instrument": inst, "tf": "15m",
                           "grade": "ORB", "direction": sig["signal"],
                           "entry": sig["entry"], "sl": sig["sl"],
                           "tp1": sig["tp1"], "tp2": sig["tp2"], "note": ""})
            added += 1
        if added:
            _save(trades)
    return added


def auto_resolve(provider) -> int:
    """Close open trades whose SL or TP2 has been hit (stop-first, conservative)."""
    from . import signals as S
    resolved = 0
    with _LOCK:
        trades = _load()
        open_t = [t for t in trades if t["status"] == "open"]
        cache = {}
        for t in open_t:
            bt = t.get("bar_time")
            if bt is None:
                continue
            key = (t["instrument"], t["tf"])
            if key not in cache:
                try:
                    cache[key] = S.get_ohlc(t["instrument"], provider, 500, t["tf"])[0]
                except Exception:
                    cache[key] = []
            after = [b for b in cache[key] if b["time"] > bt]
            long = t["direction"] == "long"
            # ORB's validated exit is 1R (tp1); sweep uses 2R (tp2)
            orb = t.get("strategy") == "orb"
            target = t["tp1"] if orb else t["tp2"]
            win_R = 1.0 if orb else 2.0
            outcome = None
            for b in after:
                if long:
                    if b["low"] <= t["sl"]:
                        outcome = -1.0; break
                    if b["high"] >= target:
                        outcome = win_R; break
                else:
                    if b["high"] >= t["sl"]:
                        outcome = -1.0; break
                    if b["low"] <= target:
                        outcome = win_R; break
            if outcome is not None:
                t["status"] = "closed"; t["result_R"] = outcome
                t["ts_close"] = time.time(); t["auto_closed"] = True
                resolved += 1
        if resolved:
            _save(trades)
    return resolved


def close(tid: int, outcome=None, result_R=None):
    with _LOCK:
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
    with _LOCK:
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
    sweep_closed = [t for t in closed if t.get("strategy", "sweep") == "sweep"]
    orb_closed = [t for t in closed if t.get("strategy") == "orb"]
    by_grade = {}
    for g in ["A", "B", "C", "D"]:
        a = _agg([t for t in sweep_closed if t["grade"] == g])
        a["backtest_hint"] = BACKTEST_HINT.get(g)
        by_grade[g] = a
    orb = _agg(orb_closed)
    orb["backtest_hint"] = ORB_HINT
    orb["open"] = len([t for t in trades if t.get("strategy") == "orb" and t["status"] == "open"])
    return {"overall": _agg(sweep_closed),
            "by_grade": by_grade,
            "orb": orb,
            "open": len([t for t in trades if t["status"] == "open"]),
            "closed": len(closed)}


def all_trades():
    return sorted(_load(), key=lambda t: t["ts_open"], reverse=True)
