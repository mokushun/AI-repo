"""
MT5 ストラテジーテスターレポート (.xlsx) の約定解析 + m1.db との実行時計の突き合わせ

使い方: python3 scripts/parse_mt5_report.py <report.xlsx>
（省略時は ~/workspace/user/files/2300-0954.xlsx）

やること:
1. レポートの「約定」セクションを読み、銘柄ごと・方向ごと（ロング/ショート）に
   in/out を対応付け（買いポジションと売りポジションは別キューで FIFO）
2. ペア別・方向別の PF を「損益のみ（価格のみ）」と「損益+スワップ」で集計。
   USD/JPY ロングは年別（決済年）も出す
3. USD/JPY の約定価格を m1.db の始値と突き合わせ、実際の約定時刻 (JST) を判定する。
   レポートの時刻はサーバー時刻。候補は server+6h（サーバー GMT+3 のときの JST）と
   server+7h（GMT+2 のときの JST）の2つで、価格差が小さい方を採用する。
   これにより夏/冬で実行時刻がずれていたかが分かる
4. m1.db 側で「意図した仕様（23:00->09:54、木曜 04:54）」の PF を再計算して照合する

定義:
- ロング約定価格の基準: IN は ask 始値 (ao)、OUT は bid 始値 (bo)
  ショートは IN が bid 始値、OUT が ask 始値
- 価格の一致 tolerance は 1.5 pip 以内を「分類できた」とする
"""

import os
import sqlite3
import sys
from collections import defaultdict, deque
from math import sqrt

import numpy as np
import pandas as pd

REPORT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/workspace/user/files/2300-0954.xlsx")
DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PIP = 100.0


def pf(series):
    s = pd.Series(series, dtype=float)
    pos, neg = s[s > 0].sum(), -s[s < 0].sum()
    return pos / neg if neg > 0 else float("nan")


def load_deals(path):
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Sheet1"]
    rows = list(ws.iter_rows(values_only=True))
    # find deals header: row whose first cell is '約定' section followed by header '時間'
    start = None
    for i, r in enumerate(rows):
        if r[0] == "時間" and r[1] == "約定":
            start = i + 1
            break
    deals = []
    for r in rows[start:]:
        if r[0] is None or r[2] is None:
            continue
        deals.append(
            dict(
                time=pd.Timestamp(r[0]),
                deal=int(r[1]),
                symbol=r[2],
                type=r[3],
                inout=r[4],
                volume=float(r[5]),
                price=float(r[6]),
                commission=float(r[8] or 0),
                swap=float(r[9] or 0),
                profit=float(r[10] or 0),
            )
        )
    return pd.DataFrame(deals)


def pair_trades(deals):
    queues = defaultdict(deque)  # (symbol, 'long'/'short') -> deque of in-deals
    trades = []
    for d in deals.itertuples():
        if d.type not in ("buy", "sell"):
            continue
        if d.inout == "in":
            pos = "long" if d.type == "buy" else "short"
            queues[(d.symbol, pos)].append(d)
        elif d.inout == "out":
            pos = "short" if d.type == "buy" else "long"  # buy-out closes a short, sell-out closes a long
            q = queues[(d.symbol, pos)]
            if not q:
                print(f"WARN: out deal without open: {d}")
                continue
            o = q.popleft()
            trades.append(
                dict(
                    symbol=d.symbol,
                    side=pos,
                    open_time=o.time,
                    open_price=o.price,
                    close_time=d.time,
                    close_price=d.price,
                    volume=o.volume,
                    profit=d.profit,
                    swap=d.swap,
                    commission=d.commission,
                )
            )
    leftover = sum(len(q) for q in queues.values())
    return pd.DataFrame(trades), leftover


def main():
    print(f"=== REPORT: {REPORT} ===")
    deals = load_deals(REPORT)
    print(f"deals loaded: {len(deals)} ({deals.time.min()} .. {deals.time.max()})")
    trades, leftover = pair_trades(deals)
    print(f"paired trades: {len(trades)}, leftover open: {leftover}")
    print(f"volumes: {sorted(trades.volume.unique())}")

    trades["profit_incl"] = trades.profit + trades.swap + trades.commission

    print("\n=== PF BY SYMBOL x SIDE ===")
    for (sym, side), g in trades.groupby(["symbol", "side"]):
        print(
            f"{sym} {side}: n={len(g)} PF(price)={pf(g.profit):.3f} PF(incl swap)={pf(g.profit_incl):.3f} "
            f"net(price)={g.profit.sum():+.2f} swap_total={g.swap.sum():+.2f}"
        )
    print("\n=== PF ALL ===")
    for side, g in trades.groupby("side"):
        print(f"{side}: n={len(g)} PF(price)={pf(g.profit):.3f} PF(incl)={pf(g.profit_incl):.3f} net(incl)={g.profit_incl.sum():+.2f}")
    print(f"ALL: n={len(trades)} PF(price)={pf(trades.profit):.3f} PF(incl)={pf(trades.profit_incl):.3f} net(incl)={trades.profit_incl.sum():+.2f}")

    uj = trades[(trades.symbol == "USDJPY") & (trades.side == "long")].copy()
    print("\n=== USDJPY LONG BY CLOSE YEAR ===")
    uj["close_year"] = uj.close_time.dt.year
    for y, g in uj.groupby("close_year"):
        print(f"{y}: n={len(g)} PF(price)={pf(g.profit):.3f} PF(incl)={pf(g.profit_incl):.3f} swap={g.swap.sum():+.2f}")
    print(f"ALL: n={len(uj)} PF(price)={pf(uj.profit):.3f} PF(incl)={pf(uj.profit_incl):.3f} swap={uj.swap.sum():+.2f}")

    # ---------- clock matching vs m1.db (USD/JPY) ----------
    print("\n=== CLOCK MATCH vs m1.db (USDJPY deals, price diff in pip) ===")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    m1 = pd.read_sql_query("SELECT t,bo,ao FROM m1 WHERE inst='USD_JPY' ORDER BY t", con)
    con.close()
    bo_d = {int(t): v for t, v in zip(m1.t, m1.bo)}
    ao_d = {int(t): v for t, v in zip(m1.t, m1.ao)}

    uj_deals = deals[deals.symbol == "USDJPY"].copy()
    # position side for each deal: in+buy=long open(ao), in+sell=short open(bo), out+sell=long close(bo), out+buy=short close(ao)
    import calendar as _cal

    def ref_price(epoch, deal):
        if deal.inout == "in":
            return ao_d.get(epoch) if deal.type == "buy" else bo_d.get(epoch)
        else:
            return bo_d.get(epoch) if deal.type == "sell" else ao_d.get(epoch)

    recs = []
    for d in uj_deals.itertuples():
        base_epoch = _cal.timegm(d.time.timetuple())  # server wall time read as if UTC
        cand = {}
        for label, addh in [("+6", 6), ("+7", 7)]:
            jst = d.time + pd.Timedelta(hours=addh)  # JST wall time (naive, for display/grouping)
            # UTC epoch of that JST wall time = base + addh - 9h
            rp = ref_price(base_epoch + (addh - 9) * 3600, d)
            cand[label] = (jst, None if rp is None else (d.price - rp) * PIP)
        a, b = cand["+6"], cand["+7"]
        chosen, pdiff = None, None
        if a[1] is not None and (b[1] is None or abs(a[1]) <= abs(b[1])):
            chosen, pdiff = "+6", a[1]
        elif b[1] is not None:
            chosen, pdiff = "+7", b[1]
        if chosen is not None and pdiff is not None and abs(pdiff) <= 1.5:
            jst = cand[chosen][0]
            recs.append(dict(server_time=d.time, type=d.type, inout=d.inout, chosen=chosen, pdiff=pdiff, jst=jst,
                             jst_month=jst.strftime("%Y-%m"), jst_hhmm=jst.strftime("%H:%M"), jst_dow=jst.dayofweek))
    m = pd.DataFrame(recs)
    print(f"classified deals: {len(m)} / {len(uj_deals)} USDJPY deals")
    if len(m):
        print("median |pdiff| by chosen:", m.groupby("chosen")["pdiff"].apply(lambda s: s.abs().median()).round(3).to_dict())
        for inout, label in [("in", "ENTRY (long open = server 17:00)"), ("out", "EXIT (long close / short open)")]:
            sub = m[(m.inout == inout)]
            print(f"\n-- {label}: monthly share of +6 (JST = server+6, server in GMT+3) --")
            piv = sub.groupby(["jst_month", "chosen"]).size().unstack(fill_value=0)
            if "+6" in piv and "+7" in piv:
                piv["pct_+6"] = (piv["+6"] / (piv["+6"] + piv["+7"]) * 100).round(0)
            print(piv.to_string())
        print("\n-- EXIT deals: JST hh:mm distribution by chosen offset --")
        ex = m[m.inout == "out"]
        print(ex.groupby(["chosen", "jst_hhmm"]).size().sort_values(ascending=False).head(12).to_string())
        print("\n-- ENTRY deals: JST hh:mm distribution by chosen offset --")
        en = m[m.inout == "in"]
        print(en.groupby(["chosen", "jst_hhmm"]).size().sort_values(ascending=False).head(12).to_string())
        # switch boundary: for long exits at server 03:54 (normal-day closes), list first/last dates per offset per year-half
        lx = m[(m.inout == "out") & (m.type == "sell")]
        print("\n-- long-exit classification ranges (JST date) --")
        for ch, g in lx.groupby("chosen"):
            print(f"{ch}: {g.jst.min()} .. {g.jst.max()} n={len(g)}")
        # transitions: sort by jst, show where chosen flips for consecutive long exits
        lx = lx.sort_values("jst")
        flips = lx[lx.chosen.ne(lx.chosen.shift())]
        print("flip points (long exits):")
        for r in flips.itertuples():
            print(f"  {r.jst} -> {r.chosen}")

    # ---------- m1.db reference PF for intended config ----------
    print("\n=== m1.db REFERENCE (USD/JPY long, price only, Tue-Fri exits) ===")
    price = {int(t): (bo, ao) for t, bo, ao in zip(m1.t, m1.bo, m1.ao)}

    def epoch_jst(date, hh, mm):
        return int((pd.Timestamp(date) + pd.Timedelta(hours=hh, minutes=mm)).tz_localize("Asia/Tokyo").timestamp())

    jst_dates = sorted(set(pd.to_datetime(m1.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo").dt.date))

    def m1_pf(entry, normal_exit, thu_exit, label):
        pnls = []
        for d in jst_dates:
            ts = pd.Timestamp(d)
            if ts.dayofweek not in (1, 2, 3, 4):
                continue
            xo = thu_exit if ts.dayofweek == 3 else normal_exit
            in_date = ts - pd.Timedelta(days=1) if entry[0] >= 12 else ts
            te = epoch_jst(in_date.date(), entry[0], entry[1])
            tx = epoch_jst(d, xo[0], xo[1])
            if te in price and tx in price:
                pnls.append((price[tx][0] - price[te][1]) * PIP)
        s = pd.Series(pnls)
        print(f"{label}: n={len(s)} mean={s.mean():+.2f}pip PF={pf(s):.3f}")
        return s

    m1_pf((23, 0), (9, 54), (4, 54), "intended 23:00->09:54 (Thu 04:54)")
    m1_pf((23, 0), (9, 54), (4, 55), "intended 23:00->09:54 (Thu 04:55)")
    m1_pf((23, 0), (9, 55), (5, 35), "grid-best hybrid 23:00->09:55 (Thu 05:35)")
    m1_pf((0, 0), (10, 54), (5, 54), "winter-shifted-as-executed 00:00->10:54 (Thu 05:54)")

    print("\nDONE")


if __name__ == "__main__":
    main()
