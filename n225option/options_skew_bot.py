#!/usr/bin/env python3
"""
options_skew_bot.py
日経225ミニオプションのIV Skewを用いた高期待値デイトレードシミュレータ＆シグナル生成エンジン

【ロジック概要】
1. オプションマスターデータから1時間毎のATM Put IVとATM Call IVを取得し、Skew = Put_IV - Call_IV を算出。
2. Skew > 2.0（プットパニック・底打ち警戒）の状態で、日経225 1分足のEMA9がEMA21を上抜けた（ゴールデンクロス）足の寄りでロング。
3. OCO形式で利確（TP: +100pts）、損切（SL: -40pts）、または最大保有時間（180分）で手仕舞い。
4. スプレッドコスト（デフォルト10.05pts）を厳密に考慮。
"""

import sys
import os
import sqlite3
import argparse
import pandas as pd
import numpy as np
from datetime import datetime

DEFAULT_DB_PATH = "/home/cli/m1.db"
DEFAULT_OPT_PATH = "/home/cli/n225_options/n225mini_options_master.csv"
DEFAULT_SPREAD = 10.05
DEFAULT_TP = 100.0
DEFAULT_SL = 40.0
DEFAULT_SKEW_THRESHOLD = 2.0
DEFAULT_MAX_BARS = 180


def load_options_skew(opt_path: str) -> pd.DataFrame:
    """オプションCSVから毎時のSkewを算出"""
    if not os.path.exists(opt_path):
        raise FileNotFoundError(f"オプションCSVが見つかりません: {opt_path}")

    df_opt = pd.read_csv(opt_path)
    records = []
    
    for ts, g in df_opt.groupby("scraped_at"):
        t_dt = pd.to_datetime(ts)
        atm = g[g["is_atm"] == True]
        if len(atm) > 0:
            call_iv = atm["call_iv"].values[0]
            put_iv = atm["put_iv"].values[0]
            strike = atm["strike"].values[0]
            skew = put_iv - call_iv if (not np.isnan(put_iv) and not np.isnan(call_iv)) else np.nan
        else:
            call_iv, put_iv, strike, skew = np.nan, np.nan, np.nan, np.nan

        records.append({
            "jst": t_dt,
            "atm_strike": strike,
            "call_iv": call_iv,
            "put_iv": put_iv,
            "skew": skew,
        })

    opt_df = pd.DataFrame(records).sort_values("jst").reset_index(drop=True)
    return opt_df


def load_m1_data(db_path: str, t_start: int, t_end: int) -> pd.DataFrame:
    """SQLite m1.dbから指定期間のJP225_USD 1分足をロード"""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"SQLite DBが見つかりません: {db_path}")

    con = sqlite3.connect(db_path)
    query = f"""
        SELECT t, datetime(t, 'unixepoch', '+9 hours') as jst_str, bo, bh, bl, bc
        FROM m1
        WHERE inst = 'JP225_USD' AND t >= {t_start} AND t <= {t_end}
        ORDER BY t
    """
    df = pd.read_sql(query, con)
    con.close()

    df["jst"] = pd.to_datetime(df["jst_str"])
    return df


def calculate_indicators(m1_df: pd.DataFrame) -> pd.DataFrame:
    """1分足のEMAとゴールデンクロスを計算"""
    df = m1_df.copy()
    df["ema9"] = df["bc"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["bc"].ewm(span=21, adjust=False).mean()
    df["ema_cross_up"] = (df["ema9"] > df["ema21"]) & (df["ema9"].shift(1) <= df["ema21"].shift(1))
    return df


def run_backtest(
    merged_df: pd.DataFrame,
    skew_thresh: float = DEFAULT_SKEW_THRESHOLD,
    tp: float = DEFAULT_TP,
    sl: float = DEFAULT_SL,
    max_bars: int = DEFAULT_MAX_BARS,
    spread: float = DEFAULT_SPREAD
) -> pd.DataFrame:
    """バックテスト実行エンジン"""
    df = merged_df.copy()
    # シグナル条件: Skew > しきい値 かつ EMAゴールデンクロス
    df["sig_long"] = (df["skew"] > skew_thresh) & df["ema_cross_up"]

    trades = []
    pos = 0
    ep = 0.0
    ei = 0

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        if pos == 0:
            if prev["sig_long"]:
                pos = 1
                ep = row["bo"] + spread  # askで買いエントリー
                ei = i
        else:
            bars = i - ei
            hit_tp = row["bh"] >= ep + tp
            hit_sl = row["bl"] <= ep - sl
            time_up = bars >= max_bars

            if hit_tp or hit_sl or time_up:
                if hit_tp:
                    xp = ep + tp
                    reason = "TP (利確)"
                elif hit_sl:
                    xp = ep - sl
                    reason = "SL (損切)"
                else:
                    xp = row["bc"]
                    reason = "TIME (時間切)"

                pnl = xp - ep
                trades.append({
                    "entry_time": df.iloc[ei]["jst"],
                    "entry_price": ep,
                    "exit_time": row["jst"],
                    "exit_price": xp,
                    "pnl": pnl,
                    "bars": bars,
                    "reason": reason,
                    "skew_at_entry": df.iloc[ei]["skew"],
                })
                pos = 0

    return pd.DataFrame(trades)


def display_results(tdf: pd.DataFrame, tp: float, sl: float, skew_thresh: float, spread: float):
    """結果の出力とパフォーマンス集計"""
    print("\n" + "=" * 65)
    print("  日経225 オプションSkew連動デイトレード バックテスト結果")
    print("=" * 65)
    print(f" 設定パラメータ: Skew閾値 > {skew_thresh:.1f} | TP = +{tp:.0f} pts | SL = -{sl:.0f} pts | Spread = {spread:.2f} pts")
    print("-" * 65)

    if tdf.empty:
        print("  該当期間中にシグナルは発生しませんでした。")
        print("=" * 65 + "\n")
        return

    wins = tdf[tdf["pnl"] > 0]
    losses = tdf[tdf["pnl"] < 0]
    total_pnl = tdf["pnl"].sum()
    win_rate = len(wins) / len(tdf) * 100
    gross_profit = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else np.inf

    print(f" 取引回数        : {len(tdf):>4d} 回")
    print(f" 勝率            : {win_rate:>6.1f} %  (勝ち {len(wins)} / 負け {len(losses)})")
    print(f" 累計損益        : {total_pnl:>+8.1f} pts")
    print(f" 平均損益/回     : {tdf['pnl'].mean():>+8.1f} pts")
    print(f" プロフィットファクタ: {pf:>6.2f}")
    print(f" 最大ドローダウン: {abs((tdf['pnl'].cumsum() - tdf['pnl'].cumsum().cummax()).min()):>6.1f} pts")
    print("-" * 65)
    print(" 決済理由の内訳:")
    for reason, count in tdf["reason"].value_counts().items():
        print(f"   - {reason:<12s}: {count:>2d} 回 ({count/len(tdf)*100:.1f}%)")
    print("-" * 65)
    print(" 全トレード履歴:")
    out_df = tdf.copy()
    out_df["pnl_str"] = out_df["pnl"].apply(lambda x: f"{x:+6.1f}")
    out_df["entry_time_str"] = out_df["entry_time"].dt.strftime("%m/%d %H:%M")
    out_df["exit_time_str"] = out_df["exit_time"].dt.strftime("%m/%d %H:%M")
    cols = ["entry_time_str", "exit_time_str", "entry_price", "exit_price", "pnl_str", "bars", "reason"]
    print(out_df[cols].to_string(index=False, header=["Entry(JST)", "Exit(JST)", "Entry", "Exit", "PnL", "Bars", "Reason"]))
    print("=" * 65 + "\n")


def check_current_signal(opt_path: str, db_path: str, skew_thresh: float = DEFAULT_SKEW_THRESHOLD):
    """最新のオプションと1分足からリアルタイムのシグナル状態を判定"""
    opt_df = load_options_skew(opt_path)
    latest_opt = opt_df.dropna(subset=["skew"]).iloc[-1]
    
    con = sqlite3.connect(db_path)
    m1 = pd.read_sql("SELECT datetime(t, 'unixepoch', '+9 hours') as jst, bc FROM m1 WHERE inst='JP225_USD' ORDER BY t DESC LIMIT 50", con)
    con.close()
    m1["jst"] = pd.to_datetime(m1["jst"])
    m1 = m1.sort_values("jst").reset_index(drop=True)
    m1 = calculate_indicators(m1)
    latest_m1 = m1.iloc[-1]
    
    print("\n" + "=" * 55)
    print("  現在シグナル判定（リアルタイム監視モード）")
    print("=" * 55)
    print(f" [最新オプション] 時刻: {latest_opt['jst']} | Skew: {latest_opt['skew']:.2f}")
    print(f"   - Put IV: {latest_opt['put_iv']:.2f} | Call IV: {latest_opt['call_iv']:.2f} (ATM: {latest_opt['atm_strike']:.0f})")
    skew_status = "ALERT (プット過熱・ロング待機)" if latest_opt["skew"] > skew_thresh else "NORMAL (待機)"
    print(f"   - Skew判定: {skew_status}")
    print(f" [最新1分足]     時刻: {latest_m1['jst']} | 終値: {latest_m1['bc']:.1f}")
    print(f"   - EMA9: {latest_m1['ema9']:.1f} | EMA21: {latest_m1['ema21']:.1f}")
    cross_status = "GOLDEN CROSS 発生!" if latest_m1["ema_cross_up"] else "クロスなし"
    print(f"   - トレンド判定: {cross_status}")
    print("-" * 55)
    if latest_opt["skew"] > skew_thresh and latest_m1["ema_cross_up"]:
        print("  ★★★ 【ENTRY SIGNAL: BUY】ロングエントリー推奨！ ★★★")
    elif latest_opt["skew"] > skew_thresh:
        print("  ※ Skew過熱中。1分足のEMAゴールデンクロスを待機してください。")
    else:
        print("  ※ 現在はエントリー条件を満たしていません。")
    print("=" * 55 + "\n")


def main():
    parser = argparse.ArgumentParser(description="日経225 オプションSkew連動デイトレードシステム")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="m1.dbのパス")
    parser.add_argument("--opt", default=DEFAULT_OPT_PATH, help="n225mini_options_master.csvのパス")
    parser.add_argument("--tp", type=float, default=DEFAULT_TP, help="利確幅 (pts)")
    parser.add_argument("--sl", type=float, default=DEFAULT_SL, help="損切幅 (pts)")
    parser.add_argument("--skew", type=float, default=DEFAULT_SKEW_THRESHOLD, help="Skewしきい値")
    parser.add_argument("--spread", type=float, default=DEFAULT_SPREAD, help="スプレッドコスト (pts)")
    parser.add_argument("--check-now", action="store_true", help="現在のシグナル状況を表示")
    args = parser.parse_args()

    if args.check_now:
        check_current_signal(args.opt, args.db, args.skew)
        return

    print("データロード＆指標算出中...")
    opt_df = load_options_skew(args.opt)
    t_start = int(opt_df["jst"].min().timestamp())
    t_end = int(opt_df["jst"].max().timestamp())

    m1_df = load_m1_data(args.db, t_start, t_end)
    m1_df = calculate_indicators(m1_df)

    # 1分足にオプションデータをasof merge (直近の確定値を前方結合)
    merged_df = pd.merge_asof(
        m1_df.sort_values("jst"),
        opt_df.sort_values("jst"),
        on="jst",
        direction="backward"
    )

    tdf = run_backtest(
        merged_df,
        skew_thresh=args.skew,
        tp=args.tp,
        sl=args.sl,
        spread=args.spread
    )

    display_results(tdf, args.tp, args.sl, args.skew, args.spread)


if __name__ == "__main__":
    main()
