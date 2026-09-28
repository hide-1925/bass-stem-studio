# セキュリティとプライバシー

## この PC に保存するもの

| 場所 | 中身 |
|---|---|
| `workspace/projects/<ID>/` | 読み込んだ元ファイル、44.1 kHz に変換した音声、分離したステム、音符・運指、編集履歴、設定（EQ など） |
| `workspace/logs/` | サーバーのログ、出来事ログ（取り込み・処理の開始/終了/失敗・内部エラー）、ブラウザのエラー |
| `workspace/jobs/` | 分離・採譜の実行記録とログ |
| `workspace/trash/` | 削除したプロジェクト（完全には消していない） |
| ブラウザの保存領域（localStorage） | 再生音量、最後に開いたプロジェクトの ID |
| `%USERPROFILE%\.cache\huggingface` | 分離モデル（約 110 MB） |

`workspace/` は `.gitignore` 対象で、Git には入らない。

## 外部との通信

- サーバーは `127.0.0.1`（この PC の中）だけで待ち受ける。同じネットワークの他の端末からは接続できない
- 曲・ステム・音符・ログを外部へ送る処理は無い
- 例外は **初回の分離でのモデルのダウンロード**（Hugging Face `adefossez/HTDemucs-6s`）と、セットアップ時の pip / PyTorch の取得だけ
- API キー・パスワード・アカウントは使わない

## 診断レポート（ZIP）に入るもの

画面の「診断 → レポート書き出し」で作る ZIP には次が入る。

- 入る：環境情報（OS・CPU・メモリ・ライブラリの版）、動作チェックの結果、サーバーログ、ブラウザのエラー、処理の記録とログ、（選んだ場合）開いている曲の設定・音符・元ファイルの診断結果
- 入らない：音声（元の曲・ステム）
- **ファイル名、フォルダのパス（Windows のユーザー名を含む）、曲名が入る。** 人に送る前に中身を確認すること

## 著作権

取り込んだ楽曲は個人の練習用途に限る。楽曲・ステム・採譜結果をこのリポジトリに入れたり、共有・配布したりしないこと（アプリにも共有機能は無い）。

## 公開前のチェック

```bash
python tools/security_scan.py --all
```

作業ツリー・全履歴の blob・全 commit の author / committer / message を走査し、API キー・トークン・秘密鍵・Bearer・メールアドレス・Claude Code の session URL・ホームディレクトリのパスを探す。
実名などの固有の語は、Git に入らない `.audit-private-terms.txt`（1行1語）に書いておくと一緒に探す。値そのものは表示せず、SHA-256 の先頭8桁だけを出す。

commit は公開用の identity（GitHub の noreply アドレス）で作ること。このリポジトリでは `git config user.email` に設定済み。

## 脆弱性の報告

GitHub の **Security → Report a vulnerability**（非公開の報告）を使う。Issue には書かないこと。

## 秘密情報を誤って push したとき

1. **まず失効・再発行する**（履歴を消しても、一度公開された値は安全にならない）
2. 手元の全 ref を bundle でリポジトリ外にバックアップする
3. `git filter-repo`（または `git filter-branch`）で全履歴から除去し、`python tools/security_scan.py --all` で 0 件を確認する
4. force push する。公開リポジトリなら作り直しや GitHub Support への削除依頼も検討する
5. 別フォルダへ clone し直して再走査する
