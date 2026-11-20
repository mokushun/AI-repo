#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect_spx.py の引け枠の窓を、16:45 ET から 17:15 ET に広げる（1回だけ実行）。

使い方（実行場所: ai-repo のクローンの直下）:
  python3 /srv/mcp-workspace/ai-repo/handoff/patch_collect_spx_close_window.py projects/spx-collector/collect_spx.py

安全のための決まり（2026-11-20 の回帰の再発防止）:
- 古い正規表現 `SPXW?` のままのファイルには適用しない（SPXO のような銘柄で収集が失敗するため）。
  先に、リポジトリの正しい版（`SPX[WO]?` のもの）に戻してから使うこと
- 置換前の文字列が、4か所ともちょうど1か所ずつ一致したときだけ書き換える。1か所でも違えば何も変更しない
- 書き換え前に .bak を作る。適用済み（CLOSE_END がある）なら何もしない
"""
import sys
from pathlib import Path

OLD_REGEX = 'OPTION_RE = re.compile(r"^(SPXW?)'

EDITS = [
    # (置換前, 置換後)
    ('ET = ZoneInfo("America/New_York")\n',
     'ET = ZoneInfo("America/New_York")\n'
     '# 引け枠の窓（ET、0時からの秒数）。1発勝負にならないよう、後ろを 17:15 ET まで取る\n'
     '# （2026-11-19: 夜に取得しても引け時点のチェーンが返ることを確認済み）。\n'
     'CLOSE_START = 16 * 3600 + 30 * 60\n'
     'CLOSE_END = 17 * 3600 + 15 * 60\n'),
    ('clock <= 16 * 3600 + 45 * 60', 'clock <= CLOSE_END'),
    ('9 * 3600 + 45 * 60 <= seconds < 16 * 3600 + 30 * 60', '9 * 3600 + 45 * 60 <= seconds < CLOSE_START'),
    ('16 * 3600 + 30 * 60 <= seconds <= 16 * 3600 + 45 * 60', 'CLOSE_START <= seconds <= CLOSE_END'),
]


def main():
    if len(sys.argv) != 2:
        print("使い方: python3 patch_collect_spx_close_window.py <collect_spx.py のパス>")
        return 2
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    if "CLOSE_END" in text:
        print("すでに適用済みです。何も変更しません:", path)
        return 0
    if OLD_REGEX in text:
        print("このファイルは古い正規表現 SPXW? のままです。適用しません。")
        print("先に、リポジトリの正しい版（SPX[WO]? のもの）に戻してください。")
        return 1
    if 'SPX[WO]?' not in text:
        print("正規表現が想定の形（SPX[WO]?）ではありません。念のため適用しません。内容を確認してください。")
        return 1
    for old, _new in EDITS:
        count = text.count(old)
        if count != 1:
            print(f"置換前の文字列が {count} か所に一致しました（1か所のはず）。何も変更せず終了します:")
            print("  " + old.strip().splitlines()[0])
            return 1
    for old, new in EDITS:
        text = text.replace(old, new)
    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    print("適用しました:", path)
    print("バックアップ:", backup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
