import sqlite3, os
import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

def load(inst):
    df = pd.read_sql_query(
        "SELECT t,bo,bh,bl,bc,ao,ah,al,ac FROM m1 WHERE inst=? ORDER BY t",
        con, params=(inst,))
    df["mid_c"] = (df.bc + df.ac) / 2
    df["mid_o"] = (df.bo + df.ao) / 2
    df["spread_c"] = df.ac - df.bc
    df["spread_o"] = df.ao - df.bo
    df["spread_avg4"] = ((df.ao-df.bo)+(df.ah-df.bh)+(df.al-df.bl)+(df.ac-df.bc))/4
    df["range_bid"] = df.bh - df.bl
    df["range_mid"] = (df.bh+df.ah)/2 - (df.bl+df.al)/2
    df["jst"] = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo")
    df["hour"] = df.jst.dt.hour
    df["dow"] = df.jst.dt.dayofweek  # Mon=0
    df["date_jst"] = df.jst.dt.date
    return df

print("=== LOAD ===")
data = {}
for inst in ["JP225_USD","NAS100_USD","SPX500_USD","USD_JPY"]:
    data[inst] = load(inst)
    d = data[inst]
    print(inst, len(d), d.jst.iloc[0], "->", d.jst.iloc[-1])

jp = data["JP225_USD"].copy()

print("\n=== 1. SPREAD / RANGE (JP225) ===")
for col in ["spread_c","spread_o","spread_avg4"]:
    s = jp[col]
    print(f"{col}: mean={s.mean():.3f} median={s.median():.2f}")
for col in ["range_bid","range_mid"]:
    s = jp[col]
    print(f"{col}: mean={s.mean():.3f} median={s.median():.2f}")
print(f"ratio mean_spread_c/mean_range_bid = {jp.spread_c.mean()/jp.range_bid.mean()*100:.1f}%")
print(f"ratio mean_spread_c/mean_range_mid = {jp.spread_c.mean()/jp.range_mid.mean()*100:.1f}%")
print(f"per-bar ratio spread_c/range_bid mean = {(jp.spread_c/jp.range_bid.replace(0,np.nan)).mean()*100:.1f}%")

print("\n--- by JST hour (JP225) ---")
g = jp.groupby("hour").agg(spread=("spread_c","mean"), rng=("range_bid","mean"), n=("t","size"))
g["ratio%"] = g.spread/g.rng*100
print(g.round(2).to_string())
print("hour9:", g.loc[9].round(2).to_dict())

print("\n=== 2. AUTOCORR lag1 of 1-min returns (JP225, dt==60 only) ===")
jp["ret"] = jp.mid_c.diff()
jp["dt"] = jp.t.diff()
ok = jp.dt == 60
r = jp.loc[ok, "ret"]
r_prev = jp.ret.shift(1)[ok]
print(f"n={ok.sum()} autocorr(pts returns)={np.corrcoef(r, r_prev)[0,1]:.4f}")
jp["logret"] = np.log(jp.mid_c).diff()
lr = jp.loc[ok, "logret"]; lr_prev = jp.logret.shift(1)[ok]
print(f"autocorr(log returns)={np.corrcoef(lr, lr_prev)[0,1]:.4f}")

print("\n=== 3. HURST (R/S on log mid_c, filtered hours concatenated) ===")
def hurst_rs(ts, lags=(10,20,40,80,160,320)):
    ts = np.asarray(ts, dtype=float)
    rs = []
    used = []
    for lag in lags:
        n_chunks = len(ts)//lag
        if n_chunks < 5: continue
        vals = []
        for i in range(n_chunks):
            chunk = ts[i*lag:(i+1)*lag]
            m = chunk.mean()
            dev = np.cumsum(chunk - m)
            R = dev.max() - dev.min()
            S = chunk.std(ddof=1)
            if S > 0: vals.append(R/S)
        if vals:
            rs.append(np.mean(vals)); used.append(lag)
    if len(used) < 3: return np.nan
    return np.polyfit(np.log(used), np.log(rs), 1)[0]

logp = np.log(jp.mid_c.values)
for label, hours in [("9-10",[9]),("10-12",[10,11]),("12-15",[12,13,14]),("20-23",[20,21,22])]:
    mask = jp.hour.isin(hours).values
    h = hurst_rs(logp[mask])
    print(f"{label}: H={h:.3f} n={mask.sum()}")

print("\n=== 4. EMA edges (JP225) ===")
jp["ema9"] = jp.mid_c.ewm(span=9, adjust=False).mean()
jp["ema21"] = jp.mid_c.ewm(span=21, adjust=False).mean()
jp["fut10"] = jp.mid_c.shift(-10) - jp.mid_c
jp["fut10_ok"] = (jp.t.shift(-10) - jp.t) == 600
cond = (jp.ema9 > jp.ema21) & jp.fut10_ok
f = jp.loc[cond, "fut10"]
fu = jp.loc[~ (jp.ema9 > jp.ema21) & jp.fut10_ok, "fut10"]
from math import sqrt
def stats(s):
    return f"n={len(s)} mean={s.mean():+.3f} std={s.std():.2f} t={s.mean()/(s.std()/sqrt(len(s))):.1f}"
print("EMA9>EMA21:", stats(f))
print("EMA9<=EMA21:", stats(fu))
print("all:", stats(jp.loc[jp.fut10_ok,'fut10']))

# align NAS by timestamp
nas = data["NAS100_USD"][["t","mid_c"]].rename(columns={"mid_c":"nas_c"})
nas["nas_ema9"] = nas.nas_c.ewm(span=9, adjust=False).mean()
nas["nas_ema21"] = nas.nas_c.ewm(span=21, adjust=False).mean()
nas["nas_up_ema"] = nas.nas_ema9 > nas.nas_ema21
m = jp.merge(nas[["t","nas_up_ema"]], on="t", how="inner")
m_ok = m[(m.t.shift(-10)-m.t)==600] if False else m  # fut10 already computed on jp index before merge -> recompute
m["fut10b"] = m.mid_c.shift(-10) - m.mid_c
m["gap10"] = m.t.shift(-10) - m.t
mm = m[m.gap10==600]
both = mm[(mm.ema9>mm.ema21) & (mm.nas_up_ema==True)]
print("JP EMA bull x NAS EMA bull:", stats(both.fut10b))
jp_only = mm[(mm.ema9>mm.ema21) & (mm.nas_up_ema!=True)]
print("JP EMA bull x NAS EMA not bull:", stats(jp_only.fut10b))

print("\n=== 5. Cross-corr: past 1h returns vs JP future 30m (pct) ===")
base = jp[["t","mid_c"]].rename(columns={"mid_c":"jp_c"})
for name, key in [("NAS","NAS100_USD"),("SPX","SPX500_USD"),("USDJPY","USD_JPY")]:
    d = data[key][["t","mid_c"]].rename(columns={"mid_c":f"{name}_c"})
    base = base.merge(d, on="t", how="inner")
base = base.sort_values("t").reset_index(drop=True)
# map timestamp->price via index on t for exact 60m/30m offsets: use merge on shifted t
def shifted(col, delta):
    s = base[["t", col]].copy(); s["t"] = s.t - delta; return s.rename(columns={col: col+"_s"})
for name in ["jp_c","NAS_c","SPX_c","USDJPY_c"]:
    base = base.merge(shifted(name, -3600), on="t", how="left", suffixes=("","_p"))
# past prices: price at t-3600
for name in ["jp_c","NAS_c","SPX_c","USDJPY_c"]:
    past = base[["t", name]].copy(); past["t"] = past.t + 3600; past = past.rename(columns={name: name+"_past"})
    base = base.merge(past, on="t", how="left")
    base[f"past1h_{name}"] = base[name]/base[name+"_past"] - 1
fut = base[["t","jp_c"]].copy(); fut["t"] = fut.t - 1800; fut = fut.rename(columns={"jp_c":"jp_fut"})
base = base.merge(fut, on="t", how="left")
base["fut30"] = base.jp_fut/base.jp_c - 1
sub = base.dropna(subset=["fut30"])
for name in ["NAS_c","jp_c","SPX_c","USDJPY_c"]:
    s = sub.dropna(subset=[f"past1h_{name}"])
    print(f"{name} past1h vs JP fut30: r={s[f'past1h_{name}'].corr(s.fut30):.4f} n={len(s)}")

print("\n=== 6. Weekday x hour mean 1-min return (pts, JP225) ===")
jj = jp[jp.dt==60].copy()
piv = jj.groupby(["dow","hour"]).ret.mean().unstack(0)
print("Thu(3) 13h:", round(piv.loc[13,3],3) if 13 in piv.index else None)
print("Fri(4) 14h:", round(piv.loc[14,4],3) if 14 in piv.index else None)
flat = piv.stack().sort_values(ascending=False)
print("top5 cells:\n", flat.head(5).round(3).to_string())
print("bottom5 cells:\n", flat.tail(5).round(3).to_string())

print("\nDONE")
