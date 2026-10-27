# status（現状と次にやること）

最終更新: 2026-10-27

## プロジェクト一覧

| プロジェクト | 状態 |
|---|---|
| spx-judgment（S&P500のAI判断） | 稼働中（シミュレーション、無料枠） |
| 日経225ミニのオプション収集 | VPS切り替え後から停止中（検討中） |
| oanda-monitor（FX監視） | このリポジトリには入れていない |

## spx-judgment の現状

- 目的: 次の米国取引日のS&P500終値が前日を上回る確率（p_up）をAIに出させ、記録・採点する
- 動いているもの
  - `collect_spx.py`: CboeのSPXオプションをSQLiteに保存（cron、15分おき、日本時間22〜6時台）
  - `collect_inputs.py` → `run_judgment.py`: 平日7:30（JST）にcronで実行。3モデルで判断
  - `evaluate.py`: 採点（OANDA US500日足、ブライアススコア）
- 結果（10/26時点、各5日）

| モデル | 平均ブライアス |
|---|---|
| gpt-oss-120b-medium | 0.189 |
| gemini-3.8-flash-high | 0.200 |
| claude-sonnet-4-6 | 0.220 |
| （基準）常に0.5 | 0.250 |

  期間中の上昇日は60%。まだ優劣は言えない
- Museの試行: 平日7:30（JST）に `collect_inputs.py --slot close` → `run_judgment.py --dry-run --show-prompt` を実行させ、結果をチャットに報告させる設定済み（初回10/27 7:30）。オプションDBが無いので7/8セクション

## 次にやること

1. 10/27: Museの初回実行結果を確認する
2. 11/2頃: 無料枠の状況（Claude・GPT系は11/2頃まで）を確認し、使うモデルを決める
3. 20日分前後たまったら、AIなしの単純な基準との比較を `evaluate.py` に足す
4. 日経225ミニ（`collect_nkopmini.py`）を来週から新VPSで再開するか決める
5. 判断の収集はMuseにやらせ、結果をVPS側で取得する方向を検討

## 注意

- 10/22にevents取得失敗が1回、10/26にgpt-ossが一時エラー後リトライ成功
- FOMC日程（10/28、12/9）とFRED休場日は手入力

## 直近の変更

- 2026-10-27: ナレッジのファイルを削除し、GitHub共有に移行。AI用リポジトリを作成
- 2026-10-22: `collect_spx.py` の集計で期限切れ満期を除外するよう修正
- 2026-10-21: WTI・BRENT・XAUUSD・NATGASを `update.py` に追加（OANDA側）
