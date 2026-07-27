"""
Backtest the 1H sweep-reclaim rule — the SAME rule your live Signals page uses
(imported from app.signals.all_signals, so there's no divergence between what's
tested and what you trade).

Runs every instrument x timeframe x exit method and reports:
  trades, win%, avg R (expectancy), profit factor, max drawdown (R), total R.

METHOD / HONESTY:
- Data: Yahoo history (as far back as each interval allows) — gives enough bars
  for statistics. Runs on THIS machine (needs internet to Yahoo).
- Within-bar ambiguity: if one bar could hit BOTH stop and target, we count it as
  the STOP (worst case). Results are therefore conservative, not flattered.
- Trades still open at the end of the data are marked to the last close.
- This tells you whether the rule has an EDGE. It is not a promise of future
  results and is not financial advice.

Run:  venv\\Scripts\\python backtest.py
Output: a table in the console + a backtest_report.html you can open.
"""
import sys, statistics, webbrowser, os
from app import signals as S

INSTRUMENTS = ["US100", "SP500", "US30", "XAU", "XAG"]
TFS = ["5m", "15m", "1h", "4h", "1d"]
EXITS = ["fixed2R", "tp1_be", "time10"]

# Max Yahoo history per interval (yfinance caps intraday history)
BT_PERIOD = {"1m": "7d", "5m": "60d", "15m": "60d", "1h": "730d", "4h": "730d", "1d": "5y"}
TIME_BARS = 10  # for the time-based exit

# --- trading costs (EDIT to match your XM account) --------------------------
# 'spread' is the round-trip cost in the instrument's PRICE units; 'commission'
# is any extra round-trip cost in price units (XM index/metal CFDs are usually
# spread-only, so commission defaults to 0). Cost is converted to R per trade
# using that trade's own risk — tight stops pay proportionally more.
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


def simulate(ohlc, exit_mode):
    """Return list of (gross_R, risk_price) per trade."""
    sigs = S.all_signals(ohlc)
    n = len(ohlc)
    trades = []
    for sg in sigs:
        i = sg["i"]; entry = sg["entry"]; sl = sg["sl"]; tp1 = sg["tp1"]; tp2 = sg["tp2"]
        long = sg["signal"] == "long"
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        tp1_hit = False
        outcome = None
        for j in range(i + 1, n):
            b = ohlc[j]
            if long:
                stop_level = entry if tp1_hit else sl
                if b["low"] <= stop_level:
                    outcome = 0.0 if tp1_hit else -1.0
                    break
                if exit_mode == "fixed2R":
                    if b["high"] >= tp2:
                        outcome = 2.0; break
                elif exit_mode == "tp1_be":
                    if not tp1_hit and b["high"] >= tp1:
                        tp1_hit = True
                    if tp1_hit and b["high"] >= tp2:
                        outcome = 2.0; break
                elif exit_mode == "time10":
                    if j - i >= TIME_BARS:
                        outcome = (b["close"] - entry) / risk; break
            else:
                stop_level = entry if tp1_hit else sl
                if b["high"] >= stop_level:
                    outcome = 0.0 if tp1_hit else -1.0
                    break
                if exit_mode == "fixed2R":
                    if b["low"] <= tp2:
                        outcome = 2.0; break
                elif exit_mode == "tp1_be":
                    if not tp1_hit and b["low"] <= tp1:
                        tp1_hit = True
                    if tp1_hit and b["low"] <= tp2:
                        outcome = 2.0; break
                elif exit_mode == "time10":
                    if j - i >= TIME_BARS:
                        outcome = (entry - b["close"]) / risk; break
        if outcome is None:
            last = ohlc[-1]["close"]
            outcome = ((last - entry) if long else (entry - last)) / risk
        trades.append((outcome, risk))
    return trades


def apply_costs(trades, cost_price):
    """Subtract cost (in R) from each trade: cost_R = cost_price / risk_price."""
    return [g - (cost_price / r if r > 0 else 0.0) for g, r in trades]


def metrics(outcomes):
    n = len(outcomes)
    if n == 0:
        return None
    wins = [o for o in outcomes if o > 0]
    losses = [o for o in outcomes if o < 0]
    gross_w = sum(wins); gross_l = -sum(losses)
    pf = (gross_w / gross_l) if gross_l > 0 else float("inf")
    # max drawdown on cumulative R equity
    eq = 0.0; peak = 0.0; mdd = 0.0
    for o in outcomes:
        eq += o; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    return {"trades": n, "win_rate": len(wins) / n, "avg_R": statistics.mean(outcomes),
            "pf": pf, "total_R": sum(outcomes), "max_dd": mdd}


def run():
    rows = []
    print(f"\nBacktesting sweep-reclaim across {len(INSTRUMENTS)} instruments x "
          f"{len(TFS)} timeframes x {len(EXITS)} exits — NET of costs...\n")
    for inst in INSTRUMENTS:
        cost = COSTS.get(inst, {"spread": 0.0, "commission": 0.0})
        cost_price = cost["spread"] + cost["commission"]
        for tf in TFS:
            ohlc = fetch(inst, tf)
            if len(ohlc) < 40:
                print(f"  {inst:5} {tf:4}  no/thin data ({len(ohlc)} bars) — skipped")
                continue
            for ex in EXITS:
                trades = simulate(ohlc, ex)
                mg = metrics([t[0] for t in trades])            # gross
                mn = metrics(apply_costs(trades, cost_price))    # net of costs
                if not mn:
                    continue
                rows.append({"inst": inst, "tf": tf, "exit": ex, "bars": len(ohlc),
                             "gross_avgR": mg["avg_R"], "gross_pf": mg["pf"], **mn})
                flag = "EDGE" if (mn["avg_R"] > 0 and mn["pf"] > 1) else "    "
                print(f"  {flag} {inst:5} {tf:4} {ex:8}  n={mn['trades']:4}  win={mn['win_rate']*100:4.1f}%  "
                      f"grossR={mg['avg_R']:+.3f} -> netR={mn['avg_R']:+.3f}  PF={mn['pf']:.2f}  totR={mn['total_R']:+.1f}")
    write_html(rows)
    survivors = [r for r in rows if r["avg_R"] > 0 and r["pf"] > 1]
    print(f"\nDone. {len(survivors)} of {len(rows)} combos still positive AFTER costs.")
    print("Open  backtest_report.html  for the sorted table (gross vs net).")
    print("\nREAD HONESTLY: 'netR' is the realistic number. A row that was green gross "
          "but red net was an illusion created by ignoring the spread. Validation, not advice.")


def write_html(rows):
    def cell(m):
        return m
    trs = ""
    for r in sorted(rows, key=lambda x: (-(x["avg_R"]), x["inst"])):
        good = r["avg_R"] > 0 and r["pf"] > 1
        color = "#3fb98f" if good else "#e5654b"
        pf = "∞" if r["pf"] == float("inf") else f"{r['pf']:.2f}"
        gpf = "∞" if r["gross_pf"] == float("inf") else f"{r['gross_pf']:.2f}"
        trs += (f"<tr><td>{r['inst']}</td><td>{r['tf']}</td><td>{r['exit']}</td>"
                f"<td>{r['trades']}</td><td>{r['win_rate']*100:.1f}%</td>"
                f"<td style='color:#8b97a6'>{r['gross_avgR']:+.3f}</td>"
                f"<td style='color:{color}'>{r['avg_R']:+.3f}</td>"
                f"<td style='color:{color}'>{pf}</td><td>{r['total_R']:+.1f}</td>"
                f"<td>{r['max_dd']:.1f}</td></tr>")
    import datetime
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Backtest</title>
<style>body{{background:#0e141c;color:#e8edf2;font-family:system-ui;padding:24px}}
h1{{font-size:18px}} .note{{color:#8b97a6;font-size:13px;max-width:820px;line-height:1.6}}
table{{border-collapse:collapse;margin-top:16px;font-family:ui-monospace,monospace;font-size:13px}}
th,td{{padding:7px 12px;border-bottom:1px solid #26313f;text-align:right}}
th{{color:#8b97a6;text-transform:uppercase;font-size:10px;letter-spacing:.08em}}
td:first-child,td:nth-child(2),td:nth-child(3){{text-align:left}}</style></head>
<body><h1>Sweep-reclaim backtest — NET of costs — sorted by expectancy · {stamp}</h1>
<p class="note">Green = positive expectancy &amp; profit factor &gt; 1 <b>after spread/commission</b> (conservative, stop-first).
'Gross R' is before costs, shown so you can see what the spread removed. A row green in Gross but red in Net R was an
illusion of ignoring costs. Rows still open at data end are marked to last close. Validation only — not financial advice.</p>
<table><tr><th>Instr</th><th>TF</th><th>Exit</th><th>Trades</th><th>Win%</th><th>Gross R</th><th>Net R</th><th>PF</th><th>Total R</th><th>Max DD</th></tr>
{trs}</table></body></html>"""
    with open("backtest_report.html", "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    run()
