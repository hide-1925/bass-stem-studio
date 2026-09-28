# 履歴の監査と書き換え計画

## 1. 監査時点（2026-09-29）

| 項目 | 値 |
|---|---|
| リモート | `hide-1925/bass-stem-studio`（Private） |
| ブランチ | `main` のみ（リモート・ローカル） |
| tag | なし |
| リモート `main` | `8107eca`（3 commit） |
| ローカル `main` | `610dcd7`（5 commit。未 push 2件：`1eaa8aa` 起動の修正、`610dcd7` セキュリティ） |
| バックアップ | リポジトリ外 `..\bass-stem-studio-pre-sanitize.bundle`（全 ref、verify 済み。Git 管理外） |

## 2. 書き換えの内容

- **identity:** 対象のメールアドレスに一致する author / committer だけを `hide-1925 <209939878+hide-1925@users.noreply.github.com>` に置き換える（Duo と同じ公開 identity）
- **tree:** `tests/test_api.py`・`tests/test_audio_export.py` の private term #7 を「日本語」に置き換える
- **message:** 変更なし（個人メール・session URL・private term は message に無い）。`Co-Authored-By: Claude …` は残す
- **道具:** `git filter-branch`（Git 同梱）。`git filter-repo` は未導入で、5 commit なので導入しない

## 3. dry-run の結果（`.audit-work/dryrun`、使い捨ての clone）

| 元 | 書き換え後 | tree | 日時 |
|---|---|---|---|
| `2b0996e` | `f6908c9` | 同一 | 同一 |
| `8c89d94` | `d7219d4` | `tests/test_audio_export.py` の1行だけ | 同一 |
| `8107eca` | `b533ed7` | 試験2ファイルの各1行だけ | 同一 |
| `1eaa8aa` | `9aecc47` | 試験2ファイルの各1行だけ | 同一 |
| `610dcd7` | `e057cbd` | 同一 | 同一 |

書き換え後に `tools/security_scan.py --all`（作業ツリー・全 blob・全 commit メタデータ、private term 8語）で **0 件**。
※ 実際の書き換えは承認後にもう一度行うので、SHA はこの表と変わりうる（最新の commit を含めるため）。

## 4. push の方法（owner が選ぶ）

### 案 A：同じリポジトリに force push

```bash
git push --force-with-lease=main:8107ecad9c4ba78f7e30313b65ce3398d3f681f6 origin main
```

- 手軽。Private のまま使い続けるなら十分
- ただし旧 commit は GitHub 上で SHA を直接指定すると一定期間取得できる可能性がある（見られるのは owner だけ）

### 案 B：リポジトリを作り直して push（Duo と同じ）

```bash
gh auth refresh -h github.com -s delete_repo      # owner がブラウザで許可
gh repo delete hide-1925/bass-stem-studio --yes
gh repo create hide-1925/bass-stem-studio --private --source . --push
```

- 旧 commit への到達経路が残らない。**将来 Public にするならこちらを推奨**
- リポジトリの設定（説明文など）は作り直しで消える（現状は既定のままなので実害なし）

どちらでも、push 後に別フォルダへ clone し直して再走査する（`post-rewrite-verification.md`）。

## 5. owner の決定（2026-09-29）

- **案 B（リポジトリの作り直し）を採用。さらに過去の履歴は残さない。**
- 最新の作業ツリーだけを、公開 identity `hide-1925` の **1 commit**（親なし）として新しいリポジトリに登録する。§3 の書き換え（filter-branch）は使わない
- 旧履歴（個人メール・実名を含む 6 commit）は、リポジトリ外のバックアップ bundle にだけ残る
- 可視性は従来どおり Private。LICENSE は付けない（owner の判断。`license-decision.md`）
