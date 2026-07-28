"""
Backtest the sweep-reclaim rule, NET of costs, WITH a bias/trend filter tested
OUT-OF-SAMPLE. Same rule your live Signals page uses (app.signals.all_signals).

THE FILTER (honest scope):
  The live bias mixes price factors with breadth/divergence. Breadth comes from a
  LIVE snapshot with no historical record, so it can't be backtested. What we CAN
  test is the price/trend core of the bias: only take a signal if it agrees with
  the trend (long only when price>EMA50 and EMA20>EMA50; short only in the mirror).
  It's a FIXED rule, not a tuned parameter — little to overfit.

OUT-OF-SAMPLE:
  Each series is split in half by time. 'OOS' columns show the filter's result on
  the LATER half only. A filter that helps in-sample but fails OOS was noise.

Honesty: stop-first within-bar (conservative), costs subtracted per trade in R,
open trades marked to last close. Validation, not financial advice.

Run:  venv/Scripts/python backtest.py   ->  console table + backtest_report.html
"""
import statistics, datetime
from app import signals as S

INSTRUMENTS = ["US100", "SP500", "US30", "XAU", "XAG"]
TFS = ["5m", "15m", "1h", "4h", "1d"]
EXITS = ["fixed2R", "tp1_be", "time10"]
BT_PERIOD = {"1m": "7d", "5m": "60d", "15m": "60d", "1h": "730d", "4h": "730d", "1d": "5y"}
TIME_BARS = 10

# trading costs — EDIT to match your XM account (spread/commission in PRICE units, round-trip)
COSTS = {
    "US100": {"spread": 1.5,  "commission": 0.0},
    "SP500": {"spread": 0.5,  "commission": 0.0},
    "US30":  {"spread": 2.0,  "commission": 0.0},
    "XAU":   {"spread": 0.30, "commission": 0.0},
    "XAG":   {"spread": 0.03, "commission": 0.0},
}


def fetch(instrument, tf):
    import yfinance as yf
    import pandas as pd
    sym = S.DATA_MAP.get(instrument)
    interval = {"4h": "1h"}.get(tf, tf)
    df = yf.download(sym, period=BT_PERIOD.get(tf, "60d"), interval=interval,
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy(); df.columns = df.columns.get_level_values(0)
    df = df.loc[:, ~df.columns.duplicated()]
    if tf == "4h":
        df = df.resample("4h", label="left", closed="left").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    out = []
    for idx, row in df.iterrows():
        try:
            out.append({"time": int(pd.Timestamp(idx).timestamp()), "open": float(row["Open"]),
                        "high": float(row["High"]), "low": float(row["Low"]), "close": float(row["Close"])})
        except Exception:
            continue
    return out


def _ema_series(closes, period):
    k = 2 / (period + 1)
    out, e = [], None
    for c in closes:
        e = c if e is None else c * k + e * (1 - k)
        out.append(e)
    return out


def trend_dirs(ohlc):
    """Per-bar trend direction — backtestable core of the bias. Causal (no lookahead)."""
    closes = [b["close"] for b in ohlc]
    e20 = _ema_series(closes, 20)
    e50 = _ema_series(closes, 50)
    dirs = []
    for i in range(len(closes)):
        if i < 50:
            dirs.append("neutral"); continue
        c = closes[i]
        if c > e50[i] and e20[i] > e50[i]:
            dirs.append("bull")
        elif c < e50[i] and e20[i] < e50[i]:
            dirs.append("bear")
        else:
            dirs.append("neutral")
    return dirs


def simulate(ohlc, exit_mode, dirs):
    """Return trades as (gross_R, risk_price, aligned_bool, signal_index)."""
    sigs = S.all_signals(ohlc)
    n = len(ohlc)
    trades = []
    for sg in sigs:
        i = sg["i"]; entry = sg["entry"]; sl = sg["sl"]; tp1 = sg["tp1"]; tp2 = sg["tp2"]
        long = sg["signal"] == "long"
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        aligned = (long and dirs[i] == "bull") or ((not long) and dirs[i] == "bear")
        tp1_hit = False
        outcome = None
        for j in range(i + 1, n):
            b = ohlc[j]
            if long:
                stop_level = entry if tp1_hit else sl
                if b["low"] <= stop_level:
                    outcome = 0.0 if tp1_hit else -1.0; break
                if exit_mode == "fixed2R" and b["high"] >= tp2:
                    outcome = 2.0; break
                if exit_mode == "tp1_be":
                    if not tp1_hit and b["high"] >= tp1:
                        tp1_hit = True
                    if tp1_hit and b["high"] >= tp2:
                        outcome = 2.0; break
                if exit_mode == "time10" and j - i >= TIME_BARS:
                    outcome = (b["close"] - entry) / risk; break
            else:
                stop_level = entry if tp1_hit else sl
                if b["high"] >= stop_level:
                    outcome = 0.0 if tp1_hit else -1.0; break
                if exit_mode == "fixed2R" and b["low"] <= tp2:
                    outcome = 2.0; break
                if exit_mode == "tp1_be":
                    if not tp1_hit and b["low"] <= tp1:
                        tp1_hit = True
                    if tp1_hit and b["low"] <= tp2:
                        outcome = 2.0; break
                if exit_mode == "time10" and j - i >= TIME_BARS:
                    outcome = (entry - b["close"]) / risk; break
        if outcome is None:
            last = ohlc[-1]["close"]
            outcome = ((last - entry) if long else (entry - last)) / risk
        trades.append((outcome, risk, aligned, i))
    return trades


def net_R(trades, cost_price):
    return [g - (cost_price / r if r > 0 else 0.0) for (g, r, *_ ) in trades]


def metrics(rlist):
    n = len(rlist)
    if n == 0:
        return None
    wins = [o for o in rlist if o > 0]
    gross_w = sum(wins); gross_l = -sum(o for o in rlist if o < 0)
    pf = (gross_w / gross_l) if gross_l > 0 else float("inf")
    return {"trades": n, "win_rate": len(wins) / n, "avg_R": statistics.mean(rlist),
            "pf": pf, "total_R": sum(rlist)}


def run():
    rows = []
    print(f"\nBacktest - NET of costs - with TREND filter, tested OUT-OF-SAMPLE\n")
    for inst in INSTRUMENTS:
        cost = COSTS.get(inst, {"spread": 0.0, "commission": 0.0})
        cp = cost["spread"] + cost["commission"]
        for tf in TFS:
            ohlc = fetch(inst, tf)
            if len(ohlc) < 60:
                print(f"  {inst:5} {tf:4}  thin data ({len(ohlc)}) - skipped"); continue
            dirs = trend_dirs(ohlc)
            cutoff = len(ohlc) // 2
            for ex in EXITS:
                trades = simulate(ohlc, ex, dirs)
                aligned = [t for t in trades if t[2]]
                oos = [t for t in aligned if t[3] >= cutoff]
                m_all = metrics(net_R(trades, cp))
                m_flt = metrics(net_R(aligned, cp))
                m_oos = metrics(net_R(oos, cp))
                if not m_all or not m_flt:
                    continue
                passes = (m_flt["avg_R"] > 0 and m_flt["pf"] > 1 and m_oos and m_oos["avg_R"] > 0)
                rows.append({"inst": inst, "tf": tf, "exit": ex,
                             "all_n": m_all["trades"], "all_R": m_all["avg_R"],
                             "flt_n": m_flt["trades"], "flt_R": m_flt["avg_R"], "flt_pf": m_flt["pf"],
                             "oos_n": (m_oos or {}).get("trades", 0), "oos_R": (m_oos or {}).get("avg_R"),
                             "passes": passes})
                oosR = f"{m_oos['avg_R']:+.3f}" if m_oos else "   n/a"
                flag = "PASS" if passes else "    "
                print(f"  {flag} {inst:5} {tf:4} {ex:8}  unfilt netR={m_all['avg_R']:+.3f}(n={m_all['trades']})"
                      f"  |  filtered netR={m_flt['avg_R']:+.3f}(n={m_flt['trades']})"
                      f"  |  OOS netR={oosR}(n={(m_oos or {}).get('trades',0)})")
    write_html(rows)
    passed = [r for r in rows if r["passes"]]
    print(f"\nDone. {len(passed)} of {len(rows)} combos PASS: filter positive AND still positive out-of-sample.")
    if not passed:
        print("None survived out-of-sample. Honest read: the trend filter does not rescue the rule -\n"
              "it mostly just trades less of the same negative edge. That's the #4 answer, with evidence.")
    else:
        print("Survivors worth a closer look (check the sample size isn't tiny):")
        for r in passed:
            print(f"   {r['inst']} {r['tf']} {r['exit']}: filtered netR={r['flt_R']:+.3f}, OOS netR={r['oos_R']:+.3f}, OOS n={r['oos_n']}")
    print("\nValidation, not financial advice. A PASS on limited history is a hint, not a guarantee.")


def write_html(rows):
    trs = ""
    for r in sorted(rows, key=lambda x: (not x["passes"], -(x["flt_R"]))):
        color = "#3fb98f" if r["passes"] else "#e5654b"
        pf = "inf" if r["flt_pf"] == float("inf") else f"{r['flt_pf']:.2f}"
        oosR = f"{r['oos_R']:+.3f}" if r["oos_R"] is not None else "n/a"
        trs += (f"<tr><td>{r['inst']}</td><td>{r['tf']}</td><td>{r['exit']}</td>"
                f"<td style='color:#8b97a6'>{r['all_R']:+.3f}</td><td style='color:#8b97a6'>{r['all_n']}</td>"
                f"<td style='color:{color}'>{r['flt_R']:+.3f}</td><td>{r['flt_n']}</td><td style='color:{color}'>{pf}</td>"
                f"<td style='color:{color}'>{oosR}</td><td>{r['oos_n']}</td></tr>")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Backtest - bias filter OOS</title>"
            "<style>body{background:#0e141c;color:#e8edf2;font-family:system-ui;padding:24px}"
            "h1{font-size:18px} .note{color:#8b97a6;font-size:13px;max-width:860px;line-height:1.6}"
            "table{border-collapse:collapse;margin-top:16px;font-family:ui-monospace,monospace;font-size:13px}"
            "th,td{padding:7px 12px;border-bottom:1px solid #26313f;text-align:right}"
            "th{color:#8b97a6;text-transform:uppercase;font-size:10px;letter-spacing:.08em}"
            "td:first-child,td:nth-child(2),td:nth-child(3){text-align:left}</style></head>"
            "<body><h1>Sweep-reclaim + trend filter - net of costs, out-of-sample &middot; " + stamp + "</h1>"
            "<p class='note'>Green = trend-filtered rule is positive (avgR&gt;0, PF&gt;1) AND still positive on the "
            "out-of-sample later half it wasn't judged on. 'Unfilt netR' is the rule without the filter, for comparison. "
            "The filter tests only the price/trend core of the bias (breadth has no historical record). "
            "Conservative, costs included. Validation only - not financial advice.</p>"
            "<table><tr><th>Instr</th><th>TF</th><th>Exit</th><th>Unfilt netR</th><th>n</th>"
            "<th>Filtered netR</th><th>n</th><th>PF</th><th>OOS netR</th><th>OOS n</th></tr>"
            + trs + "</table></body></html>")
    with open("backtest_report.html", "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    run()
