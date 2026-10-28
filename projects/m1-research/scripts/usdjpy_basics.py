"""
USD/JPY スキャルピング基礎統計（コストの壁の実測）

定義（固定仕様、2026-10-27）:
- データ: m1.db テーブル m1、inst='USD_JPY' のみ、読み取り専用で開く
- 価格: bid OHLC = bo/bh/bl/bc、ask OHLC = ao/ah/al/ac（単位: 円）
- スプレッド（終値）spread_c = ac - bc、スプレッド（始値）spread_o = ao - bo
- 1分足レンジ range_bid = bh - bl（bid の高値-安値）
- mid 終値 mid_c = (bc + ac) / 2、リターンは mid_c の差分（円）。表示は円と pip の両方
  （USD/JPY の 1 pip = 0.01 円。差分・幅の pip 換算は 円 x 100）
- 時刻: t は epoch 秒（UTC）。時間帯集計は JST（Asia/Tokyo）の「足の開始時刻の時」
- 1分リターンは dt == 60 秒の連続足のみ。将来 h 分リターンは
  t の h 本先との差がちょうど h*60 秒のときだけ有効とする（欠損をまたがない）
- EMA: mid_c に対する EMA9 / EMA21（ewm span=9/21, adjust=False、全行を時系列順に通す。
  週末等のギャップをまたいでもリセットしない＝先行検証 verify_nikkei.py と同じ流儀）
- 実行可能リターン（コスト込みの近似ではなく実価格）:
  ロング h 分保有 = bc(t+h) - ac(t)（ask で買って bid で売る）
  ショート h 分保有 = bc(t) - ac(t+h)（bid で売って ask で買う）
  mid リターンから往復でおよそスプレッド1回分を引いた値になる
- 有意性: 将来リターンは重複サンプルになるため、h 本間引き（h 分おきに1件）の
  t 値を必ず併記する。間引き開始位置は先頭（位置 0）で固定
"""

import os
import sqlite3
from math import sqrt

import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PIP = 100.0  # 円 -> pip 換算（差分・幅のみ）


def pips(x):
    return x * PIP


def stats_line(name, s):
    s = s.dropna()
    if len(s) == 0:
        print(f"{name}: n=0")
        return
    t_val = s.mean() / (s.std(ddof=1) / sqrt(len(s))) if len(s) > 1 and s.std(ddof=1) > 0 else float("nan")
    print(
        f"{name}: n={len(s)} mean={s.mean():+.4f}円 ({pips(s.mean()):+.2f}pip) "
        f"median={s.median():+.4f}円 std={s.std(ddof=1):.4f}円 t={t_val:.1f}"
    )


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT t,bo,bh,bl,bc,ao,ah,al,ac FROM m1 WHERE inst='USD_JPY' ORDER BY t",
        con,
    )
    con.close()

    df["spread_c"] = df.ac - df.bc
    df["spread_o"] = df.ao - df.bo
    df["range_bid"] = df.bh - df.bl
    df["mid_c"] = (df.bc + df.ac) / 2
    df["jst"] = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo")
    df["hour"] = df.jst.dt.hour
    df["year"] = df.jst.dt.year
    df["dt"] = df.t.diff()

    print("=== 0. LOAD (USD_JPY) ===")
    print(f"rows={len(df)}")
    print(f"period UTC: {pd.to_datetime(df.t.iloc[0], unit='s', utc=True)} -> {pd.to_datetime(df.t.iloc[-1], unit='s', utc=True)}")
    print(f"period JST: {df.jst.iloc[0]} -> {df.jst.iloc[-1]}")
    print(f"mid_c first={df.mid_c.iloc[0]:.3f} last={df.mid_c.iloc[-1]:.3f} total_change={df.mid_c.iloc[-1]-df.mid_c.iloc[0]:+.3f}円")
    n_days = (df.jst.dt.date.nunique())
    print(f"distinct JST dates={n_days} drift_per_date={(df.mid_c.iloc[-1]-df.mid_c.iloc[0])/n_days:+.4f}円/day")
    print(f"gaps: dt==60: {(df.dt==60).sum()}, dt>60: {(df.dt>60).sum()}, dt max={df.dt.max():.0f}s")

    print("\n=== 1. SPREAD / RANGE (overall) ===")
    for col in ["spread_c", "spread_o", "range_bid"]:
        s = df[col]
        print(
            f"{col}: mean={s.mean():.4f}円 ({pips(s.mean()):.3f}pip) median={s.median():.4f}円 ({pips(s.median()):.3f}pip) "
            f"p90={s.quantile(0.90):.4f} p99={s.quantile(0.99):.4f} min={s.min():.4f} max={s.max():.4f}"
        )
    ratio_means_c = df.spread_c.mean() / df.range_bid.mean() * 100
    ratio_means_o = df.spread_o.mean() / df.range_bid.mean() * 100
    print(f"ratio mean(spread_c)/mean(range_bid) = {ratio_means_c:.1f}%")
    print(f"ratio mean(spread_o)/mean(range_bid) = {ratio_means_o:.1f}%")
    per_bar = (df.spread_c / df.range_bid.replace(0, np.nan)).dropna()
    print(f"per-bar ratio spread_c/range_bid: mean={per_bar.mean()*100:.1f}% median={per_bar.median()*100:.1f}%")
    print(f"share range_bid <= spread_c: {(df.range_bid <= df.spread_c).mean()*100:.2f}%")
    print(f"share range_bid <= 2*spread_c: {(df.range_bid <= 2*df.spread_c).mean()*100:.2f}%")
    print(f"share spread_c == 0: {(df.spread_c==0).mean()*100:.3f}%  share spread_c < 0: {(df.spread_c<0).mean()*100:.3f}%")

    print("\n--- by JST hour ---")
    g = df.groupby("hour").agg(
        spread_c_mean=("spread_c", "mean"),
        spread_c_med=("spread_c", "median"),
        spread_o_mean=("spread_o", "mean"),
        range_mean=("range_bid", "mean"),
        range_med=("range_bid", "median"),
        n=("t", "size"),
    )
    g["ratio_c_%"] = g.spread_c_mean / g.range_mean * 100
    g["spread_c_pip"] = pips(g.spread_c_mean)
    g["range_pip"] = pips(g.range_mean)
    print(g[["spread_c_pip", "spread_o_mean", "range_pip", "ratio_c_%", "n"]].round(3).to_string())
    best = g["ratio_c_%"].idxmin()
    worst = g["ratio_c_%"].idxmax()
    print(f"lowest ratio hour JST {best}: {g.loc[best,'ratio_c_%']:.1f}% (spread {pips(g.loc[best,'spread_c_mean']):.2f}pip, range {pips(g.loc[best,'range_mean']):.2f}pip)")
    print(f"highest ratio hour JST {worst}: {g.loc[worst,'ratio_c_%']:.1f}% (spread {pips(g.loc[worst,'spread_c_mean']):.2f}pip, range {pips(g.loc[worst,'range_mean']):.2f}pip)")

    print("\n--- sensitivity: excluding JST 05,06,07 (wide-spread early-morning hours) ---")
    liq = df[~df.hour.isin([5, 6, 7])]
    print(f"rows={len(liq)} ({len(liq)/len(df)*100:.1f}% of all)")
    print(
        f"spread_c: mean={liq.spread_c.mean():.4f}円 ({pips(liq.spread_c.mean()):.3f}pip) median={liq.spread_c.median():.4f}円 "
        f"range_bid: mean={liq.range_bid.mean():.4f}円 ({pips(liq.range_bid.mean()):.3f}pip) median={liq.range_bid.median():.4f}円"
    )
    print(f"ratio mean(spread_c)/mean(range_bid) ex-05-07 = {liq.spread_c.mean()/liq.range_bid.mean()*100:.1f}%")
    print(f"share range_bid <= spread_c ex-05-07 = {(liq.range_bid <= liq.spread_c).mean()*100:.2f}%")

    print("\n--- by JST year ---")
    gy = df.groupby("year").agg(
        spread_c_mean=("spread_c", "mean"),
        range_mean=("range_bid", "mean"),
        n=("t", "size"),
    )
    gy["ratio_c_%"] = gy.spread_c_mean / gy.range_mean * 100
    gy["spread_c_pip"] = pips(gy.spread_c_mean)
    gy["range_pip"] = pips(gy.range_mean)
    print(gy[["spread_c_pip", "range_pip", "ratio_c_%", "n"]].round(3).to_string())

    print("\n=== 2. AUTOCORR of 1-min mid returns ===")
    df["ret1"] = np.where(df.dt == 60, df.mid_c.diff(), np.nan)
    df["logret1"] = np.where(df.dt == 60, np.log(df.mid_c).diff(), np.nan)
    r = pd.Series(df["ret1"]).dropna()
    print(f"ret1: n={len(r)} mean={r.mean():+.5f}円 ({pips(r.mean()):+.3f}pip) std={r.std(ddof=1):.5f}円 ({pips(r.std(ddof=1)):.2f}pip) mean_abs={r.abs().mean():.5f}円 ({pips(r.abs().mean()):.2f}pip)")
    for lag in [1, 2, 5, 10]:
        ok = (df.t - df.t.shift(lag)) == lag * 60
        x = df.loc[ok, "ret1"]
        y = df["ret1"].shift(lag)[ok]
        pair = pd.concat([x, y], axis=1).dropna()
        lr_x = df.loc[ok, "logret1"]
        lr_y = df["logret1"].shift(lag)[ok]
        lpair = pd.concat([lr_x, lr_y], axis=1).dropna()
        ac = np.corrcoef(pair.iloc[:, 0], pair.iloc[:, 1])[0, 1]
        ac_log = np.corrcoef(lpair.iloc[:, 0], lpair.iloc[:, 1])[0, 1]
        print(f"lag{lag}: autocorr(pts)={ac:+.4f} autocorr(log)={ac_log:+.4f} n={len(pair)}")

    print("\n=== 3. FUTURE MID MOVE vs SPREAD (unconditional) ===")
    spread_mean = df.spread_c.mean()
    for h in [1, 5, 10]:
        ok = (df.t.shift(-h) - df.t) == h * 60
        fut = (df.mid_c.shift(-h) - df.mid_c)[ok]
        long_exec = (df.bc.shift(-h) - df.ac)[ok]
        short_exec = (df.bc - df.ac.shift(-h))[ok]
        stats_line(f"fut{h} mid", fut)
        print(f"  mean_abs fut{h} = {fut.abs().mean():.4f}円 ({pips(fut.abs().mean()):.2f}pip) = {fut.abs().mean()/spread_mean:.2f} x mean_spread_c")
        print(f"  share |fut{h}| > spread_c(at entry) = {((fut.abs() > df.spread_c[ok]).mean()*100):.1f}%")
        stats_line(f"fut{h} long_exec (bc_fut - ac_now)", long_exec)
        stats_line(f"fut{h} short_exec (bc_now - ac_fut)", short_exec)

    print("\n=== 4. EMA9>EMA21: FUTURE RETURNS ===")
    df["ema9"] = df.mid_c.ewm(span=9, adjust=False).mean()
    df["ema21"] = df.mid_c.ewm(span=21, adjust=False).mean()
    df["bull"] = df.ema9 > df.ema21
    for h in [1, 5, 10]:
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
        sub = sub[sub.ok].reset_index(drop=True)
        bull = sub[sub.bull]
        bear = sub[~sub.bull]
        print(f"\n-- horizon {h} min (valid n={len(sub)}) --")
        stats_line(f"fut{h} unconditional mid", sub.fut)
        stats_line(f"fut{h} bull (EMA9>EMA21) mid", bull.fut)
        stats_line(f"fut{h} bear (EMA9<=EMA21) mid", bear.fut)
        # thinned (every h-th row from position 0): non-overlapping
        th = sub.iloc[::h]
        th_bull = th[th.bull]
        th_bear = th[~th.bull]
        print("  [thinned, non-overlapping]")
        stats_line(f"fut{h} unconditional mid (thinned)", th.fut)
        stats_line(f"fut{h} bull mid (thinned)", th_bull.fut)
        stats_line(f"fut{h} bear mid (thinned)", th_bear.fut)
        if len(th_bull) > 1 and len(th_bear) > 1:
            diff = th_bull.fut.mean() - th_bear.fut.mean()
            se = sqrt(th_bull.fut.std(ddof=1) ** 2 / len(th_bull) + th_bear.fut.std(ddof=1) ** 2 / len(th_bear))
            excess_bull = th_bull.fut.mean() - th.fut.mean()
            print(f"  bull-bear diff (thinned) = {diff:+.4f}円 ({pips(diff):+.2f}pip) Welch t={diff/se:.2f}")
            print(f"  bull excess over unconditional (thinned) = {excess_bull:+.4f}円 ({pips(excess_bull):+.2f}pip)")
            print(f"  mean spread_c = {spread_mean:.4f}円 ({pips(spread_mean):.2f}pip): bull excess / spread = {excess_bull/spread_mean:.2f}")
        # executable under the condition: bull -> long, bear -> short
        stats_line(f"fut{h} bull long_exec", bull.long_exec)
        stats_line(f"fut{h} bear short_exec", bear.short_exec)
        th_bull_exec = th_bull.long_exec
        th_bear_exec = th_bear.short_exec
        print("  [thinned executable]")
        stats_line(f"fut{h} bull long_exec (thinned)", th_bull_exec)
        stats_line(f"fut{h} bear short_exec (thinned)", th_bear_exec)

    print("\nDONE")


if __name__ == "__main__":
    main()
