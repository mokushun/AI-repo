#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evaluate.py
judgments/*.json（run_judgment.py の保存結果）と、実際の値動き（d1.json の日足）を突き合わせる。標準ライブラリのみ。
APIキー不要・AI不使用なので、何度実行しても枠は減らない。

  python3 evaluate.py                    # 公開のd1.jsonを取得して、全判断を採点
  python3 evaluate.py --d1 /tmp/d1.json  # 保存済みのd1.jsonを使う
  python3 evaluate.py --symbol US100     # 別の銘柄で採点する（既定 US500）
  python3 evaluate.py --version v3       # プロンプトの版で絞る

採点のしかた
  ・結果 ＝ 対象日（ET）の日足の終値が、前の米国取引日の終値を上回ったか（上回る=1、それ以外=0）
  ・ブライアスコア ＝ (p_up − 結果)^2。小さいほど良い。常に0.5と答えると0.25
  ・日足は OANDA のCFD（NY17時区切り）なので、現物指数の16時ET終値とは少しずれる
  ・同じ（対象日・モデル・プロンプト版）の判断が複数あるときは、最初に保存されたものだけを採点する
  ・judgments/experiments/ や manual/ の中は対象外（直下の *.json だけを読む）
  ・入力に不具合があった日などの判断は、judgments/exclude.txt にファイル名を1行ずつ書くと採点から除く（#で始まる行はコメント）
"""

import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
from run_judgment import is_trading_day  # noqa: E402  休場日の判定を共通にする

D1_URL = "https://fx.camelliab1.com/public/d1.json"
ET_TZ = ZoneInfo("America/New_York")


def load_d1(src):
    if src.startswith("http"):
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode("utf-8"))
    return json.loads(Path(src).read_text(encoding="utf-8"))


def outcomes(d1, symbol):
    """{取引日(ET): (終値, 前の取引日の終値, 変化率%)}。確定した足だけを使う"""
    bars = [b for b in d1["symbols"].get(symbol, []) if not b.get("cur")]
    days = []
    for b in bars:
        # OANDAの日足は、取引日の前日17時ETに始まる。始値の時刻+24時間のET日付が、その足の取引日
        label = datetime.fromtimestamp(b["t"] + 86400, timezone.utc).astimezone(ET_TZ).date()
        if is_trading_day(label):
            days.append((label, b["c"]))
    out = {}
    for (_, c0), (d1_, c1) in zip(days, days[1:]):
        out[d1_.isoformat()] = (c1, c0, (c1 / c0 - 1) * 100)
    return out


def load_excluded(dirpath):
    f = Path(dirpath) / "exclude.txt"
    if not f.exists():
        return set()
    return {ln.split("#")[0].strip() for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")}


def load_judgments(dirpath, version):
    rows = {}
    excluded = load_excluded(dirpath)
    for f in sorted(Path(dirpath).glob("*.json")):
        if f.name in excluded:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            m, j = d["meta"], d["judgment"]
            row = {"file": f.name, "created": m.get("created_utc", ""), "model": m.get("model") or "不明",
                   "ver": m.get("prompt_version", "?"), "slot": m.get("slot", ""),
                   "date": j["target_date"], "bias": j["bias"], "p_up": j.get("p_up"),
                   "conf": j.get("confidence"), "stand": j.get("stand_aside"), "risk": j.get("event_risk")}
        except (ValueError, KeyError, OSError, TypeError):
            continue
        if version and row["ver"] != version:
            continue
        key = (row["date"], row["model"], row["ver"], row["slot"])
        if key not in rows or row["created"] < rows[key]["created"]:
            rows[key] = row                      # 最初に保存されたものを採る
    return sorted(rows.values(), key=lambda r: (r["date"], r["model"], r["ver"]))


def main():
    ap = argparse.ArgumentParser(description="判断と実際の値動きを突き合わせる")
    ap.add_argument("--d1", default=D1_URL, help="d1.jsonのURLまたはファイル")
    ap.add_argument("--symbol", default="US500")
    ap.add_argument("--judgments", default=str(BASE_DIR / "judgments"))
    ap.add_argument("--version", default=None, help="プロンプトの版で絞る（例: v3）")
    args = ap.parse_args()

    try:
        d1 = load_d1(args.d1)
    except (OSError, ValueError) as e:
        sys.exit(f"d1.jsonを読めない: {type(e).__name__}")
    res = outcomes(d1, args.symbol)
    if not res:
        sys.exit(f"{args.symbol} の日足が見つからない")
    order = sorted(res)
    prev_up = {d: res[order[i - 1]][2] > 0 for i, d in enumerate(order) if i > 0}  # 前の取引日が上昇だったか
    rows = load_judgments(args.judgments, args.version)
    if not rows:
        sys.exit("対象の判断が無い（judgments/*.json）")

    print(f"採点の基準: {args.symbol}（OANDA CFD）の日足。最新の確定日 {max(res)}")
    print(f"{'対象日':10} {'モデル':26} {'版':3} {'bias':8} {'p_up':>5} {'見送':3} | 結果")
    scored, pending = [], 0
    for r in rows:
        o = res.get(r["date"])
        pu = "—" if r["p_up"] is None else f"{r['p_up']:.2f}"
        stand = "○" if r["stand"] else "　"
        if o is None:
            pending += 1
            print(f"{r['date']:10} {r['model'][:26]:26} {r['ver']:3} {r['bias']:8} {pu:>5} {stand:3} | 未確定")
            continue
        up = 1 if o[2] > 0 else 0
        brier = None if r["p_up"] is None else (r["p_up"] - up) ** 2
        r.update(up=up, brier=brier, chg=o[2])
        scored.append(r)
        b = "—" if brier is None else f"{brier:.3f}"
        print(f"{r['date']:10} {r['model'][:26]:26} {r['ver']:3} {r['bias']:8} {pu:>5} {stand:3} | "
              f"{'上昇' if up else '下落'} {o[2]:+.2f}%  ブライアス {b}")

    print()
    print(f"採点済み {len(scored)}件／結果待ち {pending}件")
    groups = defaultdict(list)
    for r in scored:
        groups[(r["model"], r["ver"])].append(r)
    if groups:
        base = sum(r["up"] for r in {(x["date"]): x for x in scored}.values()) / len({x["date"] for x in scored})
        print(f"基準: 常に0.5と答えるとブライアス0.250／上昇日の割合 {base:.0%}（この期間）")
        print(f"{'モデル':26} {'版':3} {'件数':>4} {'平均p_up':>8} {'平均ブライアス':>12} {'方向一致':>10} {'見送り':>5}")
        for (model, ver), rs in sorted(groups.items()):
            ps = [r for r in rs if r["p_up"] is not None]
            if ps:
                avg_p = sum(r["p_up"] for r in ps) / len(ps)
                avg_b = sum(r["brier"] for r in ps) / len(ps)
                dec = [r for r in ps if r["p_up"] != 0.5]
                hit = f"{sum((r['p_up'] > 0.5) == bool(r['up']) for r in dec)}/{len(dec)}" if dec else "—"
                print(f"{model[:26]:26} {ver:3} {len(rs):>4} {avg_p:>8.2f} {avg_b:>12.3f} {hit:>10} "
                      f"{sum(1 for r in rs if r['stand']):>5}")
            else:
                print(f"{model[:26]:26} {ver:3} {len(rs):>4} {'p_upなし':>8}")
    if groups:
        print()
        print("AIなしの基準との比較（差 = モデル − 基準。マイナスならモデルの方が良い）")
        print("  基準A: 常に0.5　／　基準B: その期間の上昇日の割合を常に答える（結果を見てから決める基準なので、実際より有利）")
        print("  基準C: 前の取引日が上昇なら0.6、下落なら0.4（前日と同じ方向に賭ける）")
        print(f"{'モデル':26} {'版':3} {'件数':>4} {'モデル':>7} {'A:0.5':>7} {'B:割合':>7} {'C:前日':>7} | {'対A':>7} {'対B':>7} {'対C':>7}")
        for (model, ver), rs in sorted(groups.items()):
            ps = [r for r in rs if r["p_up"] is not None]
            if not ps:
                continue
            m = sum(r["brier"] for r in ps) / len(ps)
            pbar = sum(r["up"] for r in ps) / len(ps)
            a = 0.25
            b = pbar * (1 - pbar)
            cs = [r for r in ps if r["date"] in prev_up]
            if cs:
                c = sum(((0.6 if prev_up[r["date"]] else 0.4) - r["up"]) ** 2 for r in cs) / len(cs)
                m_c = sum(r["brier"] for r in cs) / len(cs)   # Cは前日が分かる日だけで比べる
                cs_txt, dc = f"{c:>7.3f}", f"{m_c - c:>+7.3f}"
            else:
                cs_txt, dc = f"{'—':>7}", f"{'—':>7}"
            print(f"{model[:26]:26} {ver:3} {len(ps):>4} {m:>7.3f} {a:>7.3f} {b:>7.3f} {cs_txt} | "
                  f"{m - a:>+7.3f} {m - b:>+7.3f} {dc}")
        alld = {r["date"]: r["up"] for r in scored}
        if len(alld) >= 2:
            ds = sorted(alld)
            hit = [(alld[d] == int(prev_up[d])) for d in ds if d in prev_up]
            if hit:
                print(f"  参考: 前日と同じ方向だった日 {sum(hit)}/{len(hit)}（{sum(hit) / len(hit):.0%}）／上昇日 {sum(alld.values())}/{len(alld)}")
    print()
    print("※件数が少ないうちは、運の影響が大きく、優劣は言えません（目安として、少なくとも数十件は必要）。")
    print("※日足はOANDAのCFD（17時ET終値）で、現物指数の16時ET終値とは少し違います。")


if __name__ == "__main__":
    main()
