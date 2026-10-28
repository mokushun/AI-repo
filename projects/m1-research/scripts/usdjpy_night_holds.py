"""
USD/JPY scalping: remaining candidates from BACKLOG #6 (2026-10-28)

Definitions identical to usdjpy_basics.py (fixed spec, 2026-10-27):
- Data: m1.db table m1, inst='USD_JPY' only, opened read-only
- bid OHLC = bo/bh/bl/bc, ask OHLC = ao/ah/al/ac (yen); 1 pip = 0.01 yen
- spread_c = ac - bc; range_bid = bh - bl; mid_c = (bc + ac) / 2
- Hour = JST (Asia/Tokyo) hour of the bar's start time
- A future h-min return is valid only if the bar h steps ahead is exactly h*60 s later
  (never crosses a gap); a prior 10-min move is valid only on the same condition
- Executable returns (real prices, not mid minus spread):
  long h = bc(t+h) - ac(t); short h = bc(t) - ac(t+h)
- Significance: future returns overlap, so thinned (every h-th row from position 0)
  t-values are always shown alongside full-sample means
- Pre-registered thresholds (not tuned after seeing results):
  night window = JST hours 21, 22, 23, 0 only
  holds = 10, 20, 30 min
  extreme prior move = |mid(t) - mid(t-10min)| >= 5 pip (0.05 yen) and >= 10 pip (0.10 yen)
  Reversion trade: prior up -> short, prior down -> long; reversion return is the
  executable return of that trade, and reversion mid return is the future mid move
  signed so that positive = move back toward the prior level.
Decision rule carried from the 2026-10-27 finding: if no candidate shows an edge
of order 0.1 pip or more, the market-order scalping line is closed.
"""

import os
import sqlite3
from math import sqrt

import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PIP = 100.0


def pips(x):
    return x * PIP


def stats_line(name, s):
    s = s.dropna()
    if len(s) == 0:
        print(f"{name}: n=0")
        return
    t_val = s.mean() / (s.std(ddof=1) / sqrt(len(s))) if len(s) > 1 and s.std(ddof=1) > 0 else float("nan")
    print(
        f"{name}: n={len(s)} mean={s.mean():+.4f}yen ({pips(s.mean()):+.2f}pip) "
        f"median={s.median():+.4f}yen std={s.std(ddof=1):.4f}yen t={t_val:.1f}"
    )


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT t,bo,bh,bl,bc,ao,ah,al,ac FROM m1 WHERE inst='USD_JPY' ORDER BY t",
        con,
    )
    con.close()

    df["spread_c"] = df.ac - df.bc
    df["range_bid"] = df.bh - df.bl
    df["mid_c"] = (df.bc + df.ac) / 2
    df["jst"] = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo")
    df["hour"] = df.jst.dt.hour

    night = df[df.hour.isin([21, 22, 23, 0])].copy()
    print("=== 0. NIGHT WINDOW (JST 21,22,23,0) ===")
    print(f"rows all={len(df)} night={len(night)} ({len(night)/len(df)*100:.1f}%)")
    print(
        f"night spread_c: mean={night.spread_c.mean():.4f}yen ({pips(night.spread_c.mean()):.2f}pip) "
        f"median={night.spread_c.median():.4f}yen"
    )
    print(
        f"night range_bid: mean={night.range_bid.mean():.4f}yen ({pips(night.range_bid.mean()):.2f}pip) "
        f"median={night.range_bid.median():.4f}yen"
    )
    print(f"night ratio mean(spread)/mean(range) = {night.spread_c.mean()/night.range_bid.mean()*100:.1f}%")
    print(f"night share range_bid <= spread_c = {(night.range_bid <= night.spread_c).mean()*100:.1f}%")
    g = night.groupby("hour").agg(spread=("spread_c", "mean"), rng=("range_bid", "mean"), n=("t", "size"))
    g["ratio_%"] = g.spread / g.rng * 100
    print(g.round(4).to_string())

    # EMA on the full series (same convention as basics: no reset at gaps)
    df["ema9"] = df.mid_c.ewm(span=9, adjust=False).mean()
    df["ema21"] = df.mid_c.ewm(span=21, adjust=False).mean()
    df["bull"] = df.ema9 > df.ema21
    # Re-index night rows against the full frame so future/prior lookups use full data
    night_idx = df[df.hour.isin([21, 22, 23, 0])].index

    print("\n=== 1. NIGHT NO-FILTER BASELINE (executable, real prices) ===")
    for h in [10, 20, 30]:
        ok = (df.t.shift(-h) - df.t) == h * 60
        sub = pd.DataFrame(
            {
                "fut": (df.mid_c.shift(-h) - df.mid_c),
                "long_exec": (df.bc.shift(-h) - df.ac),
                "short_exec": (df.bc - df.ac.shift(-h)),
                "ok": ok,
            }
        )
        sub = sub.loc[night_idx]
        sub = sub[sub.ok].reset_index(drop=True)
        print(f"\n-- hold {h} min (valid n={len(sub)}) --")
        stats_line(f"night fut{h} mid", sub.fut)
        stats_line(f"night fut{h} long_exec", sub.long_exec)
        stats_line(f"night fut{h} short_exec", sub.short_exec)
        th = sub.iloc[::h]
        print("  [thinned, non-overlapping]")
        stats_line(f"night fut{h} mid (thinned)", th.fut)
        stats_line(f"night fut{h} long_exec (thinned)", th.long_exec)
        stats_line(f"night fut{h} short_exec (thinned)", th.short_exec)

    print("\n=== 2. NIGHT EMA9>EMA21 (same holds) ===")
    for h in [10, 20, 30]:
        ok = (df.t.shift(-h) - df.t) == h * 60
        sub = pd.DataFrame(
            {
                "fut": (df.mid_c.shift(-h) - df.mid_c),
                "long_exec": (df.bc.shift(-h) - df.ac),
                "short_exec": (df.bc - df.ac.shift(-h)),
                "bull": df.bull,
                "ok": ok,
            }
        )
        sub = sub.loc[night_idx]
        sub = sub[sub.ok].reset_index(drop=True)
        bull, bear = sub[sub.bull], sub[~sub.bull]
        th = sub.iloc[::h]
        th_bull, th_bear = th[th.bull], th[~th.bull]
        print(f"\n-- hold {h} min (valid n={len(sub)}; bull n={len(bull)}, bear n={len(bear)}) --")
        stats_line(f"night fut{h} bull mid", bull.fut)
        stats_line(f"night fut{h} bear mid", bear.fut)
        if len(th_bull) > 1 and len(th_bear) > 1:
            diff = th_bull.fut.mean() - th_bear.fut.mean()
            se = sqrt(th_bull.fut.std(ddof=1) ** 2 / len(th_bull) + th_bear.fut.std(ddof=1) ** 2 / len(th_bear))
            excess = th_bull.fut.mean() - th.fut.mean()
            print(f"  bull-bear diff (thinned) = {diff:+.4f}yen ({pips(diff):+.2f}pip) Welch t={diff/se:.2f}")
            print(f"  bull excess over unconditional (thinned) = {excess:+.4f}yen ({pips(excess):+.2f}pip)")
        stats_line(f"night fut{h} bull long_exec", bull.long_exec)
        stats_line(f"night fut{h} bear short_exec", bear.short_exec)
        print("  [thinned executable]")
        stats_line(f"night fut{h} bull long_exec (thinned)", th_bull.long_exec)
        stats_line(f"night fut{h} bear short_exec (thinned)", th_bear.short_exec)

    print("\n=== 3. CONDITIONAL MEAN REVERSION AFTER EXTREME 10-MIN MOVES (all hours + night only) ===")
    prior_ok = (df.t - df.t.shift(10)) == 10 * 60
    prior = df.mid_c - df.mid_c.shift(10)
    for thr_pip in [5, 10]:
        thr = thr_pip / PIP
        for scope, idx in [("ALL", df.index), ("NIGHT", night_idx)]:
            for h in [10, 20]:
                ok = prior_ok & ((df.t.shift(-h) - df.t) == h * 60) & (prior.abs() >= thr)
                sub = pd.DataFrame(
                    {
                        "prior": prior,
                        "fut": (df.mid_c.shift(-h) - df.mid_c),
                        "long_exec": (df.bc.shift(-h) - df.ac),
                        "short_exec": (df.bc - df.ac.shift(-h)),
                        "ok": ok,
                    }
                )
                sub = sub.loc[idx]
                sub = sub[sub.ok].reset_index(drop=True)
                if len(sub) == 0:
                    print(f"thr>={thr_pip}pip scope={scope} hold={h}: n=0")
                    continue
                # reversion-signed mid: positive if future move opposes prior move
                sub["rev_mid"] = -np.sign(sub.prior) * sub.fut
                # reversion executable: prior up -> short, prior down -> long
                sub["rev_exec"] = np.where(sub.prior > 0, sub.short_exec, sub.long_exec)
                th = sub.iloc[::h]
                print(f"\n-- prior|10min|>={thr_pip}pip scope={scope} hold={h} (n={len(sub)}) --")
                print(f"  prior mean_abs={sub.prior.abs().mean():.4f}yen ({pips(sub.prior.abs().mean()):.2f}pip)")
                stats_line(f"  rev_mid (signed, + = reversion)", sub.rev_mid)
                stats_line(f"  rev_exec (real prices)", sub.rev_exec)
                print("  [thinned]")
                stats_line(f"  rev_mid (thinned)", th.rev_mid)
                stats_line(f"  rev_exec (thinned)", th.rev_exec)

    print("\nDONE")


if __name__ == "__main__":
    main()
