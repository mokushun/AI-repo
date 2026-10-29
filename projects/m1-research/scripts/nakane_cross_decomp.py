#!/usr/bin/env python3
"""Why does GBP/JPY look as strong as USD/JPY in the nakane window?
Decomposition on m1.db (2026-10-29). Same trades as nakane_5pairs_firstpass.py:
IN JST 00:00 -> OUT 09:55 (Thu 05:35), exit Tue-Fri, through 2026-10-22.
Prices: bar-open MID = (bo+ao)/2 (decomposition is about price moves, not spread).
Returns in log bp. Cross identity: XXX/JPY = XXX/USD x USD/JPY,
CHF/JPY = USD/JPY / USD/CHF, so bp returns add/subtract.
Also: correlation of each cross pair's trade returns with USD/JPY's,
and the XXX/USD leg's own mean/std (how noisy the non-fixing leg is
during the Tokyo-morning window).
"""
import os, sqlite3, statistics, datetime, math
DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
JST = datetime.timezone(datetime.timedelta(hours=9))
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
INSTS = ["USD_JPY","GBP_JPY","GBP_USD","EUR_JPY","EUR_USD","AUD_JPY","AUD_USD","CHF_JPY","USD_CHF"]
def load(inst):
    d = {}
    for t, bo, ao in con.execute(
        "SELECT t,bo,ao FROM m1 WHERE inst=? AND CAST(((t/60+540)%1440) AS INT) IN (0,335,595) ORDER BY t", (inst,)):
        dt = datetime.datetime.fromtimestamp(t, JST)
        d.setdefault(dt.date(), {})[dt.hour*60+dt.minute] = (bo+ao)/2
    return d
data = {i: load(i) for i in INSTS}
cutoff = datetime.date(2026, 10, 23)
def rets(inst):
    out = {}
    for d, mins in data[inst].items():
        if d.weekday() not in (1,2,3,4) or d >= cutoff: continue
        ex = 335 if d.weekday() == 3 else 595
        if 0 in mins and ex in mins:
            out[d] = math.log(mins[ex]/mins[0]) * 10000  # bp
    return out
R = {i: rets(i) for i in INSTS}
def st(v):
    n=len(v); m=statistics.mean(v); sd=statistics.pstdev(v)
    return f"n={n} mean={m:+.2f}bp std={sd:.1f}bp t={m/(sd/math.sqrt(n)):.2f}"
print("== trade returns (mid, log bp) ==")
for i in ["USD_JPY","GBP_JPY","EUR_JPY","AUD_JPY","CHF_JPY"]:
    print(f"{i}: {st(list(R[i].values()))}")
print()
print("== non-JPY leg during the same window (mid, log bp) ==")
for i in ["GBP_USD","EUR_USD","AUD_USD","USD_CHF"]:
    print(f"{i}: {st(list(R[i].values()))}")
print()
print("== decomposition: cross = USD/JPY leg +/- XXX/USD leg (common dates) ==")
def corr(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys)) / math.sqrt(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))
for cross, leg, sign in [("GBP_JPY","GBP_USD",+1),("EUR_JPY","EUR_USD",+1),("AUD_JPY","AUD_USD",+1),("CHF_JPY","USD_CHF",-1)]:
    ds = sorted(set(R[cross]) & set(R["USD_JPY"]) & set(R[leg]))
    resid = [R[cross][d] - (R["USD_JPY"][d] + sign*R[leg][d]) for d in ds]
    own   = [R[cross][d] - R["USD_JPY"][d] for d in ds]  # cross minus USD/JPY leg = leg contribution
    print(f"{cross}: corr with USD_JPY = {corr([R[cross][d] for d in ds],[R['USD_JPY'][d] for d in ds]):.3f} | "
          f"identity residual mean={statistics.mean(resid):+.3f}bp | "
          f"leg contribution ({leg}) mean={statistics.mean(own):+.2f}bp t={statistics.mean(own)/(statistics.pstdev(own)/math.sqrt(len(own))):.2f}")
con.close()
