# Public Release Gate

2026-09-29、履歴の書き換えの dry-run まで。`[x]` は確認済み、`[ ]` は未了（理由を併記）。

## Critical gate

- [x] 有効な secret 0件（作業ツリー・全 blob・commit メタデータ）
- [x] private key / credential file 0件
- [x] revoke / rotate … 見つからなかったので不要

## Privacy gate

- [ ] 対象の個人メール 0件 … dry-run では 0件。**書き換えの push は承認待ち**
- [ ] 対象の実名メタデータ 0件 … 同上
- [x] Claude Code session URL 0件（commit message・ファイルとも）
- [ ] private term 0件 … 現行ツリーは 0件。履歴は書き換えの push 待ち
- [x] 誤って入った文字起こし・診断・曲 0件（`workspace/` は一度も追跡されていない）

## Git gate

- [x] working tree clean（commit 後）
- [x] 全 public branch を監査（`main` のみ）
- [x] 全 tag を監査（なし）
- [x] commit メタデータを監査
- [x] commit message を監査
- [ ] 旧い sensitive な object に到達できない … push の方法（案 A / B）の決定待ち
- [x] force push の対象一覧を明示（`history-audit.md` §3・§4）

## GitHub metadata gate

- [x] PR / Issue / Release / Wiki / Pages / Actions / commit コメント … すべて 0 件・無効

## Artifact gate

- [x] 追跡中の ZIP・音声なし
- [x] 生成される個人用ファイル（診断 ZIP・監査の作業フォルダ・bundle）が追跡されない（`.gitignore` と試験で固定）

## Runtime security gate

- [x] API キーを扱わない（外部送信は初回のモデル取得だけ）
- [x] 診断レポートの中身（パス・曲名を含む、音声は含まない）を画面と `SECURITY.md` に明記
- [x] サーバーは `127.0.0.1` のみ

## Quality gate

- [x] 試験 40 件通過（security の回帰試験 5 件を含む）
- [ ] LICENSE … owner の決定待ち（`license-decision.md`）
