#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect_inputs.py の sec_options に、「最新のスナップショットが引け枠ではない」警告を足す（1回だけ実行）。

背景（2026-11-19）: 引け枠（16:30〜 ET）の収集が欠けると、朝枠（09:39 ET）のスナップショットが
最新になり、その現値・GEX・マックスペインが引け値のように読まれた。入力にデータ時点は出ていたが、
「引け枠ではない」という警告が無かった。

使い方（実行場所: /srv/mcp-workspace、どのユーザーでも可）:
  python3 ai-repo/handoff/patch_collect_inputs_asof_warning.py ai-repo/projects/spx-judgment/collect_inputs.py

- 置換前の文字列がちょうど1か所に一致しないときは、何も書き換えずに止まる
- 書き換える前に、同じ場所に .bak を作る
- すでに適用済みなら、何もせず終わる（何度実行しても安全）
- 判定: データ時点（data_asof_et）の時刻が 16:00 ET より前なら警告を1行足す
"""
import sys
from pathlib import Path

MARKER = "最新のスナップショットは引け枠ではなく"

OLD = '''    lines = [f"SPX現値 {_fmt(s['current_price'], ',.1f')}｜データ時点 {asof}（ET）｜取得 {s['fetched_at_utc']}{note}"]
'''

NEW = OLD + '''    # 引け枠（16:30〜 ET の取得）のスナップショットが無く、朝枠などが最新になっているときは警告する
    # （2026-11-19: 引け枠が欠けても、朝枠の値が「現値」として引け値のように読まれた）
    try:
        asof_dt = datetime.fromisoformat(asof)
        if asof_dt.hour * 60 + asof_dt.minute < 16 * 60:
            lines.append("※最新のスナップショットは引け枠ではなく、データ時点が引け（16:00 ET）より前です。"
                         "この現値・マックスペイン・GEXは引け値ではありません。"
                         "「価格の動き」の終値とは時点が違います")
    except (TypeError, ValueError):
        pass
'''


def main():
    if len(sys.argv) != 2:
        print("使い方: python3 patch_collect_inputs_asof_warning.py <collect_inputs.py のパス>")
        return 2
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("すでに適用済みです。何も変更しません:", path)
        return 0
    count = text.count(OLD)
    if count != 1:
        print(f"置換前の文字列が {count} か所に一致しました（1か所のはず）。何も変更せず終了します。")
        print("collect_inputs.py の sec_options が、想定と違う形になっている可能性があります。")
        return 1
    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(text, encoding="utf-8")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
    print("適用しました:", path)
    print("バックアップ:", backup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
