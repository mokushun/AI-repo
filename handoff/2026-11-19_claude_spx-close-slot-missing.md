# 11/18 の引け枠が未収集だった件の調査と対策 ＋ 11/20 の回帰（SPXO 銘柄で収集失敗）

- 日時（JST）: 2026-11-19〜11-20
- 担当AI: Claude（VPS開発プロジェクト、MCP経由。コマンドは実行できない）

## 緊急（2026-11-20 01:24 JST の失敗報告）

- 症状: 収集が `ValueError: unknown option identifier`（銘柄 `SPXO261216C01600000`）で終了し、スナップショットが保存されない。前回成功は11/18の snapshot=30
- 原因: 回帰。11/10 の修正（9182a98）で `SPX[WO]?` になっていた正規表現が、11/19 のコミット e20df5c（引け窓を ET 17:15 まで拡大）で `SPXW?` に戻った
- **本当の原因は Claude のミス。** 作業場所の collect_spx.py のコピーは 9182a98 より古い版だった。リポジトリの最新かを確認できないまま、そのコピーを全体書き直しで書き換え、クローンへコピーされて commit された。全体の書き直しをしたのが誤り（他にも古いコピーに戻った箇所がある可能性がある）
- 期限: 今夜 06:30 JST（11/19 ET の引け枠）までに直して、VPS と Muse に反映する。直せば 11/20 ET の朝枠（23:45 JST から）→ 引け枠（06:30 JST）は取れる。11/19 ET の朝枠は欠けた可能性がある

## 直し方（ユーザーの作業）

1. クローンで、e20df5c の変更が窓の変更だけだったか確認する: `git diff e20df5c^ e20df5c -- projects/spx-collector/collect_spx.py`（窓の3行と定数以外に、消えた行があれば、それも回帰）
2. `git checkout e20df5c^ -- projects/spx-collector/collect_spx.py` で、回帰の前の版に戻す
3. パッチ `ai-repo/handoff/patch_collect_spx_close_window.py` で窓の変更だけをやり直す（古い正規表現のファイルには適用しない。置換前が4か所とも1か所ずつ一致しないと何も変更しない）
4. `git diff` で意図した変更だけか確認し、構文チェックと実取得の試験（`--force --data-dir /tmp/spx_probe2`）をしてから commit と push
5. VPS と Muse VM の両方に反映する

## 作業場所のコピーの扱い

- `ai-repo/projects/spx-collector/collect_spx.py`（作業場所）は**古い版**で、`SPXW?` のまま。クローンにコピーしてはいけない。直した版は、クローンから作業場所へコピーし直して置き換える
- 作業場所のコピーを使う作業の前に、リポジトリの最新をコピーしたか必ず確認する（Claude の手順に追加）。全体の書き直しは避け、置換前を照合するパッチ（assert 付き）にする

## 引け枠が欠けた件（11/18）の結論

- 欠けたのは Muse VM 側のDB。VPS では11/18の引け枠は取れていた。直接の原因は、窓の中でポーリングが走っていなかったこと（Muse のフックの実行ログ。窓内は15分間隔で1日1回しかなく、その1回が抜けた）
- 後から取り直しは、翌営業日の寄り前までは可能（検証済み: 2026-11-19 06:00 UTC に取得して data_asof_et 2026-11-18T16:14:59、満期5つ、当日満期も残る）
- 対策: (1) Muse のフックを5分間隔に、稼働時間を 07:15 JST まで。(2) 引け窓を 17:15 ET まで（パッチ）。(3) `collect_inputs.py` に、データ時点が 16:00 ET より前なら警告を出すパッチ `patch_collect_inputs_asof_warning.py`。(4) Muse のカード・トラッカーは、引け枠が無い日に朝枠を引け値として出さない

## 変更したファイル

- ai-repo/handoff/patch_collect_spx_close_window.py（新規）、patch_collect_inputs_asof_warning.py（新規）
- ai-repo/projects/spx-collector/collect_spx.py（作業場所のコピー。**古い版で、使わない**）
