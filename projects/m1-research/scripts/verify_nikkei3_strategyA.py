import sqlite3, os, datetime
import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
JST = datetime.timezone(datetime.timedelta(hours=9))

def load(inst):
    df = pd.read_sql_query("SELECT t,bo,bh,bl,bc,ao,ah,al,ac FROM m1 WHERE inst=? ORDER BY t", con, params=(inst,))
    df["mid_c"] = (df.bc + df.ac) / 2
    return df

jp = load("JP225_USD"); nas = load("NAS100_USD"); usd = load("USD_JPY")
jp["ema9"] = jp.mid_c.ewm(span=9, adjust=False).mean()
jp["ema21"] = jp.mid_c.ewm(span=21, adjust=False).mean()
usd["ema21"] = usd.mid_c.ewm(span=21, adjust=False).mean()

jp_idx = {t: i for i, t in enumerate(jp.t.values)}
nas_map = dict(zip(nas.t.values, nas.mid_c.values))
usd_map = dict(zip(usd.t.values, usd.mid_c.values))
usd_ema_map = dict(zip(usd.t.values, usd.ema21.values))
jp_ema9 = jp.ema9.values; jp_ema21 = jp.ema21.values

def epoch_jst(date, hh, mm=0):
    return int(datetime.datetime(date.year, date.month, date.day, hh, mm, tzinfo=JST).timestamp())

dates = sorted(set(datetime.datetime.fromtimestamp(t, JST).date() for t in jp.t.values))

def run(variant="all3", TP=150, SL=20, max_hold=180):
    trades = []
    for d in dates:
        t0 = epoch_jst(d, 0); t8 = epoch_jst(d, 8)
        if t0 not in nas_map or t8 not in nas_map: continue
        nas_ret = nas_map[t8] / nas_map[t0] - 1
        if nas_ret <= 0.002: continue
        te = epoch_jst(d, 9, 1)
        if te not in jp_idx: continue
        i0 = jp_idx[te]
        t_pre = epoch_jst(d, 9, 0)
        if variant in ("all3", "nas_jpema", "nas_usd"):
            if t_pre not in jp_idx: continue
            ip = jp_idx[t_pre]
        if variant in ("all3", "nas_jpema"):
            if not (jp_ema9[ip] > jp_ema21[ip]): continue
        if variant in ("all3", "nas_usd"):
            if t_pre not in usd_map: continue
            if not (usd_map[t_pre] > usd_ema_map[t_pre]): continue
        entry = jp.ao.values[i0]  # buy at ask open 9:01
        target = entry + TP; stop = entry - SL
        end_t = min(te + max_hold * 60, epoch_jst(d, 14, 50))
        exit_price = None; exit_reason = None
        j = i0; last = i0
        while jp.t.values[j] <= end_t:
            last = j
            bh = jp.bh.values[j]; bl = jp.bl.values[j]
            if bl <= stop and bh >= target:
                exit_price = stop; exit_reason = "SL(same-bar)"; break
            if bl <= stop:
                exit_price = stop; exit_reason = "SL"; break
            if bh >= target:
                exit_price = target; exit_reason = "TP"; break
            if jp.t.values[j] >= end_t: break
            if j + 1 >= len(jp) or jp.t.values[j+1] - jp.t.values[j] > 120: break
            j += 1
        if exit_price is None:
            exit_price = jp.bc.values[last]; exit_reason = "force"
        pnl = exit_price - entry
        trades.append((d.year, pnl, exit_reason))
    return trades

def summarize(label, trades):
    if not trades:
        print(label, "no trades"); return
    pnl = np.array([p for _, p, _ in trades])
    wins = pnl[pnl > 0]; losses = pnl[pnl <= 0]
    pf = wins.sum() / -losses.sum() if losses.sum() != 0 else float("inf")
    print(f"{label}: trades={len(pnl)} total={pnl.sum():+.0f} winrate={(pnl>0).mean()*100:.1f}% PF={pf:.2f} avg={pnl.mean():+.1f}")
    by = {}
    for y, p, _ in trades: by.setdefault(y, []).append(p)
    for y in sorted(by):
        a = np.array(by[y]); print(f"   {y}: n={len(a)} pnl={a.sum():+.0f} winrate={(a>0).mean()*100:.1f}%")

for variant in ["nas_only", "all3"]:
    for TP, SL, hold in [(150,20,180),(150,20,351),(150,30,180),(120,25,180),(150,25,351),(120,30,351)]:
        summarize(f"{variant} TP{TP}/SL{SL} hold{hold}m", run(variant, TP, SL, hold))
print("DONE3")
