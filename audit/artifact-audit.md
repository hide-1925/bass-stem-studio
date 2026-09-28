# 成果物・バイナリの監査（2026-09-29）

| 対象 | 結果 |
|---|---|
| 追跡中の ZIP / archive | なし（全履歴でも 0） |
| 追跡中の音声ファイル | なし（全履歴でも 0。`workspace/` は一度も追跡されていない） |
| 同梱の第三者コード | `web/js/vendor/SignalsmithStretch.mjs`（npm `signalsmith-stretch` 1.3.2、MIT）。WASM を base64 で内包するため中身は文字列走査の対象外。npm 配布物からの複製で、改変していない |
| 生成物（診断レポート ZIP、監査の作業フォルダ、bundle） | `.gitignore` 対象（`bss-diagnostics-*.zip`、`.audit-work/`、`*.bundle`）。試験で固定 |
| 利用者データ（曲・ステム・音符・ログ） | `workspace/` に保存。`.gitignore` 対象。試験で固定 |

配布 ZIP を作る運用は無いので、`dist/` の見直しは対象外。
