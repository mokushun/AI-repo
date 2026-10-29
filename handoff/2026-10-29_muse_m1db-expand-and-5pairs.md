# 2026-10-29 Muse: m1.db 拡張と仲値5ペア初回測定

- ユーザーが VPS 側で m1.db に通貨ペアを追加し、Muse VM に再ダウンロードした
  - 21銘柄 / 46,002,239行 / 約4.6GB / 2020-10-01〜2026-10-29 UTC（EUR_USD・XAU_USD は 2020-01-01 から）
  - quick_check ok、USD/JPY の重複期間は旧版と完全一致、追加ペアに逆スプレッドなし
  - 旧版は `~/workspace/oanda_json/m1_2026-10-22.db` として保持。新版が正式な検証材料
- `projects/m1-research/` に追加（未コミット）
  - `scripts/nakane_5pairs_firstpass.py` / `outputs/nakane_5pairs_firstpass_out.txt`
  - `findings/2026-10-29_nakane-5pairs-firstpass.md`
  - README のデータ表を新版に更新
- 要点: 仲値（現行構成）は5ペアとも全期間プラス。AUD_JPY が最弱（非ゴトー日は平均0.00pip）。
  2026年9〜10月は他4ペアが価格ベースでは小幅プラスで、実売買のマイナスは価格以外が主因の見立て
- 次の候補: ペア別の IN/OUT 再グリッド、スワップ込み概算、ゴトー日数量の再検討材料づくり
