# ai-repo

複数のAI（Claude / Antigravity CLI / Codex CLI / Muse）で共有する作業用リポジトリ。
コピペの代わりに、ここを「正」とする。

## 最初に読む順番

1. `AGENTS.md` … 全AI共通のルール
2. `docs/status.md` … 今の状況と次にやること
3. `docs/infra.md` … VPSの構成・パス・cron
4. `docs/decisions.md` … 決めたことと理由
5. 担当プロジェクトの `projects/<name>/README.md`

## 構成

```
README.md            このファイル（地図）
AGENTS.md            全AI共通のルール
CLAUDE.md            Claude用（AGENTS.mdを読む＋Claude固有の注意）
GEMINI.md            Antigravity用（同上）
docs/
  status.md          現状・次にやること（最優先で更新する）
  decisions.md       決定事項と理由
  infra.md           VPS・cron・ディレクトリ
projects/
  spx-judgment/      S&P500のAI判断シミュレーション
  n225option/        日経225のオプション収集（collect_nkopmini.py など）
handoff/             AI同士の引き継ぎメモ（日付_AI名_内容.md）
```

## 役割分担

| AI | 役割 |
|---|---|
| Claude（チャット） | 設計・コード作成・レビュー・全体の整理（メイン） |
| Codex CLI | VPS上での開発・修正・検証（AGENTS.mdとcheck.pyで確認） |
| Antigravity CLI（agy） | VPS上でのAI判断の実行（3モデル） |
| Muse | 平日朝の入力データ収集の実行と報告 |

## 注意

- APIキー・トークン・`config.ini`・`keys.ini`・DB・生データは**コミットしない**（`.gitignore` 参照）
- FX監視・トレード関連（oanda-monitor）は、トレード用APIが必要になるため、このリポジトリには**入れていない**
