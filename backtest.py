"""
Backtest: does the DEEP BIAS filter signals better than the simple TREND filter?

Compares three ways of taking the sweep-reclaim signal, NET of costs, OUT-OF-SAMPLE:
  1) unfiltered          - every signal
  2) trend-filtered      - only signals aligned with EMA20/50 (already validated)
  3) deep-filtered       - only signals aligned with the price-based DEEP BIAS
                           (Trend category + Momentum category, weighted 1.0/0.7,
                           exactly as the live panel weights them)

HONEST SCOPE: the deep bias's Breadth (Participation) category CANNOT be
backtested - there's no historical record of the 99-constituent snapshots. True
cross-feed multi-timeframe also collapses on a single price series. So this tests
the PRICE-BASED deep bias (trend + momentum). If deep beats trend out-of-sample,
the extra indicators earn their keep; if not, the simple trend filter is enough
and the deep bias is prettier, not more predictive.

Conservative (stop-first), costs subtracted per trade in R, causal (no lookahead).
Validation, not financial advice.

Run:  venv/Scripts/python backtest.py   ->  console table + backtest_report.html
"""
import statistics, datetime
from app import signals as S

INSTRUMENTS = ["US100", "SP500", "US30", "XAU", "XAG"]
TFS = ["5m", "15m", "1h", "4h", "1d"]
EXITS = ["fixed2R", "tp1_be", "time10"]
BT_PERIOD = {"1m": "7d", "5m": "60d", "15m": "60d", "1h": "730d", "4h": "730d", "1d": "5y"}
TIME_BARS = 10
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


# ----------------------- indicator SERIES (causal) -----------------------
def _ema_series(v, p):
    k = 2 / (p + 1); out = []; e = None
    for x in v:
        e = x if e is None else x * k + e * (1 - k); out.append(e)
    return out


def _rsi_series(c, n=14):
    out = [None] * len(c)
    if len(c) < n + 1:
        return out
    g = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    l = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag = sum(g[:n]) / n; al = sum(l[:n]) / n
    out[n] = 100 - 100 / (1 + (ag / al if al else 999))
    for i in range(n + 1, len(c)):
        ag = (ag * (n - 1) + g[i - 1]) / n; al = (al * (n - 1) + l[i - 1]) / n
        out[i] = 100 - 100 / (1 + (ag / al if al else 999))
    return out


def _macd_series(c):
    e12 = _ema_series(c, 12); e26 = _ema_series(c, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    return macd, _ema_series(macd, 9)


def _stoch_series(h, l, c, n=14):
    out = [None] * len(c)
    for i in range(n - 1, len(c)):
        hh = max(h[i - n + 1:i + 1]); ll = min(l[i - n + 1:i + 1])
        out[i] = 50.0 if hh == ll else (c[i] - ll) / (hh - ll) * 100
    return out


def _williams_series(h, l, c, n=14):
    out = [None] * len(c)
    for i in range(n - 1, len(c)):
        hh = max(h[i - n + 1:i + 1]); ll = min(l[i - n + 1:i + 1])
        out[i] = -50.0 if hh == ll else (hh - c[i]) / (hh - ll) * -100
    return out


def _cci_series(h, l, c, n=20):
    out = [None] * len(c)
    tp = [(h[i] + l[i] + c[i]) / 3 for i in range(len(c))]
    for i in range(n - 1, len(c)):
        sma = sum(tp[i - n + 1:i + 1]) / n
        md = sum(abs(x - sma) for x in tp[i - n + 1:i + 1]) / n
        out[i] = 0.0 if md == 0 else (tp[i] - sma) / (0.015 * md)
    return out


def _roc_series(c, n=10):
    out = [None] * len(c)
    for i in range(n, len(c)):
        out[i] = None if c[i - n] == 0 else (c[i] / c[i - n] - 1) * 100
    return out


def _bollinger_series(c, n=20, k=2):
    out = [None] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]; mid = sum(w) / n
        sd = (sum((x - mid) ** 2 for x in w) / n) ** 0.5
        out[i] = 0.5 if sd == 0 else (c[i] - (mid - k * sd)) / (2 * k * sd)
    return out


def trend_dirs(ohlc):
    c = [b["close"] for b in ohlc]
    e20 = _ema_series(c, 20); e50 = _ema_series(c, 50)
    d = []
    for i in range(len(c)):
        if i < 50:
            d.append("neutral"); continue
        if c[i] > e50[i] and e20[i] > e50[i]:
            d.append("bull")
        elif c[i] < e50[i] and e20[i] < e50[i]:
            d.append("bear")
        else:
            d.append("neutral")
    return d


def deep_dirs(ohlc):
    """Per-bar direction from the price-based deep bias: Trend (weight 1.0) +
    Momentum (weight 0.7), same as the live panel. Causal, no lookahead."""
    h = [b["high"] for b in ohlc]; l = [b["low"] for b in ohlc]; c = [b["close"] for b in ohlc]
    e20 = _ema_series(c, 20); e50 = _ema_series(c, 50); e200 = _ema_series(c, 200)
    rsi = _rsi_series(c); macd, sig = _macd_series(c)
    st = _stoch_series(h, l, c); wr = _williams_series(h, l, c)
    cci = _cci_series(h, l, c); roc = _roc_series(c); bb = _bollinger_series(c)

    def catmean(votes):
        nz = [v for v in votes if v != 0]
        return sum(nz) / len(nz) if nz else 0.0

    out = []
    for i in range(len(c)):
        if i < 60:
            out.append("neutral"); continue
        tv = [1 if c[i] > e20[i] else -1, 1 if e20[i] > e50[i] else -1]
        if i >= 200:
            tv.append(1 if c[i] > e200[i] else -1)
        if bb[i] is not None:
            tv.append(1 if bb[i] > 0.55 else -1 if bb[i] < 0.45 else 0)
        mv = []
        if rsi[i] is not None:
            mv.append(1 if rsi[i] >= 55 else -1 if rsi[i] <= 45 else 0)
        if macd[i] is not None and sig[i] is not None:
            mv.append(1 if macd[i] > sig[i] else -1)
        if st[i] is not None:
            mv.append(1 if st[i] > 55 else -1 if st[i] < 45 else 0)
        if cci[i] is not None:
            mv.append(1 if cci[i] > 0 else -1)
        if wr[i] is not None:
            mv.append(1 if wr[i] > -50 else -1)
        if roc[i] is not None:
            mv.append(1 if roc[i] > 0 else -1)
        deep = (1.0 * catmean(tv) + 0.7 * catmean(mv)) / 1.7
        out.append("bull" if deep > 0.15 else "bear" if deep < -0.15 else "neutral")
    return out


def simulate(ohlc, exit_mode, tdirs, ddirs):
    """trades: (gross_R, risk, trend_aligned, deep_aligned, index)."""
    sigs = S.all_signals(ohlc)
    n = len(ohlc)
    trades = []
    for sg in sigs:
        i = sg["i"]; entry = sg["entry"]; sl = sg["sl"]; tp1 = sg["tp1"]; tp2 = sg["tp2"]
        long = sg["signal"] == "long"
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        t_al = (long and tdirs[i] == "bull") or ((not long) and tdirs[i] == "bear")
        d_al = (long and ddirs[i] == "bull") or ((not long) and ddirs[i] == "bear")
        tp1_hit = False; outcome = None
        for j in range(i + 1, n):
            b = ohlc[j]
            if long:
                stop = entry if tp1_hit else sl
                if b["low"] <= stop:
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
                stop = entry if tp1_hit else sl
                if b["high"] >= stop:
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
        trades.append((outcome, risk, t_al, d_al, i))
    return trades


def net_R(trades, cp):
    return [g - (cp / r if r > 0 else 0.0) for (g, r, *_ ) in trades]


def metrics(rl):
    n = len(rl)
    if n == 0:
        return None
    wins = [o for o in rl if o > 0]
    gl = -sum(o for o in rl if o < 0)
    pf = (sum(wins) / gl) if gl > 0 else float("inf")
    return {"n": n, "avg_R": statistics.mean(rl), "pf": pf, "win": len(wins) / n}


def run():
    rows = []
    print("\nBacktest: DEEP BIAS filter vs TREND filter - net of costs, out-of-sample\n")
    for inst in INSTRUMENTS:
        cost = COSTS.get(inst, {"spread": 0.0, "commission": 0.0})
        cp = cost["spread"] + cost["commission"]
        for tf in TFS:
            ohlc = fetch(inst, tf)
            if len(ohlc) < 80:
                print(f"  {inst:5} {tf:4}  thin data ({len(ohlc)}) - skipped"); continue
            td = trend_dirs(ohlc); dd = deep_dirs(ohlc)
            cut = len(ohlc) // 2
            for ex in EXITS:
                tr = simulate(ohlc, ex, td, dd)
                def oos(sel):
                    return [t for t in tr if sel(t) and t[4] >= cut]
                m_all = metrics(net_R(tr, cp))
                m_tr = metrics(net_R([t for t in tr if t[2]], cp))
                m_dp = metrics(net_R([t for t in tr if t[3]], cp))
                o_tr = metrics(net_R(oos(lambda t: t[2]), cp))
                o_dp = metrics(net_R(oos(lambda t: t[3]), cp))
                if not m_all:
                    continue
                deep_wins = (o_dp and o_tr and o_dp["avg_R"] > o_tr["avg_R"] and o_dp["avg_R"] > 0)
                rows.append({"inst": inst, "tf": tf, "exit": ex,
                             "all_R": m_all["avg_R"], "all_n": m_all["n"],
                             "tr_R": (m_tr or {}).get("avg_R"), "tr_oos": (o_tr or {}).get("avg_R"), "tr_oosn": (o_tr or {}).get("n", 0),
                             "dp_R": (m_dp or {}).get("avg_R"), "dp_oos": (o_dp or {}).get("avg_R"), "dp_oosn": (o_dp or {}).get("n", 0),
                             "deep_wins": deep_wins})
                def f(x):
                    return f"{x:+.3f}" if x is not None else "  n/a"
                flag = "DEEP>TREND" if deep_wins else "         "
                print(f"  {flag} {inst:5} {tf:4} {ex:8}  unfilt={f(m_all['avg_R'])}  |  "
                      f"trend OOS={f((o_tr or {}).get('avg_R'))}(n={(o_tr or {}).get('n',0)})  |  "
                      f"deep OOS={f((o_dp or {}).get('avg_R'))}(n={(o_dp or {}).get('n',0)})")
    write_html(rows)
    # verdict
    comp = [r for r in rows if r["tr_oos"] is not None and r["dp_oos"] is not None and r["dp_oosn"] >= 20 and r["tr_oosn"] >= 20]
    deep_better = [r for r in comp if r["dp_oos"] > r["tr_oos"] and r["dp_oos"] > 0]
    print(f"\nComparable combos (both OOS n>=20): {len(comp)}")
    print(f"Deep bias beat the trend filter out-of-sample in: {len(deep_better)} of {len(comp)}")
    if comp:
        avg_tr = statistics.mean(r["tr_oos"] for r in comp)
        avg_dp = statistics.mean(r["dp_oos"] for r in comp)
        print(f"Average OOS avg-R across those combos:  trend={avg_tr:+.3f}   deep={avg_dp:+.3f}")
        if avg_dp > avg_tr + 0.02 and len(deep_better) > len(comp) * 0.6:
            print("VERDICT: the deep bias filter improves on the trend filter. The extra indicators earn their keep.")
        elif avg_dp < avg_tr - 0.02:
            print("VERDICT: the deep bias filter is WORSE than the simple trend filter. Keep the simple trend filter;\n         the extra indicators add noise, not edge (as warned - momentum is price wearing many hats).")
        else:
            print("VERDICT: deep and trend filters are ~equivalent out-of-sample. The deep bias is not measurably\n         better as a FILTER - keep it for the richer read, but the simple trend filter is enough for signals.")
    print("\nValidation, not financial advice. Breadth (Participation) is excluded - it has no historical record.")


def write_html(rows):
    trs = ""
    for r in sorted(rows, key=lambda x: (not x["deep_wins"], -((x["dp_oos"] or -9)))):
        col = "#3fb98f" if r["deep_wins"] else "#8b97a6"
        def f(x):
            return f"{x:+.3f}" if x is not None else "n/a"
        trs += (f"<tr><td>{r['inst']}</td><td>{r['tf']}</td><td>{r['exit']}</td>"
                f"<td style='color:#8b97a6'>{f(r['all_R'])}</td>"
                f"<td>{f(r['tr_oos'])}</td><td>{r['tr_oosn']}</td>"
                f"<td style='color:{col}'>{f(r['dp_oos'])}</td><td>{r['dp_oosn']}</td>"
                f"<td style='color:{col}'>{'YES' if r['deep_wins'] else ''}</td></tr>")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Deep bias vs trend filter</title>"
            "<style>body{background:#0e141c;color:#e8edf2;font-family:system-ui;padding:24px}"
            "h1{font-size:18px}.note{color:#8b97a6;font-size:13px;max-width:900px;line-height:1.6}"
            "table{border-collapse:collapse;margin-top:16px;font-family:ui-monospace,monospace;font-size:13px}"
            "th,td{padding:7px 12px;border-bottom:1px solid #26313f;text-align:right}"
            "th{color:#8b97a6;text-transform:uppercase;font-size:10px;letter-spacing:.08em}"
            "td:first-child,td:nth-child(2),td:nth-child(3){text-align:left}</style></head>"
            "<body><h1>Does the deep bias filter beat the trend filter? &middot; out-of-sample, net of costs &middot; " + stamp + "</h1>"
            "<p class='note'>Green / YES = the deep-bias filter had higher out-of-sample avg-R than the simple trend filter "
            "AND was positive. 'Unfilt' is no filter. Only price-based deep bias (Trend+Momentum) is tested - breadth has no "
            "historical record. If few rows are green, the simple trend filter is enough and the deep bias is a richer read, "
            "not a better signal filter. Validation only - not financial advice.</p>"
            "<table><tr><th>Instr</th><th>TF</th><th>Exit</th><th>Unfilt R</th>"
            "<th>Trend OOS R</th><th>n</th><th>Deep OOS R</th><th>n</th><th>Deep&gt;Trend</th></tr>"
            + trs + "</table></body></html>")
    with open("backtest_report.html", "w", encoding="utf-8") as fp:
        fp.write(html)


if __name__ == "__main__":
    run()
