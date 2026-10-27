import sqlite3, os
import pandas as pd, numpy as np

BASE = os.path.expanduser("~/workspace/oanda_json")
# skew series (bot definition) built by analyze_skew.py
import os.path as _op
_skew_csv = f"{BASE}/skew_series_bot.csv"
if not _op.exists(_skew_csv):
    _skew_csv = _op.join(_op.dirname(_op.abspath(__file__)), "..", "outputs", "skew_series_bot.csv")
s = pd.read_csv(_skew_csv, parse_dates=["ts"])
s = s.rename(columns={"ts": "jst", "skew": "skew"}).sort_values("jst")

con = sqlite3.connect(f"file:{BASE}/m1.db?mode=ro", uri=True)
m1 = pd.read_sql_query(
    "SELECT t, bo,bh,bl,bc,ao,ac FROM m1 WHERE inst='JP225_USD' AND t>=? AND t<=? ORDER BY t",
    con, params=(int(pd.Timestamp('2026-09-01', tz='Asia/Tokyo').timestamp()),
                 int(pd.Timestamp('2026-10-07', tz='Asia/Tokyo').timestamp())))
m1["jst"] = pd.to_datetime(m1.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)
m1["ema9"] = m1.bc.ewm(span=9, adjust=False).mean()
m1["ema21"] = m1.bc.ewm(span=21, adjust=False).mean()
m1["gc"] = (m1.ema9 > m1.ema21) & (m1.ema9.shift(1) <= m1.ema21.shift(1))
s["skew_ts"] = s.jst
merged = pd.merge_asof(m1.sort_values("jst"), s[["jst","skew","skew_ts"]].sort_values("jst"), on="jst", direction="backward")
merged["age_h"] = (merged.jst - merged.skew_ts).dt.total_seconds()/3600

print("m1 bars", len(merged), merged.jst.min(), "->", merged.jst.max())
print("bars with skew>2 (no tolerance):", int(((merged["skew"]>2)).sum()))
print("GC bars total:", int(merged.gc.sum()))
print("GC bars while skew>2:", int((merged.gc & (merged["skew"]>2)).sum()))
print("GC bars while skew>2 and age<=1h:", int((merged.gc & (merged["skew"]>2) & (merged.age_h<=1)).sum()))

def backtest(df, tp=100.0, sl=40.0, max_bars=180, spread=10.05, tp_first=True, check_entry_bar=False):
    df = df.reset_index(drop=True)
    sig = ((df["skew"] > 2) & df.gc).values
    bo = df.bo.values; bh = df.bh.values; bl = df.bl.values; bc = df.bc.values; jst = df.jst
    trades = []; pos = False; ep = 0.0; ei = 0
    for i in range(1, len(df)):
        if not pos:
            if sig[i-1]:
                pos = True; ep = bo[i] + spread; ei = i
                if not check_entry_bar: continue
            else:
                continue
        if pos:
            start = ei if check_entry_bar else i
            # when just entered and check_entry_bar, evaluate this same bar i==ei
            if i < ei: continue
            if i == ei and not check_entry_bar: continue
            bars = i - ei
            hit_tp = bh[i] >= ep + tp; hit_sl = bl[i] <= ep - sl; time_up = bars >= max_bars
            if hit_tp or hit_sl or time_up:
                if hit_tp and hit_sl:
                    xp = ep + tp if tp_first else ep - sl; reason = "TP" if tp_first else "SL"
                elif hit_tp: xp = ep + tp; reason = "TP"
                elif hit_sl: xp = ep - sl; reason = "SL"
                else: xp = bc[i]; reason = "TIME"
                trades.append(dict(entry=jst.iloc[ei], exit=jst.iloc[i], pnl=xp-ep, reason=reason, bars=bars))
                pos = False
    return pd.DataFrame(trades)

for label, kw in [("bot-as-written (TP priority, entry bar unchecked)", dict(tp_first=True, check_entry_bar=False)),
                  ("conservative (SL priority, entry bar checked)", dict(tp_first=False, check_entry_bar=True))]:
    tdf = backtest(merged, **kw)
    print(f"\n=== {label} ===")
    if tdf.empty: print("no trades")
    else:
        wins = tdf[tdf.pnl>0]; losses = tdf[tdf.pnl<0]
        pf = wins.pnl.sum()/-losses.pnl.sum() if len(losses) and losses.pnl.sum()!=0 else float('inf')
        print(f"trades={len(tdf)} total={tdf.pnl.sum():+.1f} winrate={(tdf.pnl>0).mean()*100:.1f}% PF={pf:.2f}")
        print(tdf.to_string(index=False))

# with tolerance 1h: mask skew older than 1h as NaN
m2 = merged.copy(); m2.loc[m2.age_h > 1, "skew"] = np.nan
tdf = backtest(m2, tp_first=False, check_entry_bar=True)
print("\n=== conservative + skew tolerance 1h ===")
if tdf.empty: print("no trades")
else:
    wins = tdf[tdf.pnl>0]; losses = tdf[tdf.pnl<0]
    pf = wins.pnl.sum()/-losses.pnl.sum() if len(losses) and losses.pnl.sum()!=0 else float('inf')
    print(f"trades={len(tdf)} total={tdf.pnl.sum():+.1f} winrate={(tdf.pnl>0).mean()*100:.1f}% PF={pf:.2f}")
    print(tdf.to_string(index=False))
print("DONE-REPRO")
