#!/usr/bin/env python3
"""Validation script for SPX collection and daily summary calculations."""

import math
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
COLLECT_SCRIPT = BASE_DIR / "collect_spx.py"
SAMPLE_JSON = BASE_DIR / "sample_spx.json"
def run_collect(data_dir, force=True, input_file=None):
    """Execute collect_spx.py with python interpreter."""
    cmd = [sys.executable, str(COLLECT_SCRIPT)]
    cmd.extend(["--data-dir", str(data_dir)])
    if force:
        cmd.append("--force")
    if input_file:
        cmd.extend(["--input", str(input_file)])
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res


def get_db_counts(conn):
    snap_count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    opt_count = conn.execute("SELECT COUNT(*) FROM options").fetchone()[0]
    return snap_count, opt_count


def run_checks(data_dir):
    db_path = data_dir / "spx_options.sqlite3"
    if not COLLECT_SCRIPT.exists():
        print(f"ERROR: {COLLECT_SCRIPT} not found.", file=sys.stderr)
        sys.exit(1)
    if not SAMPLE_JSON.exists():
        print(f"ERROR: {SAMPLE_JSON} not found.", file=sys.stderr)
        sys.exit(1)

    # 1. Ensure initial data exists in DB
    if not db_path.exists():
        init_res = run_collect(data_dir, force=True, input_file=SAMPLE_JSON)
        if init_res.returncode != 0:
            print(f"ERROR: Initial collection failed:\n{init_res.stderr}", file=sys.stderr)
            sys.exit(1)

    results = []

    def record(test_num, name, passed, details=""):
        status = "PASS" if passed else "FAIL"
        results.append((test_num, name, passed, details))
        msg = f"[{status}] {test_num}. {name}"
        if details:
            msg += f" ({details})"
        print(msg)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        # Check 8: Duplicate execution does not increase snapshot/option count
        snap_before, opt_before = get_db_counts(conn)
        dup_res = run_collect(data_dir, force=True, input_file=SAMPLE_JSON)
        snap_after, opt_after = get_db_counts(conn)
        check8_passed = (dup_res.returncode == 0 and
                         snap_after == snap_before and
                         opt_after == opt_before)
        check8_details = f"snapshots: {snap_before}->{snap_after}, options: {opt_before}->{opt_after}"

        # Fetch latest snapshot and summary
        snap = conn.execute("SELECT * FROM snapshots ORDER BY snapshot_id DESC LIMIT 1").fetchone()
        if not snap:
            print("ERROR: No snapshots found in DB.", file=sys.stderr)
            sys.exit(1)

        sid = snap["snapshot_id"]
        spot = float(snap["current_price"])
        asof_str = snap["data_asof_et"]
        asof_date = date.fromisoformat(asof_str[:10]) if asof_str else date.today()

        summaries = conn.execute(
            "SELECT * FROM daily_summary WHERE snapshot_id=? ORDER BY scope DESC, expiration ASC",
            (sid,)
        ).fetchall()
        exp_summaries = [s for s in summaries if s["scope"] == "expiration"]
        all_summaries = [s for s in summaries if s["scope"] == "all"]

        if not all_summaries or not exp_summaries:
            print("ERROR: Daily summary rows missing in DB.", file=sys.stderr)
            sys.exit(1)

        all_row = all_summaries[0]

        # Check 1: ATM IV (each expiration) in 0.05..0.6
        c1_details = []
        c1_passed = True
        for s in exp_summaries:
            iv = s["atm_iv"]
            ok = (iv is not None and 0.05 <= iv <= 0.6)
            c1_details.append(f"{s['expiration']}={iv}")
            if not ok:
                c1_passed = False
        record(1, "ATM IV（各満期）が0.05〜0.6", c1_passed, ", ".join(c1_details))

        # Check 2: put/call ratio in 0.3..3
        c2_details = []
        c2_passed = True
        for s in summaries:
            oi_r = s["put_call_oi_ratio"]
            vol_r = s["put_call_volume_ratio"]
            scope_label = f"{s['scope']}:{s['expiration']}" if s['scope'] == 'expiration' else 'all'
            ok_oi = (oi_r is not None and 0.3 <= oi_r <= 3.0)
            ok_vol = (vol_r is not None and 0.3 <= vol_r <= 3.0)
            c2_details.append(f"{scope_label}(OI={oi_r:.2f}, Vol={vol_r:.2f})")
            if not (ok_oi and ok_vol):
                c2_passed = False
        record(2, "put/call比が0.3〜3", c2_passed, ", ".join(c2_details))

        # Check 3: skew all is within [min_skew, max_skew] of expirations
        exp_skews = [s["iv_skew_25d"] for s in exp_summaries if s["iv_skew_25d"] is not None]
        all_skew = all_row["iv_skew_25d"]
        if exp_skews and all_skew is not None:
            min_skew, max_skew = min(exp_skews), max(exp_skews)
            c3_passed = (min_skew - 1e-9 <= all_skew <= max_skew + 1e-9)
            c3_details = f"min={min_skew:.4f}, all={all_skew:.4f}, max={max_skew:.4f}"
        else:
            c3_passed = False
            c3_details = "skew values missing"
        record(3, "満期別の skew の all が満期別の最小〜最大の範囲内", c3_passed, c3_details)

        # Only expirations with an ATM IV contribute to all-scope aggregates.
        valid_expirations = [s for s in exp_summaries if s["atm_iv"] is not None]

        # Check 4: all GEX == sum of eligible expiry GEX (rel tol 1e-6)
        all_gex = all_row["gex_approx"]
        sum_exp_gex = sum((s["gex_approx"] or 0.0) for s in valid_expirations)
        if not valid_expirations:
            c4_passed = all(all_row[k] is None for k in all_row.keys() if k != "snapshot_id" and k != "scope" and k != "expiration")
            c4_details = "有効満期なし: all 指標はすべて NULL"
        elif all_gex is not None:
            c4_passed = math.isclose(all_gex, sum_exp_gex, rel_tol=1e-6, abs_tol=1e-6)
            diff_rel = abs(all_gex - sum_exp_gex) / (abs(all_gex) if all_gex != 0 else 1.0)
            c4_details = f"all={all_gex:.1f}, sum={sum_exp_gex:.1f}, rel_diff={diff_rel:.2e}"
        else:
            c4_passed = False
            c4_details = "all GEX missing"
        record(4, "all の GEX ＝ 満期別GEXの合計（誤差は相対1e-6以内）", c4_passed, c4_details)

        # Check 5: all put/call ratios use ATM-IV-eligible expirations only.
        eligible_dates = [s["expiration"] for s in valid_expirations]
        marks = ",".join("?" for _ in eligible_dates)
        if eligible_dates:
            total_poi = conn.execute(f"SELECT SUM(open_interest) FROM options WHERE snapshot_id=? AND expiration IN ({marks}) AND option_type='put'", (sid, *eligible_dates)).fetchone()[0] or 0.0
            total_coi = conn.execute(f"SELECT SUM(open_interest) FROM options WHERE snapshot_id=? AND expiration IN ({marks}) AND option_type='call'", (sid, *eligible_dates)).fetchone()[0] or 0.0
            total_pvol = conn.execute(f"SELECT SUM(volume) FROM options WHERE snapshot_id=? AND expiration IN ({marks}) AND option_type='put'", (sid, *eligible_dates)).fetchone()[0] or 0.0
            total_cvol = conn.execute(f"SELECT SUM(volume) FROM options WHERE snapshot_id=? AND expiration IN ({marks}) AND option_type='call'", (sid, *eligible_dates)).fetchone()[0] or 0.0
        else:
            total_poi = total_coi = total_pvol = total_cvol = 0.0

        expected_oi_ratio = total_poi / total_coi if total_coi else None
        expected_vol_ratio = total_pvol / total_cvol if total_cvol else None

        c5_oi_ok = (expected_oi_ratio is not None and all_row["put_call_oi_ratio"] is not None and
                    math.isclose(all_row["put_call_oi_ratio"], expected_oi_ratio, rel_tol=1e-6))
        c5_vol_ok = (expected_vol_ratio is not None and all_row["put_call_volume_ratio"] is not None and
                     math.isclose(all_row["put_call_volume_ratio"], expected_vol_ratio, rel_tol=1e-6))
        c5_passed = (all_row["put_call_oi_ratio"] is None and all_row["put_call_volume_ratio"] is None) if not eligible_dates else c5_oi_ok and c5_vol_ok
        c5_details = ("有効満期なし: all 比率は NULL" if not eligible_dates else f"OI: stored={all_row['put_call_oi_ratio']:.4f}/calc={expected_oi_ratio:.4f}, "
                      f"Vol: stored={all_row['put_call_volume_ratio']:.4f}/calc={expected_vol_ratio:.4f}")
        record(5, "all の put/call比 ＝ ATM IVのある満期のプット合計÷コール合計", c5_passed, c5_details)

        # Check 6: straddle mid ≈ 0.8 * spot * IV * sqrt(days/365) within 10%
        c6_details = []
        c6_passed = True
        for s in exp_summaries:
            dte = max((date.fromisoformat(s["expiration"]) - asof_date).days, 0)
            actual = s["atm_straddle_mid"]
            iv = s["atm_iv"]
            if actual is not None and iv is not None and dte > 0:
                approx = 0.8 * spot * iv * math.sqrt(dte / 365.0)
                diff_pct = abs(actual - approx) / approx
                ok = diff_pct <= 0.10
                c6_details.append(f"{s['expiration']}(dte={dte}, actual={actual:.1f}, approx={approx:.1f}, diff={diff_pct*100:.2f}%)")
                if not ok:
                    c6_passed = False
            else:
                c6_passed = False
                c6_details.append(f"{s['expiration']}(missing data)")
        record(6, "ストラドル中値 ≈ 0.8×現値×IV×√(日数/365)（誤差10%以内）", c6_passed, ", ".join(c6_details))

        # Check 7: expected_move_to_expiry == expected_move_1d * sqrt(days)
        c7_details = []
        c7_passed = True
        for s in exp_summaries:
            dte = max((date.fromisoformat(s["expiration"]) - asof_date).days, 0)
            actual_move = s["expected_move_to_expiry"]
            d1_move = s["expected_move_1d"]
            if actual_move is not None and d1_move is not None:
                calc_move = d1_move * math.sqrt(dte)
                ok = math.isclose(actual_move, calc_move, rel_tol=1e-6, abs_tol=1e-6)
                c7_details.append(f"{s['expiration']}(actual={actual_move:.2f}, calc={calc_move:.2f})")
                if not ok:
                    c7_passed = False
            else:
                c7_passed = False
                c7_details.append(f"{s['expiration']}(missing data)")
        record(7, "満期別の expected_move_to_expiry ＝ expected_move_1d × √(満期までの日数)", c7_passed, ", ".join(c7_details))

        # Record Check 8
        record(8, "同じデータで2回実行しても、options と snapshots の件数が増えない", check8_passed, check8_details)

        # Check 9: nearest expiry min/max strike within spot +- 10%
        nearest_exp = exp_summaries[0]["expiration"]
        min_k, max_k = conn.execute(
            "SELECT MIN(strike), MAX(strike) FROM options WHERE snapshot_id=? AND expiration=?",
            (sid, nearest_exp)
        ).fetchone()
        spot_lower = spot * 0.9
        spot_upper = spot * 1.1
        c9_passed = (min_k >= spot_lower - 1e-6) and (max_k <= spot_upper + 1e-6)
        c9_details = f"nearest={nearest_exp}, spot={spot:.2f}, strikes=[{min_k:.1f}, {max_k:.1f}], range=[{spot_lower:.1f}, {spot_upper:.1f}]"
        record(9, "直近満期の最大行使価格・最小行使価格が現値±10%の範囲内", c9_passed, c9_details)

    total = len(results)
    passed_count = sum(1 for r in results if r[2])
    print(f"\nSummary: {passed_count}/{total} checks passed.")

    if passed_count != total:
        sys.exit(1)
    sys.exit(0)


def main():
    # Every validation run owns an isolated database and raw/log directory.
    with tempfile.TemporaryDirectory(prefix="spx-check-") as temp_dir:
        run_checks(Path(temp_dir))


if __name__ == "__main__":
    main()
