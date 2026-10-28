"""
USD/JPY 仲値トレード：現行仕様の事前実測（価格のみ、スワップは含まない）

ユーザーの現行仕様（2026-10-28 提示、時刻はすべて JST）:
- トレードA: 前日 23:00 IN -> 当日 08:54 OUT（木曜日は 04:55 OUT）
- トレードB: 当日 00:00 IN -> 当日 09:54 OUT（木曜日は 05:55 OUT）
- 「木曜日」は決済日（OUT の日）の曜日とする。木曜の早期決済はスワップ前決済のため
  （スワップ自体は m1.db に無いので、このスクリプトは価格変動分だけを測る）

定義（固定）:
- データ: m1.db、inst='USD_JPY'、読み取り専用
- 約定価格は指定時刻の足の「始値」: IN/OUT ともその分ちょうどのバーの bo/ao を使う
  （08:54 OUT = 08:54:00 の価格）
- ロング（買い→売り）: OUT の bid 始値 - IN の ask 始値
  ショート（売り→買い）: IN の bid 始値 - OUT の ask 始値。単位は pip（1 pip = 0.01円）
- 方向が未確認のため両方向を集計する
- 対象日: OUT 側のバーが存在する JST 日付のみ（祝日等で欠けたらその日は数えない）
- ゴトー日: OUT 日の日が 5 の倍数の日（5,10,15,20,25,30）
"""

import os
import sqlite3
from math import sqrt

import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PIP = 100.0


def summarize(name, s):
    s = pd.Series(s).dropna()
    if len(s) == 0:
        print(f"{name}: n=0")
        return
    t_val = s.mean() / (s.std(ddof=1) / sqrt(len(s))) if len(s) > 1 and s.std(ddof=1) > 0 else float("nan")
    print(
        f"{name}: n={len(s)} mean={s.mean():+.2f}pip total={s.sum():+.0f}pip win={100*(s>0).mean():.1f}% "
        f"std={s.std(ddof=1):.1f}pip t={t_val:.2f} min={s.min():+.0f} max={s.max():+.0f}"
    )


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT t,bo,ao FROM m1 WHERE inst='USD_JPY' ORDER BY t", con)
    con.close()
    # epoch -> (bid_open, ask_open)
    price = {int(t): (bo, ao) for t, bo, ao in zip(df.t, df.bo, df.ao)}

    def epoch_jst(date, hh, mm):
        ts = pd.Timestamp(date) + pd.Timedelta(hours=hh, minutes=mm)
        ts = ts.tz_localize("Asia/Tokyo")
        return int(ts.timestamp())

    jst_s = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo")
    all_dates = sorted(set(jst_s.dt.date))

    specs = {
        "A 23:00->08:54 (Thu 04:55)": dict(in_day_offset=-1, in_hh=23, in_mm=0, out_hh=8, out_mm=54, thu_hh=4, thu_mm=55),
        "B 00:00->09:54 (Thu 05:55)": dict(in_day_offset=0, in_hh=0, in_mm=0, out_hh=9, out_mm=54, thu_hh=5, thu_mm=55),
    }

    for name, sp in specs.items():
        rows = []
        for d in all_dates:
            thu = pd.Timestamp(d).dayofweek == 3  # Thursday = exit date weekday
            out_hh, out_mm = (sp["thu_hh"], sp["thu_mm"]) if thu else (sp["out_hh"], sp["out_mm"])
            in_date = pd.Timestamp(d) + pd.Timedelta(days=sp["in_day_offset"])
            te = epoch_jst(in_date.date(), sp["in_hh"], sp["in_mm"])
            tx = epoch_jst(d, out_hh, out_mm)
            if te not in price or tx not in price:
                continue
            bo_in, ao_in = price[te]
            bo_out, ao_out = price[tx]
            rows.append(
                dict(
                    date=d,
                    dow=pd.Timestamp(d).dayofweek,
                    year=pd.Timestamp(d).year,
                    gotobi=(pd.Timestamp(d).day % 5 == 0),
                    long_pip=(bo_out - ao_in) * PIP,
                    short_pip=(bo_in - ao_out) * PIP,
                )
            )
        r = pd.DataFrame(rows)
        print(f"\n=== TRADE {name} ===")
        print(f"days with both bars: {len(r)} ({r.date.min()} .. {r.date.max()})")
        summarize("LONG  all", r.long_pip)
        summarize("SHORT all", r.short_pip)
        print("-- LONG by year --")
        for y, g in r.groupby("year"):
            summarize(f"  {y}", g.long_pip)
        print("-- LONG by exit weekday (Mon=0) --")
        for w, g in r.groupby("dow"):
            summarize(f"  dow{w}", g.long_pip)
        print("-- LONG gotobi vs other --")
        summarize("  gotobi", r.loc[r.gotobi, "long_pip"])
        summarize("  non-gotobi", r.loc[~r.gotobi, "long_pip"])
        print("-- LONG first half / second half --")
        summarize("  2020-10..2023-12", r.loc[r.year <= 2023, "long_pip"])
        summarize("  2024-01..2026-10", r.loc[r.year >= 2024, "long_pip"])

    print("\nDONE (price only; swap NOT included)")


if __name__ == "__main__":
    main()
