# infra（VPS・cron・ディレクトリ）

## VPS

- サービス: WebARENA Indigo（1GB）、OS: Debian 12
- 接続: SSH鍵のみ（コンソールログイン不可、rootは不可）。基本作業ユーザーは `pi`（sudo付き）
- ファイアウォールはufwではなくWebArenaコンソール側（インバウンドのみ）
- Webサーバー: nginx（HTTPS: Let's Encrypt / certbot）
- AI CLI: Codex CLI、Antigravity CLI（agy）をインストール済み。AI用には専用アカウントを使う方針
- 旧VPS（XServer）はFX監視側の移行が完了するまで一部残っている

## ディレクトリ（VPS上）

| パス | 内容 |
|---|---|
| `~/work/spx_codex` | `collect_spx.py`（Cboe SPXオプション → SQLite）、`check.py`、AGENTS.md |
| `~/work/spx_inputs` | `collect_inputs.py`、`run_judgment.py`、`evaluate.py`、`judgments/`、`experiments/`、`manual/`、`notes.md` |

## cron（概要）

| 処理 | 頻度 |
|---|---|
| `collect_spx.py` | 15分おき、日本時間22〜6時台。土日は止める |
| `collect_inputs.py` → `run_judgment.py` | 平日7:30（JST） |

- 朝枠は9:45ET、引け枠は16:30ET
- VPSのcronは土日を止めている

## 外部データ

- Cboe（SPXオプションJSON。timestampはUTC）
- FRED、Finnhub（キーは設定ファイルに置く。コミットしない）
- OANDA US500日足（採点用の価格。OANDA CFDと現物SPXは水準が違う点に注意）
- FREDメンテ時は前回取得分で代用

## 無料枠の確認

- agy: `agy -p "/quota"`
- 週次クォータ: Claude・GPT系は10/26 13:13 JSTにリセット、Geminiは11/2 07:30 JSTにリセット（10/26時点の情報）

## 秘密情報の置き場

- 各ディレクトリの `config.ini` / `keys.ini`（`.gitignore` 済み）。リポジトリには入れない
- Museのキーは Muse側の `keys.ini`（FRED・Finnhubのみ）
