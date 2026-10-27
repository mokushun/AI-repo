import sqlite3, os
import numpy as np
import pandas as pd
DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql_query("SELECT t,bc,ac FROM m1 WHERE inst='JP225_USD' ORDER BY t", con)
df["mid_c"] = (df.bc+df.ac)/2
df["hour"] = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo").dt.hour
logp = np.log(df.mid_c.values)

def hurst_pkg_style(series, max_lag=100):
    series = np.asarray(series, float)
    lags = range(2, max_lag)
    tau = [np.sqrt(np.std(series[lag:] - series[:-lag])) for lag in lags]
    poly = np.polyfit(np.log(list(lags)), np.log(tau), 1)
    return poly[0]*2.0

rng = np.random.default_rng(1)
rw = np.cumsum(rng.normal(size=200000))
print("synthetic random walk H =", round(hurst_pkg_style(rw),3), "(expect ~0.5)")
for label, hours in [("9-10",[9]),("10-12",[10,11]),("12-15",[12,13,14]),("20-23",[20,21,22])]:
    mask = df.hour.isin(hours).values
    for ml in [50,100,200]:
        print(f"{label} maxlag{ml}: H={hurst_pkg_style(logp[mask], ml):.3f} n={mask.sum()}")
print("all: H=", round(hurst_pkg_style(logp,100),3))
print("DONE4")
