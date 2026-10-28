"""
USD/JPY 仲値トレード：IN/OUT 時刻の最適化検証（価格のみ、スワップなし、ロングのみ）

前提（ユーザー確認済み 2026-10-28）:
- ロングのみ、毎日実行。現行: A 23:00->08:54、B 00:00->09:54（JST）、
  木曜のみ A 04:55 / B 05:55 に早期決済（スワップ前）
- ゴトー日は出来高を増やす運用。ここでは価格の検証のみ

定義（固定）:
- データ: m1.db、inst='USD_JPY'、読み取り専用。価格は指定時刻の足の始値（bo/ao）
- ロング損益(pip) = bid始値(OUT) - ask始値(IN)。1 pip = 0.01円
- 対象日 D は「決済日（仲値の日、JST）」。ウィンドウは D-1 20:00 〜 D 10:30 JST
- 平均経路: mid始値 ((bo+ao)/2) の D-1 20:00 起点の累積変化（pip）を1分単位で平均
- グリッド: IN は 20:00〜02:00 を15分刻み、OUT は 04:00〜10:30 を5分刻み
  ＋ 08:54 / 09:54（現行 OUT）を追加。IN<OUT は自動で満たされる
- 過適合対策: 前半（決済年 <=2023）/ 後半（>=2024）で同じ組合せを評価し、
  上位組合せには近傍（IN ±15分 × OUT ±10分）の平均（プラトー）も併記する
- 木曜は別枠: 現行の早期決済と通常時刻決済を価格だけで比較する
"""

import os
import sqlite3
from math import sqrt

import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PIP = 100.0

OFF_START, OFF_END = -240, 630  # D-1 20:00 .. D 10:30 (minutes from D 00:00 JST)
OFFS = np.arange(OFF_START, OFF_END + 1)
ENTRY_OFFS = list(range(-240, 121, 15))            # 20:00 .. 02:00
EXIT_OFFS = sorted(set(list(range(240, 631, 5)) + [534, 594]))  # 04:00..10:30 + 08:54, 09:54


def fmt(off):
    m = off % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}" + ("(前日)" if off < 0 else "")


def combo_stats(pnl):
    """pnl: (days, n_entry, n_exit) array with NaN -> mean/t/n cube stats per combo."""
    valid = ~np.isnan(pnl)
    n = valid.sum(axis=0)
    s = np.nansum(pnl, axis=0)
    mean = np.where(n > 0, s / np.maximum(n, 1), np.nan)
    return mean, n


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT t,bo,ao FROM m1 WHERE inst='USD_JPY' ORDER BY t", con)
    con.close()
    bo_d = {int(t): v for t, v in zip(df.t, df.bo)}
    ao_d = {int(t): v for t, v in zip(df.t, df.ao)}

    jst_s = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo")
    all_dates = sorted(set(jst_s.dt.date))

    nd = len(all_dates)
    nm = len(OFFS)
    BO = np.full((nd, nm), np.nan)
    AO = np.full((nd, nm), np.nan)
    meta = []
    for i, d in enumerate(all_dates):
        base = int(pd.Timestamp(d).tz_localize("Asia/Tokyo").timestamp())
        for k, off in enumerate(OFFS):
            te = base + int(off) * 60
            if te in bo_d:
                BO[i, k] = bo_d[te]
                AO[i, k] = ao_d[te]
        ts = pd.Timestamp(d)
        meta.append(dict(date=d, dow=ts.dayofweek, year=ts.year, gotobi=(ts.day % 5 == 0)))
    meta = pd.DataFrame(meta)
    MID = (BO + AO) / 2

    ei = [int(np.where(OFFS == o)[0][0]) for o in ENTRY_OFFS]
    xi = [int(np.where(OFFS == o)[0][0]) for o in EXIT_OFFS]

    def pnl_cube(mask):
        idx = np.where(mask)[0]
        # (days_sub, entry, exit) = BO_exit - AO_entry
        return (BO[np.ix_(idx, xi)][:, None, :] - AO[np.ix_(idx, ei)][:, :, None]) * PIP

    # Analysis universe = exit dates Tue-Fri only (the user's actual trading days).
    # Mon exits are impossible for night entries (market closed Sun night) and Sat has
    # no Tokyo fixing; including Sat would let early-morning exits harvest Friday-night
    # US-session moves that are not nakane trades.
    base_mask = meta.dow.isin([1, 2, 3, 4]).values
    masks = {
        "ALL": base_mask,
        "H1<=2023": base_mask & (meta.year <= 2023).values,
        "H2>=2024": base_mask & (meta.year >= 2024).values,
        "GOTOBI": base_mask & meta.gotobi.values,
        "NON-GOTOBI": base_mask & (~meta.gotobi).values,
        "THU": (meta.dow == 3).values,
    }
    cubes = {k: pnl_cube(m) for k, m in masks.items()}
    means = {k: combo_stats(v)[0] for k, v in cubes.items()}
    ns = {k: combo_stats(v)[1] for k, v in cubes.items()}

    def cell(subset, entry_off, exit_off):
        i = ENTRY_OFFS.index(entry_off)
        j = EXIT_OFFS.index(exit_off)
        return means[subset][i, j], ns[subset][i, j]

    def plateau(subset, entry_off, exit_off):
        i = ENTRY_OFFS.index(entry_off)
        j = EXIT_OFFS.index(exit_off)
        ii = [k for k in (i - 1, i, i + 1) if 0 <= k < len(ENTRY_OFFS)]
        jj = [k for k, o in enumerate(EXIT_OFFS) if abs(o - exit_off) <= 10]
        return np.nanmean(means[subset][np.ix_(ii, jj)])

    print("=== 0. DAYS ===")
    print(f"candidate exit dates: {nd} ({meta.date.min()} .. {meta.date.max()}); analysis universe Tue-Fri exits: {int(base_mask.sum())}")
    print("by dow (Mon=0):", meta.dow.value_counts().sort_index().to_dict())

    print("\n=== 1. AVERAGE PATH (mid, pip from D-1 20:00, 15-min steps) ===")
    anchor = int(np.where(OFFS == -240)[0][0])
    k955 = int(np.where(OFFS == 595)[0][0])
    path = (MID - MID[:, [anchor]]) * PIP
    # To limit composition changes across minutes, path days must have both the
    # anchor bar (D-1 20:00) and the 09:55 bar.
    complete = (~np.isnan(MID[:, anchor])) & (~np.isnan(MID[:, k955]))
    for label, mask0 in [("ALL", masks["ALL"]), ("GOTOBI", masks["GOTOBI"]), ("NON-GOTOBI", masks["NON-GOTOBI"]),
                        ("H1<=2023", masks["H1<=2023"]), ("H2>=2024", masks["H2>=2024"])]:
        mask = mask0 & complete
        sub = path[mask]
        vals = np.nanmean(sub, axis=0)
        line = " ".join(f"{fmt(o)}:{vals[int(np.where(OFFS==o)[0][0])]:+.1f}" for o in range(-240, 631, 30))
        print(f"{label} (n_days_with_anchor={np.sum(~np.isnan(sub[:,0]))})")
        print("  " + line)
    # key minutes on the ALL path
    vals_all = np.nanmean(path[masks["ALL"] & complete], axis=0)
    for o in [-60, 0, 295, 355, 534, 555, 594, 595, 600, 630]:
        k = int(np.where(OFFS == o)[0][0])
        print(f"  path ALL at {fmt(o)}: {vals_all[k]:+.2f} pip")
    k_range = [int(np.where(OFFS == o)[0][0]) for o in range(360, 631)]  # 06:00..10:30
    seg = vals_all[k_range]
    print(f"  path ALL max in 06:00-10:30: {seg.max():+.2f} pip at {fmt(360 + int(np.argmax(seg)))}")
    print(f"  path ALL min in 20:00-02:00: {vals_all[[int(np.where(OFFS==o)[0][0]) for o in range(-240,121)]].min():+.2f} pip")

    print("\n=== 2. CURRENT SPEC IN GRID ===")
    for name, e_off, x_off, thu_off in [("A 23:00->08:54", -60, 534, 295), ("B 00:00->09:54", 0, 594, 355)]:
        m_all, n_all = cell("ALL", e_off, x_off)
        m_g, _ = cell("GOTOBI", e_off, x_off)
        m_ng, _ = cell("NON-GOTOBI", e_off, x_off)
        m_h1, _ = cell("H1<=2023", e_off, x_off)
        m_h2, _ = cell("H2>=2024", e_off, x_off)
        print(f"{name}: ALL mean={m_all:+.2f}pip n={n_all} | gotobi={m_g:+.2f} non={m_ng:+.2f} | H1={m_h1:+.2f} H2={m_h2:+.2f}")
        # Thursday variants use THU cube with normal exit vs early exit
        mt_norm, nt = cell("THU", e_off, x_off)
        # early exit offsets 295/355 are not both in EXIT_OFFS(5-min grid has 295? 240+55=295 yes; 355 yes)
        mt_early, _ = cell("THU", e_off, thu_off)
        print(f"   THU only (n={nt}): normal-exit mean={mt_norm:+.2f}pip | early-exit({fmt(thu_off)}) mean={mt_early:+.2f}pip | price diff(early-normal)={mt_early-mt_norm:+.2f}pip")

    print("\n=== 3. TOP COMBOS (ALL days, by mean; min n>=800) ===")
    mean_all, n_all = means["ALL"], ns["ALL"]
    flat = []
    for i, e_off in enumerate(ENTRY_OFFS):
        for j, x_off in enumerate(EXIT_OFFS):
            if n_all[i, j] >= 800:
                flat.append((mean_all[i, j], e_off, x_off, n_all[i, j]))
    flat.sort(reverse=True)
    print("rank | IN -> OUT | ALL | H1 | H2 | gotobi | non-gotobi | plateau(ALL) | n")
    for r, (m, e_off, x_off, nn) in enumerate(flat[:15], 1):
        mh1, _ = cell("H1<=2023", e_off, x_off)
        mh2, _ = cell("H2>=2024", e_off, x_off)
        mg, _ = cell("GOTOBI", e_off, x_off)
        mng, _ = cell("NON-GOTOBI", e_off, x_off)
        pl = plateau("ALL", e_off, x_off)
        print(f"{r:2d} | {fmt(e_off)} -> {fmt(x_off)} | {m:+.2f} | {mh1:+.2f} | {mh2:+.2f} | {mg:+.2f} | {mng:+.2f} | {pl:+.2f} | {nn}")

    print("\n=== 4. SPLIT-HALF TRANSFER ===")
    for src, dst in [("H1<=2023", "H2>=2024"), ("H2>=2024", "H1<=2023")]:
        ms, nsrc = means[src], ns[src]
        best = None
        for i, e_off in enumerate(ENTRY_OFFS):
            for j, x_off in enumerate(EXIT_OFFS):
                if nsrc[i, j] >= 400 and (best is None or ms[i, j] > best[0]):
                    best = (ms[i, j], e_off, x_off)
        m_src, e_off, x_off = best
        m_dst, n_dst = cell(dst, e_off, x_off)
        m_all2, _ = cell("ALL", e_off, x_off)
        print(f"best in {src}: {fmt(e_off)}->{fmt(x_off)} mean={m_src:+.2f}pip -> in {dst}: {m_dst:+.2f}pip (ALL {m_all2:+.2f}, n_dst={n_dst})")

    print("\n=== 5. MARGINALS ===")
    for e_off in [-60, 0]:
        row = [(means['ALL'][ENTRY_OFFS.index(e_off), j], x_off) for j, x_off in enumerate(EXIT_OFFS)]
        row_ok = [(m, x) for m, x in row if not np.isnan(m)]
        bm, bx = max(row_ok)
        print(f"IN {fmt(e_off)}: best OUT (ALL) = {fmt(bx)} mean={bm:+.2f}pip")
        for x_off in [534, 594, 595, 600]:
            if x_off in EXIT_OFFS:
                mm, nn = cell("ALL", e_off, x_off)
                print(f"    OUT {fmt(x_off)}: {mm:+.2f}pip n={nn}")
    for x_off in [534, 594]:
        col = [(means['ALL'][i, EXIT_OFFS.index(x_off)], e_off) for i, e_off in enumerate(ENTRY_OFFS)]
        col_ok = [(m, e) for m, e in col if not np.isnan(m)]
        bm, be = max(col_ok)
        print(f"OUT {fmt(x_off)}: best IN (ALL) = {fmt(be)} mean={bm:+.2f}pip")
        for e_off in [-120, -60, 0, 60]:
            mm, nn = cell("ALL", e_off, x_off)
            print(f"    IN {fmt(e_off)}: {mm:+.2f}pip n={nn}")

    print("\n=== 6. THURSDAY GRID (entry 23:00 / 00:00, exit scan, THU only) ===")
    for e_off in [-60, 0]:
        row = [(means['THU'][ENTRY_OFFS.index(e_off), j], x_off, ns['THU'][ENTRY_OFFS.index(e_off), j]) for j, x_off in enumerate(EXIT_OFFS)]
        row_ok = [(m, x, nn) for m, x, nn in row if not np.isnan(m)]
        bm, bx, bn = max(row_ok)
        print(f"THU IN {fmt(e_off)}: best OUT = {fmt(bx)} mean={bm:+.2f}pip n={bn}")
        for x_off in [295, 355, 534, 594]:
            if x_off in EXIT_OFFS:
                mm, nn = cell("THU", e_off, x_off)
                print(f"    OUT {fmt(x_off)}: {mm:+.2f}pip n={nn}")

    print("\n=== 7. WEEKDAY PF FOR CURRENT A/B (long, price only) ===")
    # recompute A/B daily pnl directly (with Thursday early-exit rule), PF by dow
    def daily_pnl(e_off, x_off, thu_x_off):
        idx_e = int(np.where(OFFS == e_off)[0][0])
        out = []
        for i in range(nd):
            xo = thu_x_off if meta.dow.iloc[i] == 3 else x_off
            idx_x = int(np.where(OFFS == xo)[0][0])
            a, b = AO[i, idx_e], BO[i, idx_x]
            out.append(np.nan if (np.isnan(a) or np.isnan(b)) else (b - a) * PIP)
        return np.array(out)

    for name, e_off, x_off, thu_off in [("A", -60, 534, 295), ("B", 0, 594, 355)]:
        pnl = daily_pnl(e_off, x_off, thu_off)
        print(f"-- trade {name} --")
        for w in [1, 2, 3, 4]:
            s = pd.Series(pnl[meta.dow.values == w]).dropna()
            pf = s[s > 0].sum() / -s[s < 0].sum() if (s < 0).any() else float("nan")
            print(f"  dow{w}: n={len(s)} mean={s.mean():+.2f}pip PF={pf:.3f} win={100*(s>0).mean():.1f}%")
        s = pd.Series(pnl).dropna()
        pf = s[s > 0].sum() / -s[s < 0].sum()
        print(f"  ALL: n={len(s)} mean={s.mean():+.2f}pip PF={pf:.3f}")

    print("\n=== 8. RULE COMPARISON (paired, Tue-Fri exits, price only) ===")
    base_idx = np.where(masks["ALL"])[0]
    dow_arr = meta.dow.values

    def rule_pnl(entry_off, exit_normal, exit_thu=None):
        ie = int(np.where(OFFS == entry_off)[0][0])
        out = np.full(nd, np.nan)
        for i in base_idx:
            xo = exit_thu if (exit_thu is not None and dow_arr[i] == 3) else exit_normal
            ix = int(np.where(OFFS == xo)[0][0])
            a, b = AO[i, ie], BO[i, ix]
            if not (np.isnan(a) or np.isnan(b)):
                out[i] = (b - a) * PIP
        return out

    def rule_line(name, pnl):
        s = pd.Series(pnl).dropna()
        pf = s[s > 0].sum() / -s[s < 0].sum() if (s < 0).any() else float("nan")
        t_val = s.mean() / (s.std(ddof=1) / sqrt(len(s)))
        print(f"{name}: n={len(s)} mean={s.mean():+.2f}pip PF={pf:.3f} win={100*(s>0).mean():.1f}% t={t_val:.2f}")
        return pnl

    def paired(name, pnl_a, pnl_b):
        d = pd.Series(pnl_a - pnl_b).dropna()
        t_val = d.mean() / (d.std(ddof=1) / sqrt(len(d))) if len(d) > 1 and d.std(ddof=1) > 0 else float("nan")
        print(f"  paired diff {name}: mean={d.mean():+.2f}pip std={d.std(ddof=1):.1f} t={t_val:.2f} n={len(d)}")

    for e_off, cur_x, cur_thu in [(-60, 534, 295), (0, 594, 355)]:
        print(f"-- entry {fmt(e_off)} --")
        cur = rule_line(f"current hybrid (OUT {fmt(cur_x)}, Thu {fmt(cur_thu)})", rule_pnl(e_off, cur_x, cur_thu))
        fix = rule_line(f"always fixing (OUT {fmt(cur_x)} daily)", rule_pnl(e_off, cur_x))
        early = rule_line("always early (OUT 05:35 daily)", rule_pnl(e_off, 335))
        hyb955 = rule_line("hybrid 09:55 / Thu 05:35", rule_pnl(e_off, 595, 335))
        paired("current-hybrid minus always-fixing", cur, fix)
        paired("current-hybrid minus always-05:35", cur, early)
        paired("hybrid-09:55/Thu05:35 minus current-hybrid", hyb955, cur)

    print("\nDONE (price only; swap NOT included)")


if __name__ == "__main__":
    main()
