# infra（VPS・cron・ディレクトリ）

## VPS

- サービス: WebARENA Indigo（1GB）、OS: Debian 12
- 接続: SSH鍵のみ（コンソールログイン不可、rootは不可）。基本作業ユーザーは `pi`（sudo付き）
- ファイアウォールはufwではなくWebArenaコンソール側（インバウンドのみ）
- Webサーバー: nginx（HTTPS: Let's Encrypt / certbot）
- AI CLI: Codex CLI、Antigravity CLI（agy）をインストール済み。AI用には専用アカウントを使う方針
- 旧VPS（XServer）はFX監視側の移行が完了するまで一部残っている

## 共有作業場所（MCP）

- VPS上の `/srv/mcp-workspace/` に、ユーザーとAIが共有する作業場所がある。チャットのClaudeなどが、MCPサーバー（コード保存用。サービス名 `mcp-files.service`）を通して、ファイルの一覧・読み込み・書き込みができる。削除とコマンド実行はできない
- 現状、cronとは無関係。作業場所のコードを、cronが実行しているわけではない（cronは下の「cron（概要）」のとおり、別の場所で動いている）
- 中身
  - `ai-repo/`: このリポジトリ（`.git` あり）
  - `FX/`、`GMO_Trade/`: トレード用のコード。このリポジトリには入れず、別のプライベートなリポジトリで管理する。作業場所のものはそのクローンではなく、ユーザーが手動で反映する
  - `mcp/`: MCPサーバー自身のソースと設定
  - `tmp/`（一時ファイル）、`trash/`（不要ファイルの置き場。AIは削除できないため、ユーザーが移す）、`.history/`（上書き前の自動保存）
- ルール: 作業場所の直下にある `RULES.md`（使い方のルール）と `PROGRESS.md`（作業の予定と経過）。MCPで作業するAIは、作業前に両方を読み、作業後に `PROGRESS.md` を更新する。このリポジトリの中の作業では、`AGENTS.md` の手順にも従う。MCPの使い方・権限・実行方法は `RULES.md`、リポジトリ内の書き方と記録の付け方は `AGENTS.md` を優先する
- 権限: MCPは専用ユーザー `mcpfiles` で動く。共有グループは `mcpdev`。MCPが作るファイルは 640、フォルダは 750。ユーザーが直接編集するときは `sudo` を使う
- 実行: AIはVPS上でコマンドを実行できない。実行はユーザー（またはcron）が行う。実行用の専用ユーザーも作ってある。実行ユーザーの使い分けと、試し実行から本番への流れは、決まり次第ここに追記する
- Git: このリポジトリのコミットとプッシュは、当面ユーザーが手動で行う。AIは行わない
- 秘密情報: 作業場所には、APIキー・トークン・`config.ini`・`keys.ini` を置かない。MCPの接続情報（URL・トークン）も、このリポジトリに書かない

## ディレクトリ（VPS上）

| パス | 内容 |
|---|---|
| `~/work/spx_codex` | `collect_spx.py`（Cboe SPXオプション → SQLite）、`check.py`、AGENTS.md |
| `~/work/spx_inputs` | `collect_inputs.py`、`run_judgment.py`、`evaluate.py`、`judgments/`、`experiments/`、`manual/`、`notes.md` |

## cron（概要）

- VPSのcronは動いている。動かしているのは、ユーザー本人のジョブと、`aitrader` ユーザーのSPX関連のジョブ

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
- 共有作業場所（`/srv/mcp-workspace/`）には置かない
- Museのキーは Muse側の `keys.ini`（FRED・Finnhubのみ）

## Muse VM
- collect_spx.py: 15分おきに時刻判定スクリプトが起動し、平日22:00〜翌6:59 JSTの間だけ本体を実行（失敗時のみAIを起動して報告）。DBは ~/spx_data（リポジトリ外）
- 毎朝7:30 JST: git pull → collect_inputs.py（SPX_OPTIONS_DB指定）→判断→チャット報告。リポジトリは読み取り専用
