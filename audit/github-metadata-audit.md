# GitHub 側メタデータの監査（2026-09-29）

| 対象 | 件数 | 結果 |
|---|---|---|
| Pull Request（本文・コメント・review） | 0 | 対象なし |
| Issue（本文・コメント） | 0 | 対象なし |
| Release（本文・asset） | 0 | 対象なし |
| tag | 0 | 対象なし |
| Wiki | 未作成 | 対象なし（使わないなら設定で無効化を推奨） |
| Pages | 無効 | 対象なし |
| Actions（実行・artifact） | 0 / 0 | 対象なし |
| commit コメント | 0 | 対象なし |
| リモートの commit identity | 3 commit すべてが個人メール | F-01（書き換え対象） |

PR が無いので、Duo のような `refs/pull/*/head` に旧 commit が残る問題は起きない。
ただし force push（案 A）では、旧 commit は GitHub 上で SHA を直接指定すると一定期間取得できる可能性がある（`history-audit.md` §4）。
