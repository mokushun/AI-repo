# status（現状と次にやること）

最終更新: 2026-11-10

## プロジェクト一覧

| プロジェクト | 状態 |
|---|---|
| spx-judgment（S&P500のAI判断） | 稼働中（シミュレーション、無料枠） |
| 日経225ミニのオプション収集 | 稼働中（10/23（金）から再開。収集のみ。10/6〜10/22は欠損） |
| oanda-monitor（FX監視） | このリポジトリには入れていない |

## spx-judgment の現状

- 目的: 次の米国取引日のS&P500終値が前日を上回る確率（p_up）をAIに出させ、記録・採点する
- 動いているもの
  - `collect_spx.py`: CboeのSPXオプションをSQLiteに保存（cron、15分おき、日本時間22〜6時台）
  - `collect_inputs.py` → `run_judgment.py`: 平日7:30（JST）にcronで実行。agyの3モデルと、Codex（gpt-6-luna。11/2から）の4系統で判断
  - `evaluate.py`: 採点（OANDA US500日足、ブライアススコア）。AIなしの基準（A: 常に0.5、B: 期間の上昇日の割合、C: 前日と同じ方向）との比較も出力する（`python3 evaluate.py`。APIキー不要で、何度実行しても枠は減らない）
- 結果（11/2時点、各10日）

| モデル | 平均ブライアス |
|---|---|
| gpt-oss-120b-medium | 0.189 |
| gemini-3.8-flash-high | 0.200 |
| claude-sonnet-4-6 | 0.220 |
| （基準）常に0.5 | 0.250 |

  期間中の上昇日は60%。まだ優劣は言えない。表の数値は、10/26時点（各5日）と同じ値で、更新が追いついていない可能性がある。`python3 evaluate.py` の最新の出力で更新する
- Museの試行: 平日7:30（JST）にMuseのVMで collect_inputs.py --slot close を実行し、結果を判断させてチャットに報告させている（初回10/27成功、bullish・p_up=0.55）。Museはリポジトリをcloneしてgitで同期する（コードは読み取り専用）。判断結果は `judgments/` に `*_close_muse.json` として push される（10/28以降）。VPSの採点（evaluate.py）への取り込みは当面手動。オプションDBは、MuseのVMで collect_spx.py（projects/spx-collector）を回して持たせる予定で、それまでは7/8セクション。オプションが揃うまでは入力条件がVPSの3モデルと違うため、採点では別枠として扱う

## 次にやること

1. 件数が数十件たまったら、`evaluate.py` の出力（AIなしの基準との比較を含む）で、優劣を判断する
2. 判断の収集はMuseにやらせ、結果をVPS側で取得する方向を検討
3. Museの判断結果をVPSの採点（evaluate.py）にどう取り込むか決める（いまは手動。Museの結果はリポジトリの `judgments/` に push されている）。MuseのVMでオプション収集が動くか確認する

## 注意

- 10/22にevents取得失敗が1回、10/26にgpt-ossが一時エラー後リトライ成功
- FOMC日程（10/28、12/9）とFRED休場日は手入力

## 直近の変更

- 2026-11-10: `AGENTS.md` の環境メモに `pi` と `aitrader` の両方を記載、「書き方」にコードブロックのルールを追記、「編集の衝突を避ける」に `git pull` と、編集後すぐの `push` を追記。`docs/status.md` の `evaluate.py` の記述を、実装済みの基準との比較に合わせて修正（Claude）
- 2026-11-09: `docs/infra.md`（基本作業ユーザーに `pi` と `aitrader` の両方を記載、Muse VMの記述を実際の運用に修正）、`docs/status.md`（Museの記述、「次にやること」の整理）、`projects/spx-judgment/README.md`（「今後」と手順3）を、現状に合わせて修正（Claude）
- 2026-11-08: `docs/infra.md` に「共有作業場所（MCP）」を追記、`CLAUDE.md` をMCP経由の書き込みに合わせて更新、`docs/decisions.md` に共有作業場所・権限・コミット手動の決定を追記（Claude）
- 2026-11-08: `docs/status.md` の重複を整理（「Museの試行」の重複段落を削除、「次にやること」の3と4を分割）。`README.md` の構成図を実際の配置に合わせた。aitrader側とのstatus.mdの競合を、両方の内容を残して統合した（Claude）
- 2026-11-02: Codex（gpt-6-luna）を追加。cronの `--model` に `codex:gpt-6-luna` を足し、agyの3モデルと合わせて4系統で毎朝実行（読み取り専用・推論なしの既定。使用量はスクリプトに記録されない）
- 2026-11-02: 無料枠の確認結果。Gemini 残り99%（リセット11/9 07:30 JST）、Claude・GPT系 残り87%（リセット11/3 07:31 JST）。ペースに余裕があり、3モデルは継続
- 2026-10-31: Museの試行の内容を記載（詳細は上の「spx-judgment の現状」）
- 2026-10-27: ナレッジのファイルを削除し、GitHub共有に移行。AI用リポジトリを作成
- 2026-10-22: `collect_spx.py` の集計で期限切れ満期を除外するよう修正
- 2026-10-21: WTI・BRENT・XAUUSD・NATGASを `update.py` に追加（OANDA側）
