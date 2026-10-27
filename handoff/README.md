# handoff（AI同士の引き継ぎメモ）

AIが作業を終えたとき、次に作業するAIやClaudeへ向けて、ここにメモを残す。

## ファイル名

`YYYY-MM-DD_<AI名>_<内容>.md`

例: `2026-10-27_muse_first_run.md`、`2026-10-28_codex_collect_fix.md`

## 書くこと（テンプレート）

```
# 件名

- 日時（JST）:
- 担当AI:
- やったこと:
- 結果・エラー:
- 変更したファイル:
- 次にやってほしいこと:
- 注意点:
```

## ルール

- 秘密情報は書かない
- 1ファイル1テーマ
- Claudeが内容を `docs/status.md` に統合したら、古いメモは `handoff/archive/` に移す
