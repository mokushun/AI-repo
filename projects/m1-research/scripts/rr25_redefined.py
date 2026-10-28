"""BACKLOG #1: 25-delta risk-reversal skew, redefined (2026-10-27, Muse).

Question: does replacing the bot's ATM put_iv - call_iv skew with a
25-delta risk reversal (25d put IV - 25d call IV), plus a price-time
freshness filter and DTE stratification, give a clean series
(median near 0, small sigma)?

Definitions (read before reusing the numbers):
- Input: n225mini_options_master.csv, path from N225_CSV_PATH
  (default ~/workspace/oanda_json/n225mini_options_master.csv).
  Coverage of the copy on this VM: 2026-09-02 to 2026-10-05,
  776 snapshots, 22 trade dates. Post-2026-10-23 data is NOT in it.
- One snapshot = one scraped_at value. Strikes in a snapshot share
  one estimated_expiry (a Friday weekly).
- 25d legs: put with put_delta closest to -0.25, call with
  call_delta closest to +0.25, within |delta error| <= 0.10.
  Median delta error is ~0.01, so the nearest-strike choice is tight.
- Freshness: call_price_time / put_price_time are "MM/DD HH:MM" with
  no year. They are parsed against the snapshot's scraped_at date,
  rolling back a day while the parsed time is in the future.
  Age = scraped_at - price_time. TIMEZONE CAVEAT: the CSV does not
  state the timezone of scraped_at or price_time; ages are
  differences on the same clock, so they are valid only if both
  fields share that clock (assumed here, not verified).
- DTE = calendar days from the snapshot date to estimated_expiry.
  (Not trading days, and not last_trading_day.)
- Two freshness variants, because "fresh" is ambiguous:
  (a) filter-first: drop legs older than the cap, then pick nearest
      delta among the survivors (a stale nearest leg is replaced).
  (b) strict: pick nearest-delta legs first, keep the snapshot only
      if BOTH legs are <= 1h old.
- Sensitivity: delta-interpolation (linear IV in delta space) instead
  of nearest strike, same caps.

Reference values for comparison (analyze_skew.py, bot definition):
  ATM skew: n=500/776, median -1.38, mean -8.0, sigma ~11.0,
  DTE1 mean -15.8, skew>2 in 15 snapshots.
"""
import os
import numpy as np
import pandas as pd

P = os.path.expanduser(os.environ.get(
    "N225_CSV_PATH", "~/workspace/oanda_json/n225mini_options_master.csv"))
OUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "outputs", "rr25_series.csv")

df = pd.read_csv(P, encoding="utf-8-sig")
for c in ["call_iv", "put_iv", "call_delta", "put_delta"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")
df["scraped_at"] = pd.to_datetime(df["scraped_at"])
df["expiry"] = pd.to_datetime(df["estimated_expiry"], errors="coerce")
df["dte"] = (df["expiry"] - df["scraped_at"].dt.normalize()).dt.days


def parse_pt(s, scraped):
    if pd.isna(s) or str(s).strip() in ("", "--"):
        return pd.NaT
    try:
        md, hm = str(s).strip().split()
        m, d = map(int, md.split("/"))
        hh, mm = map(int, hm.split(":"))
        dt = pd.Timestamp(year=scraped.year, month=m, day=d, hour=hh, minute=mm)
        while dt > scraped:
            dt -= pd.Timedelta(days=1)
        return dt
    except Exception:
        return pd.NaT


df["call_pt"] = [parse_pt(s, t) for s, t in
                 zip(df["call_price_time"], df["scraped_at"])]
df["put_pt"] = [parse_pt(s, t) for s, t in
                zip(df["put_price_time"], df["scraped_at"])]
df["call_age_h"] = (df["scraped_at"] - df["call_pt"]).dt.total_seconds() / 3600
df["put_age_h"] = (df["scraped_at"] - df["put_pt"]).dt.total_seconds() / 3600
print("snapshots:", df["scraped_at"].nunique(),
      "| scraped range:", df["scraped_at"].min(), "->", df["scraped_at"].max())
print("selected-leg age median (all rows): call",
      round(df["call_age_h"].median(), 2), "h, put",
      round(df["put_age_h"].median(), 2), "h")


def nearest_series(age_cap=None, strict=False):
    recs = []
    for ts, g in df.groupby("scraped_at"):
        calls = g.dropna(subset=["call_delta", "call_iv", "call_age_h"])
        puts = g.dropna(subset=["put_delta", "put_iv", "put_age_h"])
        if not strict and age_cap is not None:
            calls = calls[calls["call_age_h"] <= age_cap]
            puts = puts[puts["put_age_h"] <= age_cap]
        if len(calls) == 0 or len(puts) == 0:
            continue
        ci = (calls["call_delta"] - 0.25).abs().idxmin()
        pi = (puts["put_delta"] + 0.25).abs().idxmin()
        c, p = calls.loc[ci], puts.loc[pi]
        if abs(c["call_delta"] - 0.25) > 0.10 or abs(p["put_delta"] + 0.25) > 0.10:
            continue
        if strict and not (c["call_age_h"] <= 1 and p["put_age_h"] <= 1):
            continue
        recs.append(dict(ts=ts, rr25=p["put_iv"] - c["call_iv"],
                         put_iv=p["put_iv"], call_iv=c["call_iv"],
                         put_delta=p["put_delta"], call_delta=c["call_delta"],
                         put_age_h=p["put_age_h"], call_age_h=c["call_age_h"],
                         dte=int(g["dte"].iloc[0])))
    return pd.DataFrame(recs)


def show(name, r):
    print(f"\n=== {name} (n={len(r)}/776) ===")
    if len(r) == 0:
        return
    print(r["rr25"].describe().round(3).to_string())
    print("rr25>2:", int((r["rr25"] > 2).sum()),
          f"({(r['rr25'] > 2).mean() * 100:.1f}%)",
          "| rr25>4:", int((r["rr25"] > 4).sum()))
    print("by DTE:")
    print(r.groupby("dte")["rr25"].agg(["count", "mean", "median", "std"])
          .round(2).to_string())
    print("DTE>=5:", r[r["dte"] >= 5]["rr25"].describe()
          [["count", "mean", "50%", "std"]].round(2).to_dict())


base = nearest_series()
show("nearest, no age cap", base)
show("nearest, filter-first cap 1h", nearest_series(age_cap=1))
show("nearest, STRICT both legs <=1h", nearest_series(strict=True))
base.to_csv(os.path.abspath(OUT_CSV), index=False)
print("\nseries written to:", os.path.abspath(OUT_CSV),
      "(note: *.csv is gitignored; force-add needed to commit it)")

# Interpolation sensitivity (nearest vs linear IV in delta space).
def interp_series(age_cap=None):
    recs = []
    for ts, g in df.groupby("scraped_at"):
        calls = g.dropna(subset=["call_delta", "call_iv", "call_age_h"])
        puts = g.dropna(subset=["put_delta", "put_iv", "put_age_h"])
        if age_cap is not None:
            calls = calls[calls["call_age_h"] <= age_cap]
            puts = puts[puts["put_age_h"] <= age_cap]
        if len(calls) < 2 or len(puts) < 2:
            continue
        cs = calls.sort_values("call_delta")
        ps = puts.sort_values("put_delta")
        if not (cs["call_delta"].min() <= 0.25 <= cs["call_delta"].max()):
            continue
        if not (ps["put_delta"].min() <= -0.25 <= ps["put_delta"].max()):
            continue
        civ = float(np.interp(0.25, cs["call_delta"].values, cs["call_iv"].values))
        piv = float(np.interp(-0.25, ps["put_delta"].values, ps["put_iv"].values))
        recs.append(dict(ts=ts, rr25=piv - civ, dte=int(g["dte"].iloc[0])))
    return pd.DataFrame(recs)


show("interp, no age cap", interp_series())
show("interp, filter-first cap 1h", interp_series(age_cap=1))
print("\nDONE-RR25")
