#!/usr/bin/env python3
"""
日経225ミニオプション（金曜限月）の気配・グリークスを取得してCSVに蓄積する。

cron実行を想定し、1回の実行で
  1. svc.qri.jp から最新HTMLを取得
  2. rawとして保存
  3. パースして正規化
  4. マスターCSVに追記
まで行う。エラーはログファイルに記録し、cronが失敗に気づけるようにする。

crontab例（平日、日中立会 9:00-15:30の間、1時間おき）:
    0 9-15 * * 1-5 /home/pi/venv/bin/python3 /home/pi/github/dev-lab/n225option/collect_nkopmini.py

必要ならナイト・セッション分もタブを追加できる。
"""

import re
import logging
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import requests
import pandas as pd
from bs4 import BeautifulSoup

# ------------------------------------------------------------------
# 設定
# ------------------------------------------------------------------

DATA_DIR = Path.home() / "n225_options"
RAW_DIR = DATA_DIR / "raw"
LOG_DIR = DATA_DIR / "logs"
MASTER_CSV = DATA_DIR / "n225mini_options_master.csv"

RAW_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 注意: svc.qri.jp/jpx/nkopmini/{n} のnは「絶対的な曜日」ではなく
# 「限月が近い順の通し番号」。水・金が交互に並んでいるだけなので、
# 限月がロールするたびにどの番号が金曜限月かは変わる。
# そのため固定URLではなく、取引最終日の曜日を見て動的に探す。
BASE_URL_TEMPLATE = "https://svc.qri.jp/jpx/nkopmini/{page}"
MAX_PAGES_TO_SCAN = 8  # 何番目まで探索するか（週次+月次限月が並ぶ想定）

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Referer": "https://www.jpx.co.jp/markets/derivatives/quotes/index.html",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

# ------------------------------------------------------------------
# ログ設定（cron実行時は標準出力が見えないため、ファイルに残す）
# ------------------------------------------------------------------

logging.basicConfig(
    filename=LOG_DIR / "collect_nkopmini.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 取得
# ------------------------------------------------------------------

def fetch_page(page):
    """page=0 は無番号URL、page>=1 は /1, /2, ... に対応"""
    url = BASE_URL_TEMPLATE.format(page="") if page == 0 else BASE_URL_TEMPLATE.format(page=page)
    logger.info(f"GET {url}")
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return url, response.text


def is_friday_expiry(last_trading_day_str):
    """取引最終日（前営業日）の曜日から金曜限月かどうか判定"""
    if not last_trading_day_str:
        return False
    try:
        dt = datetime.strptime(last_trading_day_str, "%Y/%m/%d")
    except ValueError:
        return False
    # 金曜限月の取引最終日は原則「木曜」(祝日を挟む場合はズレる可能性あり)
    return dt.weekday() == 3  # 0=月 ... 3=木 ... 6=日


def fetch_html():
    """金曜限月に対応するページを、番号を1つずつ探索して見つける"""
    for page in range(MAX_PAGES_TO_SCAN):
        url, html = fetch_page(page)
        _, last_trading_day, _ = extract_contract_info(html)
        logger.info(f"page={page} url={url} last_trading_day={last_trading_day}")

        if is_friday_expiry(last_trading_day):
            logger.info(f"金曜限月ページを特定: page={page}, last_trading_day={last_trading_day}")
            return html

    raise RuntimeError(
        f"金曜限月ページが見つかりませんでした（{MAX_PAGES_TO_SCAN}ページまで探索）。"
        "祝日の影響で最終取引日が木曜以外になっている可能性があります。"
    )


def save_raw_html(html, timestamp):
    filename = f"{timestamp:%Y%m%d_%H%M%S}_page1.html"
    path = RAW_DIR / filename
    path.write_text(html, encoding="utf-8")
    logger.info(f"saved raw html: {path}")
    return path


# ------------------------------------------------------------------
# パース
# ------------------------------------------------------------------

def clean_text(s):
    return s.replace("\xa0", " ").replace("\u3000", " ").strip()


def parse_strike_block(cells):
    (call_settle, call_oi, call_vol, call_iv_ask_bid, call_qty_ask_bid,
     call_iv, call_chg, call_price_raw,
     strike_raw,
     put_price_raw, put_chg, put_iv, put_qty_ask_bid, put_iv_ask_bid,
     put_vol, put_oi, put_settle) = cells

    strike_clean = strike_raw.replace("\xa0", " ")
    is_atm = bool(re.search(r"A\s*T\s*M", strike_clean))
    strike_clean = re.sub(r"A\s*T\s*M", "", strike_clean)
    strike_clean = strike_clean.replace("リスク指標", "")
    strike_clean = strike_clean.replace(",", "").strip()
    strike = float(strike_clean)

    def split_price_time(raw):
        m = re.match(r"([\d,\.]+|-)(\d{2}/\d{2} \d{2}:\d{2})?$", raw)
        if not m:
            return raw, None
        return m.group(1), m.group(2)

    call_price, call_time = split_price_time(call_price_raw)
    put_price, put_time = split_price_time(put_price_raw)

    return {
        "strike": strike,
        "is_atm": is_atm,
        "call_settle_prev": call_settle,
        "call_oi": call_oi,
        "call_volume": call_vol,
        "call_iv": call_iv,
        "call_chg": call_chg,
        "call_price": call_price,
        "call_price_time": call_time,
        "put_price": put_price,
        "put_price_time": put_time,
        "put_chg": put_chg,
        "put_iv": put_iv,
        "put_volume": put_vol,
        "put_oi": put_oi,
        "put_settle_prev": put_settle,
    }


def parse_greeks(cells):
    delta, gamma, theta, vega = cells
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def parse_option_board(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find_all("table")[1]
    rows = table.find_all("tr")

    records = []
    i = 2  # ヘッダー2行をスキップ
    while i < len(rows):
        main_cells = [clean_text(c.get_text(strip=True)) for c in rows[i].find_all(["td", "th"])]
        if len(main_cells) != 17:
            i += 1
            continue

        rec = parse_strike_block(main_cells)

        call_greek_cells = [c.get_text(strip=True) for c in rows[i + 3].find_all(["td", "th"])]
        put_greek_cells = [c.get_text(strip=True) for c in rows[i + 5].find_all(["td", "th"])]

        rec["call_greeks"] = parse_greeks(call_greek_cells)
        rec["put_greeks"] = parse_greeks(put_greek_cells)

        records.append(rec)
        i += 6

    return records


def extract_contract_info(html):
    """取引日・取引最終日をページ内テキストから抽出（見つからなければNone）

    注意: 「取引日」は今日の日付であって限月そのものではない。
    限月（SQ日相当）は取引最終日の翌営業日になるはずなので、
    休日を挟まない前提で+1日した推定値も別途返す。
    """
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    trade_date = None
    m = re.search(r"取引日\s*(\d{4}/\d{2}/\d{2})", text)
    if m:
        trade_date = m.group(1)

    last_trading_day = None
    m = re.search(r"取引最終日\s*(\d{4}/\d{2}/\d{2})", text)
    if m:
        last_trading_day = m.group(1)

    estimated_expiry = None
    if last_trading_day:
        try:
            dt = datetime.strptime(last_trading_day, "%Y/%m/%d")
            estimated_expiry = (dt + timedelta(days=1)).strftime("%Y/%m/%d")
        except ValueError:
            pass

    return trade_date, last_trading_day, estimated_expiry


def to_float_or_none(s):
    if s in ("-", "--", "", None):
        return None
    try:
        return float(str(s).replace(",", "").replace("%", ""))
    except ValueError:
        return None


def normalize_record(rec):
    numeric_fields = [
        "call_settle_prev", "call_oi", "call_volume", "call_price",
        "put_price", "put_settle_prev", "put_volume", "put_oi",
    ]
    for f in numeric_fields:
        rec[f] = to_float_or_none(rec[f])

    rec["call_iv"] = to_float_or_none(rec["call_iv"])
    rec["put_iv"] = to_float_or_none(rec["put_iv"])

    for side in ("call_greeks", "put_greeks"):
        for k, v in rec[side].items():
            rec[side][k] = to_float_or_none(v)

    return rec


def flatten(rec):
    flat = {k: v for k, v in rec.items() if k not in ("call_greeks", "put_greeks")}
    for k, v in rec["call_greeks"].items():
        flat[f"call_{k}"] = v
    for k, v in rec["put_greeks"].items():
        flat[f"put_{k}"] = v
    return flat


# ------------------------------------------------------------------
# 保存（追記）
# ------------------------------------------------------------------

def append_to_master_csv(records, scraped_at, trade_date, last_trading_day, estimated_expiry):
    df = pd.DataFrame([flatten(r) for r in records])
    df["scraped_at"] = scraped_at
    df["trade_date"] = trade_date
    df["last_trading_day"] = last_trading_day
    df["estimated_expiry"] = estimated_expiry  # 限月推定日（休日を挟むとズレる可能性あり）

    write_header = not MASTER_CSV.exists()
    df.to_csv(
        MASTER_CSV,
        mode="a",
        header=write_header,
        index=False,
        encoding="utf-8-sig",
    )
    logger.info(f"appended {len(df)} rows to {MASTER_CSV}")


# ------------------------------------------------------------------
# メイン
# ------------------------------------------------------------------

def main():
    now = datetime.now()

    try:
        html = fetch_html()
    except Exception:
        logger.error("HTML取得に失敗しました:\n" + traceback.format_exc())
        return

    save_raw_html(html, now)

    try:
        records = parse_option_board(html)
        if not records:
            logger.warning("パース結果が0件でした。ページ構造が変わっていないか確認してください。")
            return

        records = [normalize_record(r) for r in records]
        trade_date, last_trading_day, estimated_expiry = extract_contract_info(html)

        append_to_master_csv(records, now, trade_date, last_trading_day, estimated_expiry)
        logger.info(f"OK: {len(records)}件取得・保存完了")

    except Exception:
        logger.error("パース・保存処理に失敗しました:\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
