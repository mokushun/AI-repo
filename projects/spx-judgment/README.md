# spx-judgment

S&P500の「次の米国取引日の終値が前日を上回る確率（p_up）」をAIに出させ、記録・採点するシミュレーション。売買はしない。

## パイプライン

1. `collect_spx.py`（`~/work/spx_codex`）: CboeのSPXオプションをSQLiteに保存
2. `collect_inputs.py`（`~/work/spx_inputs`）: 8セクションの入力mdを作る
3. `run_judgment.py`: agyで3モデルに判断させ、`judgments/` にJSONで保存（プロンプトv3）
4. `evaluate.py`: OANDA US500日足で採点（ブライアススコア）

## モデル

- gemini-3.8-flash-high
- claude-sonnet-4-6
- gpt-oss-120b-medium

## ファイル・フォルダの意味

| 名前 | 内容 |
|---|---|
| `judgments/` | 判断結果のJSON |
| `judgments/exclude.txt` | 採点から除外する判断の一覧 |
| `experiments/` | 実験用 |
| `manual/` | 手動実行の記録 |
| `notes.md` | 気づきの記録 |

## 注意

- Cboe JSONのtimestampはUTC
- 集計（all）は期限切れ満期を除く
- OANDA CFDと現物SPXは水準が違う
- FOMC日程（10/28、12/9）とFRED休場日は手入力。FRED障害時は前回取得分で代用
- 比較の基準: 常に0.5を出したときのブライアスは0.250

## 今後

- 20日分前後たまったら、AIなしの単純な基準との比較を `evaluate.py` に追加
- 11/2頃に無料枠の状況を確認し、モデルを決める

## 実際のコードについて

このREADMEは説明のみ。スクリプト本体（`collect_spx.py` など）は、VPSからこのディレクトリにコピーして追加する。`config.ini`・`keys.ini`・DB・`judgments/` の生データのうち公開したくないものはコミットしない。
