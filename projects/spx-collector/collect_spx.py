#!/usr/bin/env python3
"""Fetch Cboe's delayed SPX option chain into SQLite; suitable for cron."""
import argparse
import gzip
import json
import logging
import math
import re
import sqlite3
import sys
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "spx_data"
RAW_DIR = DATA_DIR / "raw"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "spx_options.sqlite3"
URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Referer": "https://www.cboe.com/",
    "Accept": "application/json",
}
MAX_RETRIES = 2
RETRY_SECONDS = 10
TIMEOUT_SECONDS = 30
MAX_EXPIRATIONS = 5
ET = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


def configure_data_dir(data_dir):
    """Set storage paths before running collection operations."""
    global DATA_DIR, RAW_DIR, LOG_DIR, DB_PATH
    DATA_DIR = Path(data_dir).expanduser().resolve()
    RAW_DIR = DATA_DIR / "raw"
    LOG_DIR = DATA_DIR / "logs"
    DB_PATH = DATA_DIR / "spx_options.sqlite3"
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_DIR / "collect_spx.log", level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", force=True)

# SPX or SPXW + YYMMDD + C/P + 8 digit strike.
OPTION_RE = re.compile(r"^(SPXW?)(\d{6})([CP])(\d{8})$")


def fetch_json():
    request = Request(URL, headers=HEADERS, method="GET")
    for attempt in range(MAX_RETRIES + 1):
        try:
            logger.info("GET %s attempt=%d", URL, attempt + 1)
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            logger.exception("取得失敗 attempt=%d/%d", attempt + 1, MAX_RETRIES + 1)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_SECONDS)


def parse_option(item):
    match = OPTION_RE.fullmatch(str(item.get("option", "")))
    if not match:
        raise ValueError(f"unknown option identifier: {item.get('option')!r}")
    root, expiry, side, strike = match.groups()
    row = {
        "option_symbol": item["option"], "root": root,
        "expiration": datetime.strptime(expiry, "%y%m%d").date().isoformat(),
        "option_type": "call" if side == "C" else "put",
        "strike": int(strike) / 1000.0,
    }
    fields = ("bid", "bid_size", "ask", "ask_size", "iv", "open_interest", "volume",
              "delta", "gamma", "vega", "theta", "rho", "theo", "change", "open",
              "high", "low", "tick", "last_trade_price", "last_trade_time",
              "percent_change", "prev_day_close")
    row.update({key: item.get(key) for key in fields})
    return row


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db(conn):
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS snapshots (
      snapshot_id INTEGER PRIMARY KEY,
      source_timestamp TEXT NOT NULL UNIQUE,
      symbol TEXT,
      current_price REAL NOT NULL,
      received_at TEXT NOT NULL,
      raw_path TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS options (
      snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
      option_symbol TEXT NOT NULL, root TEXT NOT NULL,
      expiration TEXT NOT NULL, option_type TEXT NOT NULL, strike REAL NOT NULL,
      bid REAL, bid_size REAL, ask REAL, ask_size REAL, iv REAL,
      open_interest REAL, volume REAL, delta REAL, gamma REAL, vega REAL,
      theta REAL, rho REAL, theo REAL, change REAL, open REAL, high REAL, low REAL,
      tick TEXT, last_trade_price REAL, last_trade_time TEXT,
      percent_change REAL, prev_day_close REAL,
      PRIMARY KEY(snapshot_id, option_symbol)
    );
    CREATE INDEX IF NOT EXISTS idx_options_exp_strike ON options(expiration, strike);
    CREATE TABLE IF NOT EXISTS collection_slots (
      slot_date TEXT NOT NULL,
      slot_name TEXT NOT NULL CHECK(slot_name IN ('morning', 'close')),
      status TEXT NOT NULL CHECK(status IN ('running', 'done')),
      started_at_utc TEXT NOT NULL,
      completed_at_utc TEXT,
      PRIMARY KEY(slot_date, slot_name)
    );
    CREATE TABLE IF NOT EXISTS app_meta (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
    """)
    snap_cols = _columns(conn, "snapshots")
    # data_timestamp_raw deliberately preserves the payload string verbatim;
    # its timezone is unconfirmed and must not be inferred from its contents.
    for name in ("data_timestamp_raw", "fetched_at_utc", "data_asof_et"):
        if name not in snap_cols:
            conn.execute(f"ALTER TABLE snapshots ADD COLUMN {name} TEXT")
    summary_cols = _columns(conn, "daily_summary") if "daily_summary" in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    } else set()
    if not summary_cols:
        conn.execute("""CREATE TABLE daily_summary (
          snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
          scope TEXT NOT NULL CHECK(scope IN ('all', 'expiration')),
          expiration TEXT NOT NULL DEFAULT '',
          put_call_oi_ratio REAL, put_call_volume_ratio REAL, atm_iv REAL,
          atm_straddle_mid REAL, expected_move_to_expiry REAL,
          expected_move_1d REAL, iv_skew_25d REAL, max_pain REAL, gex_approx REAL,
          PRIMARY KEY(snapshot_id, scope, expiration)
        )""")
    else:
        if "atm_mid_price" in summary_cols and "atm_straddle_mid" not in summary_cols:
            conn.execute("ALTER TABLE daily_summary RENAME COLUMN atm_mid_price TO atm_straddle_mid")
        if "expected_move_points" in summary_cols and "expected_move_to_expiry" not in summary_cols:
            conn.execute("ALTER TABLE daily_summary RENAME COLUMN expected_move_points TO expected_move_to_expiry")
        summary_cols = _columns(conn, "daily_summary")
        if "expected_move_1d" not in summary_cols:
            conn.execute("ALTER TABLE daily_summary ADD COLUMN expected_move_1d REAL")

    # Backfill acquisition metadata for old records, retaining their original rows.
    conn.execute("""UPDATE snapshots SET data_timestamp_raw=source_timestamp
                    WHERE data_timestamp_raw IS NULL""")
    for sid, received in conn.execute("""SELECT snapshot_id,received_at FROM snapshots
                                        WHERE fetched_at_utc IS NULL OR fetched_at_utc=received_at""").fetchall():
        try:
            fetched_utc = datetime.fromisoformat(received).astimezone(timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            fetched_utc = received
        conn.execute("UPDATE snapshots SET fetched_at_utc=? WHERE snapshot_id=?", (fetched_utc, sid))
    ids = [r[0] for r in conn.execute("SELECT snapshot_id FROM snapshots")]
    for sid in ids:
        row = conn.execute("SELECT MAX(last_trade_time) FROM options WHERE snapshot_id=?", (sid,)).fetchone()
        if row and row[0]:
            conn.execute("UPDATE snapshots SET data_asof_et=? WHERE snapshot_id=? AND data_asof_et IS NULL", (row[0], sid))
    # Recalculate existing summaries once when this definition version is first
    # installed. Avoid rescanning every historical option row on every cron run.
    summary_version = conn.execute("SELECT value FROM app_meta WHERE key='daily_summary_version'").fetchone()
    if not summary_version or summary_version[0] != "3":
        option_columns = [c[1] for c in conn.execute("PRAGMA table_info(options)")]
        for sid, spot, asof in conn.execute("SELECT snapshot_id,current_price,data_asof_et FROM snapshots").fetchall():
            opts = [dict(zip(option_columns, row))
                    for row in conn.execute("SELECT * FROM options WHERE snapshot_id=?", (sid,))]
            if opts:
                conn.execute("DELETE FROM daily_summary WHERE snapshot_id=?", (sid,))
                conn.executemany("""INSERT INTO daily_summary
                  (snapshot_id,scope,expiration,put_call_oi_ratio,put_call_volume_ratio,atm_iv,
                   atm_straddle_mid,expected_move_to_expiry,expected_move_1d,iv_skew_25d,max_pain,gex_approx)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  [(sid, *summary) for summary in calc_summaries(opts, spot, asof or date.today().isoformat())])
        conn.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES('daily_summary_version','3')")


def positive_quote(o):
    return (o.get("bid") is not None and o.get("ask") is not None
            and float(o["bid"]) > 0 and float(o["ask"]) > 0
            and o.get("iv") is not None and float(o["iv"]) > 0)


def quote_mid(o):
    return (float(o["bid"]) + float(o["ask"])) / 2.0


def calc_summary(rows, spot, as_of):
    """Summarize one expiry. ATM straddle mid is call+put midpoint at the ATM strike."""
    calls = [o for o in rows if o["option_type"] == "call"]
    puts = [o for o in rows if o["option_type"] == "put"]
    total = lambda group, key: sum(float(o.get(key) or 0) for o in group)
    coi, poi = total(calls, "open_interest"), total(puts, "open_interest")
    cv, pv = total(calls, "volume"), total(puts, "volume")
    strikes = sorted({o["strike"] for o in rows})
    atm_strike = min(strikes, key=lambda k: abs(k - spot)) if strikes else None
    atm = [o for o in rows if o["strike"] == atm_strike and positive_quote(o)]
    atm_iv = sum(float(o["iv"]) for o in atm) / len(atm) if atm else None
    # The straddle is the sum of the ATM call midpoint and ATM put midpoint,
    # requiring both legs at the same strike and expiration.
    atm_calls = [o for o in atm if o["option_type"] == "call"]
    atm_puts = [o for o in atm if o["option_type"] == "put"]
    atm_straddle = quote_mid(atm_calls[0]) + quote_mid(atm_puts[0]) if atm_calls and atm_puts else None
    valid_calls = [o for o in calls if positive_quote(o) and o.get("delta") is not None]
    valid_puts = [o for o in puts if positive_quote(o) and o.get("delta") is not None]
    call25 = min(valid_calls, key=lambda o: abs(float(o["delta"]) - .25), default=None)
    put25 = min(valid_puts, key=lambda o: abs(float(o["delta"]) + .25), default=None)
    # Select both 25-delta legs within this expiration; skew is put IV minus call IV.
    skew = float(put25["iv"]) - float(call25["iv"]) if call25 and put25 else None
    payouts = []
    for k in strikes:
        payout = sum(float(o.get("open_interest") or 0) * max(k-o["strike"], 0) for o in calls)
        payout += sum(float(o.get("open_interest") or 0) * max(o["strike"]-k, 0) for o in puts)
        payouts.append((payout, k))
    max_pain = min(payouts)[1] if payouts else None
    gex = sum((1 if o["option_type"] == "call" else -1)
              * float(o.get("gamma") or 0) * float(o.get("open_interest") or 0)
              * 100 * spot * spot * .01 for o in rows)
    try:
        dte = max((date.fromisoformat(rows[0]["expiration"]) - date.fromisoformat(str(as_of)[:10])).days, 0)
    except (ValueError, TypeError, IndexError):
        dte = 0
    expiry_move = spot * float(atm_iv) * math.sqrt(dte / 365) if atm_iv is not None else None
    # One calendar day of annualized volatility, using the same 365-day basis.
    daily_move = spot * float(atm_iv) / math.sqrt(365) if atm_iv is not None else None
    return (poi / coi if coi else None, pv / cv if cv else None, atm_iv,
            atm_straddle, expiry_move, daily_move, skew, max_pain, gex)


def calc_summaries(options, spot, as_of):
    """Calculate expiration rows and the all-chain row with column-specific definitions."""
    by_expiry = []
    for expiry in sorted({o["expiration"] for o in options}):
        rows = [o for o in options if o["expiration"] == expiry]
        by_expiry.append(("expiration", expiry, *calc_summary(rows, spot, as_of)))
    # For `all`, only expirations with a calculable ATM IV are eligible. Use
    # their contracts for ratios, max pain, and GEX; expiration rows remain intact.
    # ATM straddle and move-to-expiry have no single all-chain meaning, so are NULL.
    # ATM IV, 1-day expected move, and 25-delta skew are simple means of the
    # available expiration values; each skew itself uses same-expiration legs.
    def mean_at(index):
        # `index` is the position in calc_summary's return tuple; rows prepend
        # the scope and expiration columns.
        row_index = index + 2
        values = [r[row_index] for r in by_expiry if r[row_index] is not None]
        return sum(values) / len(values) if values else None
    valid_expirations = {r[1] for r in by_expiry if r[4] is not None}
    valid_options = [o for o in options if o["expiration"] in valid_expirations]
    if valid_options:
        chain = calc_summary(valid_options, spot, as_of)
        expiry_gex_sum = sum(row[10] or 0 for row in by_expiry if row[1] in valid_expirations)
        aggregate = ("all", "", chain[0], chain[1], mean_at(2), None, None,
                     mean_at(5), mean_at(6), chain[7], expiry_gex_sum)
    else:
        aggregate = ("all", "", None, None, None, None, None, None, None, None, None)
    return [aggregate, *by_expiry]


def _same_as_previous(conn, spot, options):
    previous = conn.execute("SELECT snapshot_id,current_price FROM snapshots ORDER BY snapshot_id DESC LIMIT 1").fetchone()
    if not previous or float(previous[1]) != spot:
        return False
    old = [tuple(row) for row in conn.execute("""SELECT option_symbol,open_interest,volume
          FROM options WHERE snapshot_id=? ORDER BY option_symbol""", (previous[0],))]
    new = sorted((o["option_symbol"], o.get("open_interest"), o.get("volume")) for o in options)
    return old == new


def save(data):
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise ValueError("JSON structure mismatch: expected top-level data object")
    payload = data["data"]
    if not isinstance(payload.get("options"), list):
        raise ValueError("JSON structure mismatch: data.options is not a list")
    timestamp = data.get("timestamp")
    if timestamp is None:
        raise ValueError("JSON missing top-level timestamp")
    timestamp_raw = str(timestamp)
    spot_value = payload.get("current_price")
    if spot_value is None:
        raise ValueError("data.current_price is missing; ATM cannot be determined")
    spot = float(spot_value)
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError(f"invalid current_price: {spot_value!r}")
    parsed = [parse_option(item) for item in payload["options"]]
    asof_times = [o.get("last_trade_time") for o in parsed if o.get("last_trade_time")]
    data_asof_et = max(asof_times) if asof_times else None
    try:
        as_of_date = date.fromisoformat(data_asof_et[:10]) if data_asof_et else datetime.now(ET).date()
    except ValueError as exc:
        raise ValueError(f"unrecognized options last_trade_time: {data_asof_et!r}") from exc
    expirations = sorted({o["expiration"] for o in parsed
                          if date.fromisoformat(o["expiration"]) >= as_of_date})[:MAX_EXPIRATIONS]
    options = [o for o in parsed if o["expiration"] in expirations
               and spot * .9 <= o["strike"] <= spot * 1.1]
    if not options:
        raise ValueError("no option rows remain after expiry/strike filtering")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    received_at = datetime.now().astimezone().isoformat(timespec="seconds")
    stamp = re.sub(r"[^0-9A-Za-z_-]", "_", timestamp_raw)
    raw_path = RAW_DIR / f"{stamp}.json.gz"
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        if _same_as_previous(conn, spot, options):
            logger.info("unchanged spot/OI/volume; duplicate snapshot skipped")
            return 0, 0, True
        if conn.execute("SELECT 1 FROM snapshots WHERE source_timestamp=?", (timestamp_raw,)).fetchone():
            logger.info("timestamp already exists; duplicate snapshot skipped: %s", timestamp_raw)
            return 0, 0, True
        with gzip.open(raw_path, "wt", encoding="utf-8") as stream:
            json.dump(data, stream, separators=(",", ":"), ensure_ascii=False)
        cur = conn.execute("""INSERT INTO snapshots
          (source_timestamp,symbol,current_price,received_at,raw_path,data_timestamp_raw,fetched_at_utc,data_asof_et)
          VALUES(?,?,?,?,?,?,?,?)""",
          (timestamp_raw, payload.get("symbol", data.get("symbol")), spot, received_at,
           str(raw_path), timestamp_raw, fetched_at, data_asof_et))
        snapshot_id = cur.lastrowid
        columns = "option_symbol root expiration option_type strike bid bid_size ask ask_size iv open_interest volume delta gamma vega theta rho theo change open high low tick last_trade_price last_trade_time percent_change prev_day_close".split()
        placeholders = ",".join("?" for _ in range(len(columns) + 1))
        conn.executemany(f"INSERT INTO options(snapshot_id,{','.join(columns)}) VALUES({placeholders})",
                         [(snapshot_id, *(o.get(c) for c in columns)) for o in options])
        summaries = calc_summaries(options, spot, data_asof_et)
        conn.executemany("""INSERT INTO daily_summary
          (snapshot_id,scope,expiration,put_call_oi_ratio,put_call_volume_ratio,atm_iv,
           atm_straddle_mid,expected_move_to_expiry,expected_move_1d,iv_skew_25d,max_pain,gex_approx)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", [(snapshot_id, *summary) for summary in summaries])
        logger.info("saved snapshot=%s options=%d summary_rows=%d", snapshot_id, len(options), len(summaries))
    return len(options), len(summaries), False


def in_market_hours(now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    clock = now.hour * 3600 + now.minute * 60 + now.second
    return now.weekday() < 5 and 9 * 3600 + 30 * 60 <= clock <= 16 * 3600 + 45 * 60


def current_slot(now=None):
    """Return (ET date, slot) only once one of today's two capture windows opens."""
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    if now.weekday() >= 5:
        return None
    seconds = now.hour * 3600 + now.minute * 60 + now.second
    if 9 * 3600 + 45 * 60 <= seconds < 16 * 3600 + 30 * 60:
        return now.date().isoformat(), "morning"
    if 16 * 3600 + 30 * 60 <= seconds <= 16 * 3600 + 45 * 60:
        return now.date().isoformat(), "close"
    return None


def claim_slot(slot):
    """Claim a daily slot in SQLite; stale claims can be retried after 30 minutes."""
    slot_date, slot_name = slot
    now = datetime.now(timezone.utc)
    started = now.isoformat(timespec="seconds")
    stale_before = (now.timestamp() - 30 * 60)
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        inserted = conn.execute("""INSERT OR IGNORE INTO collection_slots
          (slot_date,slot_name,status,started_at_utc) VALUES(?,?,?,?)""",
          (slot_date, slot_name, "running", started)).rowcount
        if not inserted:
            existing = conn.execute("SELECT status,started_at_utc FROM collection_slots WHERE slot_date=? AND slot_name=?",
                                    slot).fetchone()
            try:
                is_stale = datetime.fromisoformat(existing[1]).timestamp() < stale_before
            except (TypeError, ValueError):
                is_stale = True
            if existing[0] == "done" or not is_stale:
                return None
            updated = conn.execute("""UPDATE collection_slots SET status='running',started_at_utc=?,completed_at_utc=NULL
              WHERE slot_date=? AND slot_name=? AND status='running' AND started_at_utc=?""",
              (started, slot_date, slot_name, existing[1])).rowcount
            if not updated:
                return None
    return started


def finish_slot(slot, started, success):
    with sqlite3.connect(DB_PATH) as conn:
        if success:
            conn.execute("""UPDATE collection_slots SET status='done',completed_at_utc=?
              WHERE slot_date=? AND slot_name=? AND started_at_utc=?""",
              (datetime.now(timezone.utc).isoformat(timespec="seconds"), *slot, started))
        else:
            conn.execute("DELETE FROM collection_slots WHERE slot_date=? AND slot_name=? AND started_at_utc=?",
                         (*slot, started))


def cleanup_raw_files(now=None):
    """Delete gzip response files once their filesystem age reaches 30 days."""
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - 30 * 24 * 60 * 60
    for path in RAW_DIR.glob("*.json.gz"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                logger.info("removed expired raw response: %s", path.name)
        except FileNotFoundError:
            continue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="ignore weekday/ET market-hours gate")
    parser.add_argument("--input", type=Path, help="read a local JSON file instead of fetching Cboe")
    parser.add_argument("--data-dir", type=Path, default=BASE_DIR / "spx_data",
                        help="directory for database, raw data, and logs (default: spx_data)")
    args = parser.parse_args()
    configure_data_dir(args.data_dir)
    cleanup_raw_files()
    slot = None
    started = None
    if not args.force:
        now = datetime.now(timezone.utc)
        if not in_market_hours(now):
            print("時間外のため終了しました (ET)")
            return 0
        slot = current_slot(now)
        if slot is None:
            print("取得枠の開始前のため終了しました (ET)")
            return 0
        started = claim_slot(slot)
        if started is None:
            print(f"本日の取得枠は処理済みです: {slot[0]} {slot[1]}")
            return 0
    try:
        if args.input:
            with args.input.open(encoding="utf-8") as stream:
                data = json.load(stream)
        else:
            data = fetch_json()
        count, summaries, duplicate = save(data)
        if duplicate:
            print(f"変更なしのためスキップ: timestamp={data.get('timestamp')}")
        else:
            print(f"保存完了: options={count}, summaries={summaries}, db={DB_PATH}")
        if slot is not None:
            finish_slot(slot, started, True)
        return 0
    except Exception:
        if slot is not None:
            finish_slot(slot, started, False)
        logger.error("取得・パース・保存に失敗しました:\n%s", traceback.format_exc())
        print("失敗しました。ログを確認してください:", LOG_DIR / "collect_spx.log", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
