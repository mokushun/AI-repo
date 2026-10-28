"""
USD/JPY 仲値後のショート（9:56 売り）の検証

背景: ユーザーは「仲値到達後の 9:56 で売りを入れる」戦略を実際に半年売ったが結果は駄目だった。
EA 内蔵の売りレッグは 9:54 IN -> 11:00 OUT で、MT5 2回のレポートでも PF 0.91〜0.97。
ここでは m1.db で 9:56 IN 版を中心に、IN/OUT の組合せを固定仕様で測る。

定義（固定）:
- データ: m1.db、inst='USD_JPY'、読み取り専用。約定は指定時刻の足の始値
- ショート損益(pip) = bid始値(IN) - ask始値(OUT)。1 pip = 0.01円
- 対象日: JST の月〜金（9:56 のバーが存在する日）。仲値ロングと違い月曜も入れる
- IN 候補: 09:54（EA版の参照）/ 09:55 / 09:56（ユーザーの実売り）
- OUT 候補: 10:00 / 10:30 / 11:00（EA版） / 12:00 / 15:00
- 参考に mid ベースの gross（コスト前）も出す: mid始値(IN) - mid始値(OUT)
"""

import os
import sqlite3
from math import sqrt

import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PIP = 100.0

ENTRIES = [(9, 54), (9, 55), (9, 56)]
EXITS = [(10, 0), (10, 30), (11, 0), (12, 0), (15, 0)]


def pf(s):
    s = pd.Series(s, dtype=float)
    pos, neg = s[s > 0].sum(), -s[s < 0].sum()
    return pos / neg if neg > 0 else float("nan")


def line(name, s):
    s = pd.Series(s, dtype=float).dropna()
    if len(s) == 0:
        print(f"{name}: n=0")
        return
    t = s.mean() / (s.std(ddof=1) / sqrt(len(s))) if len(s) > 1 and s.std(ddof=1) > 0 else float("nan")
    print(f"{name}: n={len(s)} mean={s.mean():+.2f}pip PF={pf(s):.3f} win={100*(s>0).mean():.1f}% std={s.std(ddof=1):.1f} t={t:.2f}")


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT t,bo,ao FROM m1 WHERE inst='USD_JPY' ORDER BY t", con)
    con.close()
    price = {int(t): (bo, ao) for t, bo, ao in zip(df.t, df.bo, df.ao)}
    jst_dates = sorted(set(pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo").dt.date))

    def epoch_jst(date, hh, mm):
        return int((pd.Timestamp(date) + pd.Timedelta(hours=hh, minutes=mm)).tz_localize("Asia/Tokyo").timestamp())

    def build(e_hhmm, x_hhmm):
        rows = []
        for d in jst_dates:
            ts = pd.Timestamp(d)
            if ts.dayofweek not in (0, 1, 2, 3, 4):
                continue
            te = epoch_jst(d, *e_hhmm)
            tx = epoch_jst(d, *x_hhmm)
            if te not in price or tx not in price:
                continue
            bo_in, ao_in = price[te]
            bo_out, ao_out = price[tx]
            rows.append(
                dict(
                    date=d,
                    dow=ts.dayofweek,
                    year=ts.year,
                    gotobi=(ts.day % 5 == 0),
                    exec_pip=(bo_in - ao_out) * PIP,
                    mid_pip=(((bo_in + ao_in) / 2) - ((bo_out + ao_out) / 2)) * PIP,
                )
            )
        return pd.DataFrame(rows)

    print("=== SHORT ENTRY x EXIT (all Mon-Fri) ===")
    cache = {}
    for e in ENTRIES:
        for x in EXITS:
            r = build(e, x)
            cache[(e, x)] = r
            line(f"IN {e[0]:02d}:{e[1]:02d} -> OUT {x[0]:02d}:{x[1]:02d} exec", r.exec_pip)
            line(f"    (mid gross)", r.mid_pip)

    print("\n=== MAIN SPEC: IN 09:56 -> OUT 11:00 ===")
    r = cache[((9, 56), (11, 0))]
    line("ALL", r.exec_pip)
    print("-- by weekday (Mon=0) --")
    for w in range(5):
        line(f"dow{w}", r.loc[r.dow == w, "exec_pip"])
    print("-- gotobi --")
    line("gotobi", r.loc[r.gotobi, "exec_pip"])
    line("non-gotobi", r.loc[~r.gotobi, "exec_pip"])
    print("-- halves --")
    line("2020-10..2023", r.loc[r.year <= 2023, "exec_pip"])
    line("2024..2026-10", r.loc[r.year >= 2024, "exec_pip"])
    print("-- by year --")
    for y, g in r.groupby("year"):
        line(f"{y}", g.exec_pip)

    print("\n=== REFERENCE: EA SELL LEG IN 09:54 -> OUT 11:00 ===")
    r2 = cache[((9, 54), (11, 0))]
    line("ALL", r2.exec_pip)
    for w in range(5):
        line(f"dow{w}", r2.loc[r2.dow == w, "exec_pip"])

    print("\nDONE (price only; swap NOT included)")


if __name__ == "__main__":
    main()
