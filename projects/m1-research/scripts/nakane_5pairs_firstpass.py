#!/usr/bin/env python3
"""Nakane 5-pair first pass on the expanded m1.db (2026-10-29).

Data: ~/workspace/oanda_json/m1.db (OANDA M1, bid/ask OHLC, epoch sec UTC).
Override with M1_DB_PATH.

Part A: OANDA spreads (pip) for the 5 nakane pairs, 2026-09-01..2026-10-22,
        at JST 00:00 / 05:35 / 09:55 (bar-open ask-bid) + overall close basis.
        Same measurement as the GMO klines sample taken 2026-10-29.
Part B: Nakane live config, long only, price only (executable basis):
        IN  JST 00:00 bar-open ASK, OUT JST 09:55 bar-open BID,
        Thursday OUT 05:35. Exit dates Tue-Fri (JST). No swap.
        Splits: gotobi (JST date day-of-month % 5 == 0) / non-gotobi,
        and Sep-Oct 2026 only.
All times JST = UTC+9 fixed. pip = 0.01 yen for JPY pairs.
"""
import os
import sqlite3
import statistics
import datetime

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PAIRS = ["USD_JPY", "GBP_JPY", "AUD_JPY", "EUR_JPY", "CHF_JPY"]
JST = datetime.timezone(datetime.timedelta(hours=9))

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

print("=== A. OANDA spreads 2026-09-01..10-22 (pip, open basis at target minutes) ===")
since = int(datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc).timestamp())
until = int(datetime.datetime(2026, 10, 23, tzinfo=datetime.timezone.utc).timestamp())


def at(inst, hm):
    h, m = map(int, hm.split(":"))
    mod = h * 60 + m
    rows = [r[0] for r in con.execute(
        "SELECT (ao-bo)*100 FROM m1 WHERE inst=? AND CAST(((t/60+540)%1440) AS INT)=? AND t>=? AND t<?",
        (inst, mod, since, until))]
    return (f"n={len(rows)} med={statistics.median(rows):.2f} mean={statistics.mean(rows):.2f}"
            if rows else "n=0")


for inst in PAIRS:
    rows = [r[0] for r in con.execute(
        "SELECT (ac-bc)*100 FROM m1 WHERE inst=? AND t>=? AND t<?", (inst, since, until))]
    print(f"{inst}: 00:00 {at(inst, '00:00')} | 09:55 {at(inst, '09:55')} | "
          f"05:35 {at(inst, '05:35')} | overall(close) med={statistics.median(rows):.2f} "
          f"mean={statistics.mean(rows):.2f}")

print()
print("=== B. Nakane live config (00:00 ask-in -> 09:55 bid-out, Thu 05:35), long, price-only ===")


def stats(rets):
    if not rets:
        return "n=0"
    n = len(rets)
    mean = statistics.mean(rets)
    gp = sum(r for r in rets if r > 0)
    gl = -sum(r for r in rets if r < 0)
    pf = gp / gl if gl > 0 else float("inf")
    sd = statistics.pstdev(rets) if n > 1 else 0.0
    t = mean / (sd / (n ** 0.5)) if sd > 0 else 0.0
    wr = sum(1 for r in rets if r > 0) / n * 100
    return f"n={n} mean={mean:+.2f}pip PF={pf:.3f} t={t:.2f} win={wr:.1f}%"


cutoff = datetime.date(2026, 10, 23)
for inst in PAIRS:
    data = {}
    for t, ao, bo in con.execute(
            "SELECT t,ao,bo FROM m1 WHERE inst=? AND CAST(((t/60+540)%1440) AS INT) IN (0,335,595) ORDER BY t",
            (inst,)):
        dt = datetime.datetime.fromtimestamp(t, JST)
        data.setdefault(dt.date(), {})[dt.hour * 60 + dt.minute] = (ao, bo)
    allr, got, nongot, sepoct = [], [], [], []
    for d, mins in sorted(data.items()):
        if d.weekday() not in (1, 2, 3, 4):  # exit Tue-Fri
            continue
        if 0 not in mins:
            continue
        exitmin = 335 if d.weekday() == 3 else 595  # Thu 05:35, else 09:55
        if exitmin not in mins:
            continue
        r = (mins[exitmin][1] - mins[0][0]) * 100
        if d >= cutoff:
            continue
        allr.append(r)
        (got if d.day % 5 == 0 else nongot).append(r)
        if datetime.date(2026, 9, 1) <= d:
            sepoct.append(r)
    print(f"{inst}: ALL(to 10-22) {stats(allr)}")
    print(f"   gotobi {stats(got)} | non-gotobi {stats(nongot)} | Sep-Oct2026 {stats(sepoct)}")

con.close()
