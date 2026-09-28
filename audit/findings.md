# Findings

値そのものは書かない。個人メールアドレス・実名・private term は、規則名・件数・長さ・SHA-256 の先頭8桁（`fp`）だけで示す。
`Raw value is redacted` はすべて **yes**。監査日 2026-09-29、対象 `hide-1925/bass-stem-studio`（Private）。

**Critical（有効な API key・token・password・cookie・client secret・private key）は 0 件。** キーの失効・再発行は不要（そもそもこのアプリは API キーを扱わない）。

| ID | Severity | 要約 | 場所 | 状態 |
|---|---|---|---|---|
| F-01 | High | 個人メールアドレス（fp `604736db`、27文字）が commit の author / committer に入っている | 全 commit（リモート3件＋未 push 1件）のメタデータ | **解消**（作り直し＋履歴を残さない1 commit。`post-rewrite-verification.md`） |
| F-02 | High | 実名（private term #1・#2）が author / committer の name と email に入っている | 同上 | 同上 |
| F-03 | Low | 利用者の音楽ライブラリの曲名（private term #7）が試験の固定文字列に入っている | `tests/test_api.py`、`tests/test_audio_export.py`（現行＋履歴3 commit） | **解消**（現行を修正し、旧履歴は登録しない） |
| F-04 | Medium | 正式な LICENSE が無い | リポジトリ直下 | owner の判断で付けない（Private 運用）。公開時に再検討 |
| F-05 | Low | 以後の commit が個人 identity で作られる設定だった（リポジトリ単位の `user.name` / `user.email` が未設定） | `.git/config` | **修正済み**（公開 identity をリポジトリ単位で設定） |
| F-06 | Low | `.gitignore` に秘密・監査入力・診断レポート ZIP の除外が無い | `.gitignore` | **修正済み**（試験で固定） |
| F-07 | Low | 診断レポート ZIP にはファイル名・フォルダのパス（Windows のユーザー名を含む）・曲名が入る（仕様） | `bss/diag.py` の `build_report_zip` | 仕様として `SECURITY.md` と画面に明記済み。音声は含まない（試験で固定） |
| F-08 | Low | commit のタイムゾーン（+0900）と時刻から作業時間帯が読める | commit メタデータ | 書き換えない（価値が低い）。記録のみ |
| F-09 | Info | PR・Issue・Release・tag・Wiki・Pages・Actions の実行／artifact・commit コメントはすべて 0 件 | GitHub | 確認済み |
| F-10 | Info | 配布 ZIP・バイナリは追跡していない。同梱の第三者コードは `web/js/vendor/SignalsmithStretch.mjs`（MIT、WASM を base64 で内包）だけ | `web/js/vendor/` | 確認済み（`artifact-audit.md`） |
| F-11 | Info | gitleaks / trufflehog は未導入のため未実行。代わりに `tools/security_scan.py` で全範囲を走査 | 道具 | 記録のみ |
| F-12 | Info | ブラウザ拡張ではないので拡張の権限監査は対象外。サーバーは `127.0.0.1` だけで待ち受け | `bss/config.py` | 確認済み |

---

## F-01 / F-02 個人メールアドレスと実名

- **Category:** PII（commit identity）
- **Location:** author 4件・committer 4件（リモートにあるのは3 commit）。ファイルの中身には無い（全 blob を走査して0件）
- **Reachability:** `main`（リモート・ローカル）から到達可能。リポジトリは Private なので現状は owner だけが見られる
- **Evidence:** `python tools/security_scan.py --all`。email 規則 fp `604736db` len 27、private term #1（fp `c908e81e`）・#2（fp `509c412b`）
- **Remediation:** 対象の identity（このメールアドレス）に一致するものだけを、Duo と同じ公開 identity `hide-1925 <209939878+hide-1925@users.noreply.github.com>` に置き換える。`Claude <noreply@anthropic.com>`（`Co-Authored-By`）は変えない
- **Verification（dry-run）:** 書き換え後の全 ref で email 0件・private term 0件。5 commit すべて author/committer の日時は元と同一。tree の差分は F-03 の箇所だけ（`history-audit.md`）
- **Status:** 解消（新しいリポジトリは1 commit のみ）

## F-03 曲名（private term #7）

- **Location:** 試験用の WAV タグの文字列（Shift-JIS タグの読込試験）
- **Remediation:** 一般的な語「日本語」に置き換え（現行ツリー）。履歴の3 commit も同じ置き換えを `--tree-filter` で行う
- **Status:** 解消

## F-07 診断レポートの中身

- 診断レポート（ZIP）は不具合相談用に、環境情報・ログ・（選べば）開いている曲の設定と音符を含む。**音声は含まない**
- ファイル名・パス・曲名は含むので、画面と `SECURITY.md` に「送る前に中身を確認」と明記した
- 生成物のファイル名 `bss-diagnostics-*.zip` は `.gitignore` に追加（誤 commit 防止）

## 残るリスク

- force push 後も、旧 commit は GitHub 上で SHA を直接指定すれば一定期間取得できる可能性がある。現在は Private なので見られるのは owner だけ。**将来 Public にするなら、作り直し（`history-audit.md` の案 B）を推奨**
- gitleaks / trufflehog は未実行
- GitHub のキャッシュ・fork・外部アーカイブまで消せるとは断定しない（Private で fork も無いので現実的なリスクは低い）
