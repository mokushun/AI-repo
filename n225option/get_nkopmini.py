#!/usr/bin/env python3

import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path
import csv
import re

# 保存先
DATA_DIR = Path.home() / "n225_options"
RAW_DIR = DATA_DIR / "raw"

RAW_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Referer": "https://www.jpx.co.jp/markets/derivatives/quotes/index.html",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

BASE_URL = "https://svc.qri.jp/jpx/nkopmini/1"

def get_html(page=None):
    url = BASE_URL if page is None else f"{BASE_URL}{page}"
    print(f"GET {url}")
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text

    print("status:", response.status_code)
    print("encoding:", response.encoding)
    print("content-type:", response.headers.get("Content-Type"))

    response.raise_for_status()

    response.encoding = response.apparent_encoding

    return response.text


def save_raw_html(html, page):
    """取得したHTMLを保存"""

    now = datetime.now()
    filename = (
        f"{now:%Y%m%d_%H%M%S}_page{page}.html"
    )

    path = RAW_DIR / filename

    path.write_text(
        html,
        encoding="utf-8",
    )

    print(f"saved: {path}")

    return path


def get_tables(html):
    """HTML内のtableを取得"""

    soup = BeautifulSoup(html, "html.parser")

    tables = soup.find_all("table")

    print(f"tables: {len(tables)}")

    for i, table in enumerate(tables):
        rows = table.find_all("tr")

        print(
            f"  table[{i}] rows={len(rows)}"
        )

    return tables


def print_text_sample(html):
    """HTMLからテキストを少し表示"""

    soup = BeautifulSoup(html, "html.parser")

    text = soup.get_text(
        "\n",
        strip=True,
    )

    print("\n--- TEXT SAMPLE ---")

    for line in text.splitlines()[:100]:
        print(line)


def main():
    html = get_html()

    save_raw_html(html, 1)

    get_tables(html)

    print_text_sample(html)

if __name__ == "__main__":
    main()

