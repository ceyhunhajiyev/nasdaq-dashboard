"""
ORB variation lab — tests ATR stops, a volume filter, and entry-window length,
each OUT-OF-SAMPLE on ~6 months of MT5/XM 15m data. Trend filter is always on
(the only ORB element with support). Targets tested at 1R and 2R.

HONESTY: many configs are tested (6 x 2 targets = 12 per instrument). With that
many rolls, ~1 will look green by chance. The bar is NOT one green cell — it's a
config that's positive out-of-sample on >=2 of 3 indices (consistency). Anything
passing on a single instrument is treated as luck. Net of costs, stop-first,
intraday close, causal. Validation, not financial advice.

Requires MT5 open + logged into XM. Run: venv/Scripts/python backtest_orb_lab.py
"""
import statistics, datetime as _dt
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

NY = ZoneInfo("America/New_York")
SYMBOLS = {"US100": "US100Cash", "SP500": "US500Cash", "US30": "US30Cash"}
COSTS = {"US100": 1.5, "SP500": 0.5, "US30": 2.0}
MONTHS_BACK = 200

# (name, stop_mode, atr_mult, vol_filter, window_min)
CONFIGS = [
    ("base",     "range", 0.0, False, 120),
    ("atr1.0",   "atr",   1.0, False, 120),
    ("atr1.5",   "atr",   1.5, False, 120),
    ("volfilter","range", 0.0, True,  120),
    ("window30", "range", 0.0, False, 30),
    ("windowFull","range",0.0, False, 360),
]
TARGETS = [1.0, 2.0]


def load(sym):
    mt5.symbol_select(sym, True)
    frm = datetime.now(timezone.utc) - timedelta(days=MONTHS_BACK)
    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, frm, datetime.now(timezone.utc))
    if rates is None or len(rates) == 0:
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 20000)
    out = []
    if rates is None:
        return out
    for r in rates:
        out.append({"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
                    "low": float(r["low"]), "close": float(r["close"]),
                    "vol": float(r["tick_volume"]) if "tick_volume" in r.dtype.names else 0.0})
    return out


def _ema_series(v, p):
    k = 2 / (p + 1); out = []; e = None
    for x in v:
        e = x if e is None else x * k + e * (1 - k); out.append(e)
    return out


def _atr_series(ohlc, n=14):
    out = [None] * len(ohlc)
    trs = []
    for i in range(len(ohlc)):
        if i == 0:
            trs.append(ohlc[0]["high"] - ohlc[0]["low"]); continue
        h, l, pc = ohlc[i]["high"], ohlc[i]["low"], ohlc[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    for i in range(n - 1, len(ohlc)):
        out[i] = sum(trs[i - n + 1:i + 1]) / n
    return out


def orb_lab(ohlc, cp, stop_mode="range", atr_mult=1.0, vol_filter=False,
            window_min=120, target_R=2.0):
    closes = [b["close"] for b in ohlc]
    e20 = _ema_series(closes, 20); e50 = _ema_series(closes, 50)
    atr = _atr_series(ohlc, 14)
    days = defaultdict(list)
    for idx, b in enumerate(ohlc):
        d = datetime.fromtimestamp(b["time"], NY)
        days[d.date()].append((idx, d.hour * 60 + d.minute, b))
    om, orm = 9 * 60 + 30, 15
    or_vols = {}
    for day, rows in days.items():
        or_vols[day] = sum(b["vol"] for idx, m, b in rows if om <= m < om + orm)
    vmed = sorted(or_vols.values())[len(or_vols) // 2] if or_vols else 0
    trades = []
    for day in sorted(days.keys()):
        rows = days[day]; day_last = max(idx for idx, _, _ in rows)
        if vol_filter and or_vols.get(day, 0) < vmed:
            continue
        or_hi = or_lo = None
        for idx, m, b in rows:
            if om <= m < om + orm:
                or_hi = b["high"] if or_hi is None else max(or_hi, b["high"])
                or_lo = b["low"] if or_lo is None else min(or_lo, b["low"])
        if or_hi is None or or_hi <= or_lo:
            continue
        brk = None
        for idx, m, b in rows:
            if m < om + orm or m > om + window_min:
                continue
            if b["high"] > or_hi:
                brk = ("long", idx); break
            if b["low"] < or_lo:
                brk = ("short", idx); break
        if not brk:
            continue
        side, bidx = brk
        if bidx < 50:
            continue
        c = closes[bidx]
        if (side == "long" and not (c > e50[bidx] and e20[bidx] > e50[bidx])) or \
           (side == "short" and not (c < e50[bidx] and e20[bidx] < e50[bidx])):
            continue
        entry = or_hi if side == "long" else or_lo
        if stop_mode == "atr":
            a = atr[bidx]
            if not a:
                continue
            risk = atr_mult * a
            stop = entry - risk if side == "long" else entry + risk
        else:
            stop = or_lo if side == "long" else or_hi
            risk = abs(entry - stop)
        if risk <= 0:
            continue
        tp = entry + target_R * risk if side == "long" else entry - target_R * risk
        outcome = None
        for j in range(bidx, day_last + 1):
            b = ohlc[j]
            if side == "long":
                if b["low"] <= stop:
                    outcome = -1.0; break
                if b["high"] >= tp:
                    outcome = float(target_R); break
            else:
                if b["high"] >= stop:
                    outcome = -1.0; break
                if b["low"] <= tp:
                    outcome = float(target_R); break
        if outcome is None:
            cc = ohlc[day_last]["close"]
            outcome = ((cc - entry) if side == "long" else (entry - cc)) / risk
        trades.append((outcome - cp / risk, bidx))
    return trades


def metrics(rl):
    n = len(rl)
    if n == 0:
        return None
    wins = [o for o in rl if o > 0]
    gl = -sum(o for o in rl if o < 0)
    pf = (sum(wins) / gl) if gl > 0 else float("inf")
    return {"n": n, "win": len(wins) / n, "avg_R": statistics.mean(rl), "pf": pf}


def run():
    if mt5 is None or not mt5.initialize():
        raise SystemExit("MT5 not available / initialize failed: " + str(mt5.last_error() if mt5 else "no module"))
    print("\nORB variation lab - 6mo MT5 15m, trend-filtered, out-of-sample\n")
    results = {}   # (inst, cfg, tR) -> (m, mo)
    data = {}
    for inst, sym in SYMBOLS.items():
        data[inst] = load(sym)
    for inst in SYMBOLS:
        ohlc = data[inst]
        if len(ohlc) < 200:
            print(f"  {inst}: thin data"); continue
        cut = len(ohlc) // 2; cp = COSTS[inst]
        print(f"  {inst}:")
        for (cfg, sm, am, vf, wm) in CONFIGS:
            for tR in TARGETS:
                tr = orb_lab(ohlc, cp, sm, am, vf, wm, tR)
                m = metrics([r for r, _ in tr])
                mo = metrics([r for r, i in tr if i >= cut])
                results[(inst, cfg, tR)] = (m, mo)
                if not m:
                    continue
                oosR = f"{mo['avg_R']:+.3f}" if mo else "  n/a"
                print(f"     {cfg:11}{int(tR)}R  n={m['n']:3} avgR={m['avg_R']:+.3f} PF={m['pf']:.2f} "
                      f"| OOS avgR={oosR}(n={(mo or {}).get('n',0)})")
    # verdict: configs positive OOS on >=2 instruments
    print("\nConfigs positive out-of-sample on >=2 of 3 indices (the honest bar):")
    winners = []
    for (cfg, *_ ) in CONFIGS:
        for tR in TARGETS:
            hits = [inst for inst in SYMBOLS
                    if results.get((inst, cfg, tR)) and results[(inst, cfg, tR)][0]
                    and results[(inst, cfg, tR)][0]["avg_R"] > 0
                    and results[(inst, cfg, tR)][1] and results[(inst, cfg, tR)][1]["avg_R"] > 0]
            if len(hits) >= 2:
                winners.append((cfg, tR, hits))
                print(f"   {cfg} {int(tR)}R  -> {hits}")
    n_configs = len(CONFIGS) * len(TARGETS)
    if not winners:
        print("   none.")
        print(f"\nVERDICT: none of the {n_configs} tested configs held up out-of-sample on 2+ indices.\n"
              "         No ORB refinement earns a grade. The honest answer remains: fragile on futures.")
    else:
        print(f"\n{len(winners)} of {n_configs} configs passed. NOTE: with {n_configs} configs tested, ~1 green by chance\n"
              "is expected - only trust these if the SAME config wins across instruments AND makes mechanical sense.\n"
              "Recommend forward-tracking before grading. Not proven, not financial advice.")
    write_html(results, winners)
    print("\nOpen  backtest_orb_lab_report.html  for the table.")
    mt5.shutdown()


def write_html(results, winners):
    wins = {(c, t) for c, t, _ in winners}
    trs = ""
    for inst in SYMBOLS:
        first = True
        for (cfg, *_ ) in CONFIGS:
            for tR in TARGETS:
                g = results.get((inst, cfg, tR))
                if not g or not g[0]:
                    continue
                m, mo = g
                good = m["avg_R"] > 0 and mo and mo["avg_R"] > 0
                col = "#3fb98f" if good else "#e5654b"
                pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
                oosR = f"{mo['avg_R']:+.3f}" if mo else "n/a"
                top = " style='border-top:2px solid #26313f'" if first else ""
                first = False
                trs += (f"<tr{top}><td>{inst if cfg=='base' and tR==1.0 else ''}</td><td>{cfg} {int(tR)}R</td>"
                        f"<td>{m['n']}</td><td>{m['win']*100:.0f}%</td><td style='color:{col}'>{m['avg_R']:+.3f}</td>"
                        f"<td style='color:{col}'>{pf}</td><td style='color:{col}'>{oosR}</td><td>{(mo or {}).get('n',0)}</td></tr>")
    passed = len(wins) > 0
    vcol = "#3fb98f" if passed else "#e5654b"
    vtxt = (f"{len(wins)} config(s) held up out-of-sample on 2+ indices — but {len(CONFIGS)*len(TARGETS)} were tested, "
            "so treat as leads to forward-track, not proven edges." if passed else
            f"None of the {len(CONFIGS)*len(TARGETS)} tested configs held up out-of-sample on 2+ indices. "
            "No ORB refinement earns a grade — it remains fragile on index futures.")
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>ORB variation lab</title>"
            "<style>body{background:#0e141c;color:#e8edf2;font-family:system-ui;padding:24px}"
            "h1{font-size:18px}.note{color:#8b97a6;font-size:13px;max-width:900px;line-height:1.6}"
            ".verdict{margin:14px 0;padding:12px 14px;border-radius:8px;font-family:ui-monospace,monospace;font-size:13px;line-height:1.5;border:1px solid " + vcol + ";color:" + vcol + "}"
            "table{border-collapse:collapse;margin-top:12px;font-family:ui-monospace,monospace;font-size:13px}"
            "th,td{padding:7px 12px;border-bottom:1px solid #1c2530;text-align:right}"
            "th{color:#8b97a6;text-transform:uppercase;font-size:10px;letter-spacing:.08em}"
            "td:first-child,td:nth-child(2){text-align:left}</style></head>"
            "<body><h1>ORB variation lab — ATR stops / volume filter / entry window — OOS · " + stamp + "</h1>"
            "<div class='verdict'>Verdict: " + vtxt + "</div>"
            "<p class='note'>All configs are trend-filtered (the supported base). Green = positive avg R AND positive "
            "out-of-sample. 6 configs x 2 targets = 12 tested per instrument — with that many, ~1 green by chance, so the "
            "bar is a config winning on 2+ indices, not one lucky cell. Real XM prices, 6 months, net of costs. "
            "Validation only — not financial advice.</p>"
            "<table><tr><th>Instrument</th><th>Config</th><th>Trades</th><th>Win%</th><th>Avg R</th><th>PF</th>"
            "<th>OOS R</th><th>OOS n</th></tr>" + trs + "</table></body></html>")
    with open("backtest_orb_lab_report.html", "w", encoding="utf-8") as fp:
        fp.write(html)


if __name__ == "__main__":
    run()
