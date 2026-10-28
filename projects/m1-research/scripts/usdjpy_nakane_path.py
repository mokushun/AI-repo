"""
USD/JPY 仲値トレード：IN/OUT 最適化のステップ1「平均的な値動きの経路」

前段 scripts/usdjpy_nakane_current.py の続き。時刻はすべて JST。
定義（前段と同一）:
- データ: m1.db、inst='USD_JPY'、読み取り専用
- 約定価格は指定時刻の足の「始値」: IN/OUT ともその分ちょうどのバーの bo/ao
- ロング（買い→売り）: その時刻の bid 始値 - IN の ask 始値。単位 pip（1 pip = 0.01円）
- 方向は未確認だが前段でロング側だけがプラスだったため、ここではロングのみを描く
- 対象は IN の足がある決済日セッション。各時刻の平均は、その時刻の足がある
  セッションだけで取る（全時刻揃いを要求すると欠損で n が激減するため）
- ゴトー日: 決済日の日が 5 の倍数（5,10,15,20,25,30）
- 価格のみ。スワップは m1.db に無いので含まない。木曜の早期決済コストは
  「通常時刻まで持った場合との価格差」として別枠で測る
"""

import os
import sqlite3
from math import sqrt

import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PIP = 100.0


def mean_t(s):
    s = pd.Series(s).dropna()
    if len(s) == 0:
        return float("nan"), float("nan")
    t_val = s.mean() / (s.std(ddof=1) / sqrt(len(s))) if len(s) > 1 and s.std(ddof=1) > 0 else float("nan")
    return s.mean(), t_val


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT t,bo,ao FROM m1 WHERE inst='USD_JPY' ORDER BY t", con)
    con.close()
    price = {int(t): (bo, ao) for t, bo, ao in zip(df.t, df.bo, df.ao)}

    def epoch_jst(date, hh, mm):
        ts = pd.Timestamp(date) + pd.Timedelta(hours=hh, minutes=mm)
        return int(ts.tz_localize("Asia/Tokyo").timestamp())

    jst_s = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo")
    all_dates = sorted(set(jst_s.dt.date))

    # アンカー: (名前, INの決済日オフセット, IN時刻, 特殊時刻ラベル -> (時,分))
    anchors = {
        "A 23:00 IN": dict(off=-1, hh=23, mm=0,
                          specials={"04:55": (4, 55), "08:54": (8, 54), "09:54": (9, 54)}),
        "B 00:00 IN": dict(off=0, hh=0, mm=0,
                          specials={"05:55": (5, 55), "08:54": (8, 54), "09:54": (9, 54)}),
    }

    for name, sp in anchors.items():
        # セッション収集：IN の足さえあれば採用し、各時刻はその足がある
        # セッションだけで平均する（全時刻揃いを要求すると、1本の欠損で
        # セッションごと落ち、n=26 まで減ることが判明したため）
        grid_labels = []
        base = -60 if name.startswith("A") else 0
        grid_abs = list(range(base, 10 * 60 + 1, 15))
        specials_abs = {}
        for lab, (hh, mm) in sp["specials"].items():
            specials_abs[lab] = hh * 60 + mm
        all_abs = sorted(set(grid_abs) | set(specials_abs.values()))
        label_of = {}
        for a in all_abs:
            if a in specials_abs.values():
                lab = [k for k, v in specials_abs.items() if v == a][0]
            else:
                hh, mm = divmod(a % 1440, 60)
                lab = f"{hh:02d}:{mm:02d}"
            label_of[a] = lab

        sessions = []  # (date, dow, year, gotobi, {label: ret}) — 欠損時刻はキー無し
        for d in all_dates:
            # 前段と同一の母集団にする：決済日は火〜金のみ（土曜決済＝金曜夜→
            # 土曜朝のセッションを混ぜると、06:00 以降の n が落ちて時刻間の
            # 比較が崩れる。月曜決済は IN の足が存在しないので自然に落ちる）
            dow = pd.Timestamp(d).dayofweek
            if dow not in (1, 2, 3, 4):
                continue
            in_date = pd.Timestamp(d) + pd.Timedelta(days=sp["off"])
            te = epoch_jst(in_date.date(), sp["hh"], sp["mm"])
            if te not in price:
                continue
            _, ao_in = price[te]
            rets = {}
            for a in all_abs:
                day = pd.Timestamp(d) + pd.Timedelta(days=-1 if a < 0 else 0)
                hh, mm = divmod(a % 1440, 60)
                tx = epoch_jst(day.date(), hh, mm)
                if tx not in price:
                    continue
                bo_out, _ = price[tx]
                rets[label_of[a]] = (bo_out - ao_in) * PIP
            sessions.append(dict(date=d, dow=pd.Timestamp(d).dayofweek,
                                 year=pd.Timestamp(d).year,
                                 gotobi=(pd.Timestamp(d).day % 5 == 0),
                                 thu=(pd.Timestamp(d).dayofweek == 3),
                                 rets=rets))
        print(f"\n=== PATH {name} (LONG, price only) ===")
        print(f"sessions with entry bar: {len(sessions)} "
              f"({min(s['date'] for s in sessions)} .. {max(s['date'] for s in sessions)})")
        print("n per time is for 'all'; other groups use the sessions where that bar exists")

        groups = {
            "all": lambda s: True,
            "gotobi": lambda s: s["gotobi"],
            "non-gotobi": lambda s: not s["gotobi"],
            "2020-10..2023": lambda s: s["year"] <= 2023,
            "2024..2026": lambda s: s["year"] >= 2024,
        }
        print(f"{'time':>6} {'n':>5} {'all':>7} {'gotobi':>7} {'non-got':>7} {'<=2023':>7} {'2024+':>7}")
        means = {}
        n_all_map = {}
        for a in all_abs:
            lab = label_of[a]
            row = {}
            n_all = 0
            for gname, mask in groups.items():
                vals = [s["rets"][lab] for s in sessions if mask(s) and lab in s["rets"]]
                row[gname] = np.mean(vals) if vals else float("nan")
                if gname == "all":
                    n_all = len(vals)
            means[lab] = row
            n_all_map[lab] = n_all
            print(f"{lab:>6} {n_all:>5} {row['all']:+7.2f} {row['gotobi']:+7.2f} {row['non-gotobi']:+7.2f} "
                  f"{row['2020-10..2023']:+7.2f} {row['2024..2026']:+7.2f}")
        # ピークは n>=1200 の時刻に限定する。06:00（n=465）や 07:00（n=878）は
        # 足の欠損が多く、残ったセッションだけの平均になるため比較できない
        eligible = [lab for lab in means if n_all_map[lab] >= 1200]
        print(f"  note: excluded from peak (n<1200): "
              f"{[lab for lab in means if n_all_map[lab] < 1200]}")
        for gname in ["all", "gotobi", "2020-10..2023", "2024..2026"]:
            grid_only = {lab: means[lab][gname] for lab in eligible}
            peak = max(grid_only, key=lambda k: (grid_only[k] == grid_only[k], grid_only[k]))
            plateau = [lab for lab, v in grid_only.items()
                       if v == v and v >= grid_only[peak] - 1.0]
            print(f"  peak [{gname}]: {peak} {grid_only[peak]:+.2f}pip; within 1pip: {plateau}")

        # 木曜の早期決済コスト（ペア差：両方の足がある木曜決済日のみ）
        thu = [s for s in sessions if s["thu"]]
        if name.startswith("A"):
            early, normal = "04:55", "08:54"
        else:
            early, normal = "05:55", "09:54"
        paired = [(s["rets"][early], s["rets"][normal]) for s in thu
                  if early in s["rets"] and normal in s["rets"]]
        e = pd.Series([p[0] for p in paired])
        n_ = pd.Series([p[1] for p in paired])
        d_ = n_ - e
        mt, tt = mean_t(d_)
        print(f"-- Thursday early-exit price cost ({name}) --")
        print(f"  Thu paired n={len(paired)} early({early}) mean={e.mean():+.2f}pip "
              f"normal({normal}) mean={n_.mean():+.2f}pip "
              f"given-up (normal-early) mean={mt:+.2f}pip t={tt:.2f}")

    print("\nDONE (price only; swap NOT included)")


if __name__ == "__main__":
    main()
