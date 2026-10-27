import re
from bs4 import BeautifulSoup
from pathlib import Path
import pandas as pd
from datetime import datetime

DATA_DIR = Path.home() / "n225_options"
RAW_DIR = DATA_DIR / "raw"

def parse_iv_qty(s):
    """'32.41%-' や '22 (180)- (-)' のような結合文字列を分割"""
    return s  # 後述の正規表現で個別に処理

def parse_strike_block(cells):
    (call_settle, call_oi, call_vol, call_iv_ask_bid, call_qty_ask_bid,
     call_iv, call_chg, call_price_raw,
     strike_raw,
     put_price_raw, put_chg, put_iv, put_qty_ask_bid, put_iv_ask_bid,
     put_vol, put_oi, put_settle) = cells

    # 非改行スペースを通常スペースに変換してから正規化
    strike_clean = strike_raw.replace("\xa0", " ")

    # "A T M" や "ATM" ラベルを除去し、"リスク指標"も除去
    strike_clean = re.sub(r"A\s*T\s*M", "", strike_clean)
    strike_clean = strike_clean.replace("リスク指標", "")
    strike_clean = strike_clean.replace(",", "").strip()

    is_atm = "ATM" in strike_raw.replace("\xa0", "") or bool(re.search(r"A\s*T\s*M", strike_raw))

    strike = float(strike_clean)

    # 現在値+時刻を分離 (例: "4708/31 22:18" -> 47, "08/31 22:18")
    def split_price_time(raw):
        m = re.match(r"([\d,\.]+|-)(\d{2}/\d{2} \d{2}:\d{2})?$", raw)
        if not m:
            return raw, None
        return m.group(1), m.group(2)

    call_price, call_time = split_price_time(call_price_raw)
    put_price, put_time = split_price_time(put_price_raw)

    return {
        "strike": float(strike),
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
    """4要素のグリークス値行をパース"""
    delta, gamma, theta, vega = cells
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}

def clean_text(s):
    return s.replace("\xa0", " ").replace("\u3000", " ").strip()

def parse_option_board(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find_all("table")[1]
    rows = table.find_all("tr")

    records = []
    i = 2
    while i < len(rows):
        main_cells = [clean_text(c.get_text(strip=True)) for c in rows[i].find_all(["td", "th"])]
        if len(main_cells) != 17:
            i += 1
            continue

        rec = parse_strike_block(main_cells)

        call_greek_cells = [c.get_text(strip=True) for c in rows[i+3].find_all(["td", "th"])]
        put_greek_cells = [c.get_text(strip=True) for c in rows[i+5].find_all(["td", "th"])]

        rec["call_greeks"] = parse_greeks(call_greek_cells)
        rec["put_greeks"] = parse_greeks(put_greek_cells)

        records.append(rec)
        i += 6  # 次の権利行使価格ブロックへ

    return records

def to_float_or_none(s):
    if s in ("-", "--", "", None):
        return None
    return float(s.replace(",", "").replace("%", ""))

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

with open("/home/pi/n225_options/raw/20260901_151437_page1.html", encoding="utf-8") as f:
    html = f.read()


records = parse_option_board(html)
records = [normalize_record(r) for r in records]

timestamp = datetime.strptime("20260901_144337", "%Y%m%d_%H%M%S")
df = pd.DataFrame(records)
df["scraped_at"] = timestamp
df.to_csv(f"n225mini_options_{timestamp:%Y%m%d_%H%M%S}.csv", index=False, encoding="utf-8-sig")


df = pd.DataFrame([flatten(r) for r in records])
df["scraped_at"] = timestamp  # 取得日時
df.to_csv(f"n225mini_{timestamp:%Y%m%d_%H%M%S}.csv", index=False, encoding="utf-8-sig")

for r in records:
    print(r)
