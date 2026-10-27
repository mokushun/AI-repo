import sqlite3, os
import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

def load(inst):
    df = pd.read_sql_query("SELECT t,bo,bh,bl,bc,ao,ah,al,ac FROM m1 WHERE inst=? ORDER BY t", con, params=(inst,))
    df["mid_c"] = (df.bc + df.ac) / 2
    df["jst"] = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo")
    df["hour"] = df.jst.dt.hour
    df["dow"] = df.jst.dt.dayofweek
    df["dt"] = df.t.diff()
    df["ret"] = df.mid_c.diff()
    df["logret"] = np.log(df.mid_c).diff()
    return df

jp = load("JP225_USD")
print("=== AUTOCORR fixed ===")
for col in ["ret","logret"]:
    v = jp[col].values
    dt = jp.dt.values
    mask = (dt[1:] == 60) & (dt[:-1] == 60)  # placeholder, simpler below
    # pairs (r_i, r_{i-1}) where bar i and i-1 are consecutive minutes: dt_i==60
    idx = np.where(jp.dt.values == 60)[0]
    idx = idx[idx > 0]
    a = v[idx]; b = v[idx-1]
    ok = np.isfinite(a) & np.isfinite(b)
    print(col, f"n={ok.sum()} lag1 autocorr={np.corrcoef(a[ok], b[ok])[0,1]:.4f}")
    # also lag2, lag5, lag10
    for lag in [2,5,10,30]:
        idx2 = np.where(jp.dt.values == 60)[0]
        # require chain: use returns array and check t difference == lag*60 between bar i and i-lag via positional if no gaps in between segment; approximate with t diff
        t = jp.t.values
        sel = idx2[idx2-lag >= 0]
        good = (t[sel] - t[sel-lag]) == lag*60
        sel = sel[good]
        aa = v[sel]; bb = v[sel-lag]
        ok2 = np.isfinite(aa) & np.isfinite(bb)
        print(f"  lag{lag}: {np.corrcoef(aa[ok2], bb[ok2])[0,1]:.4f} n={ok2.sum()}")

print("\n=== HURST on 1-min log returns (R/S) ===")
def hurst_rs(ts, lags=(10,20,40,80,160,320)):
    ts = np.asarray(ts, float); rs=[]; used=[]
    for lag in lags:
        n = len(ts)//lag
        if n < 5: continue
        vals=[]
        for i in range(n):
            chunk = ts[i*lag:(i+1)*lag]
            dev = np.cumsum(chunk - chunk.mean())
            R = dev.max()-dev.min(); S = chunk.std(ddof=1)
            if S>0: vals.append(R/S)
        if vals: rs.append(np.mean(vals)); used.append(lag)
    return np.polyfit(np.log(used), np.log(rs), 1)[0]

# synthetic sanity check
rng = np.random.default_rng(0)
print("synthetic iid returns H =", round(hurst_rs(rng.normal(size=200000)),3), "(expect ~0.5)")
for label, hours in [("9-10",[9]),("10-12",[10,11]),("12-15",[12,13,14]),("20-23",[20,21,22])]:
    sel = jp[(jp.dt==60) & (jp.hour.isin(hours))]
    h = hurst_rs(sel.logret.values)
    print(f"{label}: H(returns)={h:.3f} n={len(sel)}")
# all hours
sel = jp[jp.dt==60]
print("all hours: H =", round(hurst_rs(sel.logret.values),3))

print("\n=== DOW x HOUR variants ===")
jj = jp[jp.dt==60].copy()
for fut, shift in [("fut10",-10),("fut30",-30),("fut60",-60)]:
    jj[fut] = jj.mid_c.shift(shift) - jj.mid_c
    jj[fut+"_ok"] = (jj.t.shift(shift)-jj.t) == (-shift*60)
def cell(col, okcol, dow, hour):
    s = jj[(jj.dow==dow)&(jj.hour==hour)&jj[okcol]]
    return round(s[col].mean(),3), len(s)
print("mean 1min ret Thu13, Fri14:", round(jj[(jj.dow==3)&(jj.hour==13)].ret.mean(),3), round(jj[(jj.dow==4)&(jj.hour==14)].ret.mean(),3))
for fut in ["fut10","fut30","fut60"]:
    print(fut, "Thu13:", cell(fut, fut+"_ok", 3, 13), "Fri14:", cell(fut, fut+"_ok", 4, 14))

print("\n=== CROSS-CORR variants (past1h -> JP fut30) ===")
insts = {"JP":"JP225_USD","NAS":"NAS100_USD","SPX":"SPX500_USD","USD":"USD_JPY"}
series = {}
for k,v in insts.items():
    d = jp if k=="JP" else load(v)
    series[k] = dict(zip(d.t.values, d.mid_c.values))
tj = jp.t.values; pj = jp.mid_c.values
tset = tj
def price_at(s, t): return s.get(t, np.nan)
rows = []
# sample every minute of JP where all needed timestamps exist
for i, t in enumerate(tj):
    if t-3600 not in series["JP"] or t+1800 not in series["JP"]: continue
    rec = {"t": t}
    okk = True
    for k in insts:
        s = series[k]
        if t not in s or t-3600 not in s: okk=False; break
        rec[k+"_past_pts"] = s[t]-s[t-3600]
        rec[k+"_past_pct"] = s[t]/s[t-3600]-1
    if not okk: continue
    rec["fut30_pts"] = series["JP"][t+1800]-pj[i]
    rec["fut30_pct"] = series["JP"][t+1800]/pj[i]-1
    rows.append(rec)
df = pd.DataFrame(rows)
print("n=", len(df))
for k in insts:
    print(k, "pct->pct r=", round(df[k+"_past_pct"].corr(df.fut30_pct),4),
          "| pts->pts r=", round(df[k+"_past_pts"].corr(df.fut30_pts),4),
          "| pct->pts r=", round(df[k+"_past_pct"].corr(df.fut30_pts),4))
print("DONE2")
