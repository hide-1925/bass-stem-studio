# Baseline（監査開始時点、2026-09-29）

個人メールアドレス・実名・秘密値は記載しない。

| 項目 | 値 |
|---|---|
| リポジトリ | `hide-1925/bass-stem-studio`（Private、2026-09-27 作成） |
| 作業ツリー | clean（`git status` で変更なし） |
| リモート | `origin` = `https://github.com/hide-1925/bass-stem-studio.git` |
| ブランチ | `main`、`remotes/origin/main` |
| tag | なし |
| HEAD | `1eaa8aa`（ローカル）／ `8107eca`（リモート） |
| 到達可能な commit | 4 |
| gh | ログイン済み（アカウント `hide-1925`） |
| PR / Issue / Release | 0 / 0 / 0 |
| Wiki | 未作成（`.wiki.git` が存在しない） |
| Pages | 無効（API 404） |
| Actions | 実行 0・artifact 0 |
| commit コメント | 0 |
| gitleaks / trufflehog / git-filter-repo | 未導入 |

## 監査の範囲

作業ツリー、全 reachable history の blob、全 commit の author / committer / message、GitHub 側（PR・Issue・Release・Wiki・Pages・Actions・commit コメント）。
配布 ZIP は存在しない。`workspace/`（利用者の曲・ステム・ログ）は `.gitignore` 対象で、一度も追跡されていないことを全履歴で確認した。
