import pandas as pd, numpy as np, os
P = os.environ.get("N225_CSV_PATH", os.path.expanduser("~/workspace/oanda_json/n225mini_options_master.csv"))
df = pd.read_csv(P, encoding="utf-8-sig")
print("rows", len(df))
print("cols", list(df.columns))
df["scraped_at"] = pd.to_datetime(df["scraped_at"])
print("scraped range", df.scraped_at.min(), "->", df.scraped_at.max())
snaps = df.groupby("scraped_at")
print("snapshots", df.scraped_at.nunique())
print("distinct trade_dates", df.trade_date.nunique())
per = snaps.size()
print("rows per snapshot: min", per.min(), "median", per.median(), "max", per.max())
print("snapshots per day (head/tail):")
byday = df.groupby(df.scraped_at.dt.date).scraped_at.nunique()
print(byday.to_string())
print("\nestimated_expiry values:", sorted(df.estimated_expiry.dropna().unique())[:20], "... total", df.estimated_expiry.nunique())
print("is_atm rows per snapshot: value counts of count")
atm_cnt = df[df.is_atm == True].groupby("scraped_at").size()
print(atm_cnt.value_counts().to_dict() if len(atm_cnt) else "no atm rows")
print("snapshots missing ATM:", df.scraped_at.nunique() - len(atm_cnt))

# bot skew series
recs = []
for ts, g in df.groupby("scraped_at"):
    atm = g[g.is_atm == True]
    if len(atm):
        r = atm.iloc[0]
        recs.append(dict(ts=ts, strike=r.strike, call_iv=r.call_iv, put_iv=r.put_iv,
                         skew=(r.put_iv - r.call_iv) if pd.notna(r.put_iv) and pd.notna(r.call_iv) else np.nan,
                         call_time=r.call_price_time, put_time=r.put_price_time,
                         expiry=r.estimated_expiry, trade_date=r.trade_date))
s = pd.DataFrame(recs).sort_values("ts").reset_index(drop=True)
s.to_csv(os.path.join(os.path.dirname(P), "skew_series_bot.csv"), index=False)
print("\n=== BOT SKEW (ATM put_iv - call_iv) ===")
print(s["skew"].describe().round(3).to_string())
print("skew valid snapshots:", s["skew"].notna().sum(), "/", len(s))
for th in [0, 1, 2, 3, 5]:
    print(f"skew>{th}: {(s["skew"]>th).sum()} snapshots ({(s["skew"]>th).mean()*100:.1f}%)")
hi = s[s["skew"] > 2]
print("\nskew>2 rows:")
print(hi.to_string(index=False) if len(hi) else "none")
# episodes: skew>2 snapshots grouped by calendar date & by gaps >26h
if len(hi):
    days = sorted(hi.ts.dt.date.unique())
    print("skew>2 distinct dates:", len(days), days)
    gaps = hi.ts.diff().dt.total_seconds()/3600
    print("episodes (split when gap>26h):", int((gaps>26).sum()+1))
    print("call_time vs put_time on skew>2 rows (staleness check):")
    print(hi[["ts","call_time","put_time"]].to_string(index=False))
    alltimes = s[["call_time","put_time"]].copy()
    print("overall: call_time present", alltimes.call_time.notna().mean().round(3), "put_time present", alltimes.put_time.notna().mean().round(3))

print("\n=== 25d risk reversal per snapshot (for comparison) ===")
rr = []
for ts, g in df.groupby("scraped_at"):
    gg = g.dropna(subset=["call_delta","put_delta","call_iv","put_iv"])
    if len(gg) < 5: continue
    ci = (gg.call_delta - 0.25).abs().idxmin()
    pi = (gg.put_delta + 0.25).abs().idxmin()
    rr.append(dict(ts=ts, rr25=gg.loc[pi,"put_iv"] - gg.loc[ci,"call_iv"],
                   put25_iv=gg.loc[pi,"put_iv"], call25_iv=gg.loc[ci,"call_iv"],
                   put25_delta=gg.loc[pi,"put_delta"], call25_delta=gg.loc[ci,"call_delta"],
                   expiry=gg.estimated_expiry.iloc[0]))
r = pd.DataFrame(rr).sort_values("ts")
print(r.rr25.describe().round(3).to_string() if len(r) else "no rr data")
if len(r):
    print("corr(bot skew, rr25) on common ts:")
    m = s.merge(r, on="ts", how="inner", suffixes=("","_r"))
    print(round(m["skew"].corr(m.rr25),3), "n=", len(m))
    print("rr25 > 2:", (r.rr25>2).sum(), "/", len(r))
print("\nDTE distribution at snapshots:")
s2 = s.copy(); s2["dte"] = (pd.to_datetime(s2.expiry) - pd.to_datetime(s2.trade_date)).dt.days
print(s2.dte.value_counts().sort_index().to_dict())
print("bot skew mean by DTE:")
print(s2.groupby("dte")["skew"].mean().round(3).to_dict())
print("DONE-SKEW")
