import sqlite3, os, datetime
import numpy as np, pandas as pd
DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
JST = datetime.timezone(datetime.timedelta(hours=9))
def load(inst):
    df = pd.read_sql_query("SELECT t,bo,bh,bl,bc,ao,ah,al,ac FROM m1 WHERE inst=? ORDER BY t", con, params=(inst,))
    df["mid_c"] = (df.bc + df.ac) / 2
    return df
jp = load("JP225_USD"); nas = load("NAS100_USD")
jp_idx = {t: i for i, t in enumerate(jp.t.values)}
nas_map = dict(zip(nas.t.values, nas.mid_c.values))
def epoch_jst(d, hh, mm=0):
    return int(datetime.datetime(d.year, d.month, d.day, hh, mm, tzinfo=JST).timestamp())
dates = sorted(set(datetime.datetime.fromtimestamp(t, JST).date() for t in jp.t.values))
def run(mode, TP=150, SL=20, max_hold=180):
    trades=[]
    for d in dates:
        if mode=="nas":
            t0=epoch_jst(d,0); t8=epoch_jst(d,8)
            if t0 not in nas_map or t8 not in nas_map: continue
            if nas_map[t8]/nas_map[t0]-1 <= 0.002: continue
        te=epoch_jst(d,9,1)
        if te not in jp_idx: continue
        i0=jp_idx[te]; entry=jp.ao.values[i0]
        target=entry+TP; stop=entry-SL; end_t=te+max_hold*60
        xp=None; j=i0; last=i0
        while jp.t.values[j] <= end_t:
            last=j
            if jp.bl.values[j] <= stop and jp.bh.values[j] >= target: xp=stop; break
            if jp.bl.values[j] <= stop: xp=stop; break
            if jp.bh.values[j] >= target: xp=target; break
            if jp.t.values[j] >= end_t or j+1>=len(jp) or jp.t.values[j+1]-jp.t.values[j]>120: break
            j+=1
        if xp is None: xp=jp.bc.values[last]
        trades.append(xp-entry)
    p=np.array(trades)
    if len(p)==0: print(mode,"none"); return
    wins=p[p>0]; losses=p[p<=0]
    pf=wins.sum()/-losses.sum() if losses.sum()!=0 else float('inf')
    print(f"{mode} TP{TP}/SL{SL}: trades={len(p)} total={p.sum():+.0f} winrate={(p>0).mean()*100:.1f}% PF={pf:.2f} avg={p.mean():+.1f}")
for mode in ["none","nas"]:
    for TP,SL in [(150,20),(150,30),(100,40)]:
        run(mode,TP,SL)
print("DONE-BASE")
