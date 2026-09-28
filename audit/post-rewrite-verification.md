# 作り直し後の検証（2026-09-29）

owner の決定（`history-audit.md` §5）に従い、旧リポジトリを削除して Private で作り直し、最新の作業ツリーだけを親の無い 1 commit として登録した。
検証は、既存の作業ツリーを使わず **GitHub から別フォルダへ clone し直して** 行った。

| 確認 | 結果 |
|---|---|
| commit 数 | 1（親なし）＋この記録の commit |
| author / committer | すべて `hide-1925 <209939878+hide-1925@users.noreply.github.com>` |
| `tools/security_scan.py --all`（作業ツリー・全 blob・commit メタデータ、private term 8語） | 0 件 |
| 試験 | 40 件通過 |
| 旧 commit（`2b0996e`・`8c89d94`・`8107eca`）を GitHub API で取得 | 3件とも not found |
| 可視性 | PRIVATE |
| PR / Issue / Release / tag | 0 / 0 / 0 / 0 |
| 追跡中の音声・ZIP・bundle・`workspace/` | 0 件 |
| ローカルの旧 commit | reflog 失効＋gc で削除（`git cat-file` で不在を確認） |

## 残るもの

- 旧履歴（個人メール・実名を含む）は、リポジトリ外のバックアップ `..\bass-stem-studio-pre-sanitize.bundle` にだけある。不要になれば owner が削除してよい
- GitHub 内部のキャッシュまで消えたとは断定しない（旧リポジトリは Private で、PR・fork も無かった）
- Wiki は未使用のまま有効になっている。使わないなら設定で無効化を推奨
