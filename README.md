# Bass Stem Studio

曲を 6 つのパート（ボーカル / ドラム / ベース / ギター / ピアノ / その他）に分離して、
**ベースやギターを聞き取りやすくしながら練習** し、ベースの音程をピアノロールと推定TABで確認・修正できるローカルアプリです。

- 🎚 **6 ステム分離**（Demucs `htdemucs_6s`）と共通時間軸での同時再生
- 🔊 **ミキサー**：ステムごとにミュート / ソロ、ベース以外は −30 / −20 / −10 / 0 / +10 dB（ベースは 0 dB 固定）
- 🛡 **音割れ防止**：実際の加算ピークから自動ヘッドルーム＋ルックアヘッド・リミッター（作動中は画面に表示）
- 🔁 **A/B ループ**・**速度 50〜150%（10% 刻み、音程維持）**。何倍速でもステム間のずれ 0
- 🎛 **FX ラック（マスター全体 / ステムごと）**：
  - **EQ**：8 バンド（HPF / LOW / LO-MID / MID / HI-MID / PRES / HIGH / LPF）。リアルタイムのスペクトルに周波数特性カーブを重ねて表示し、点をドラッグして調整（**5 dB 刻み**、Shift で 0.5 dB）。ベース開放弦の周波数も目盛りに表示
  - **フィルター**：ハイパス / ローパス（12 / 24 dB/oct）
  - **コンプレッサー**：入出力の伝達カーブ（今の動作点をリアルタイム表示）、アタック / リリースのエンベロープ図、GR メーターと履歴
  - プリセット（ベースを聞き取る、ギターを聞き取る、ボーカルを控えめに、ジャンル別など）
- 🔊 **再生音量**（リミッターの後段なので音割れ防止に影響しない）
- 🩺 **診断**：動作チェック、ファイル診断（開けない理由を表示）、エラーログ（サーバー・ブラウザ）、ジョブ履歴、診断レポート（ZIP）
- 🎼 **ベース採譜**：Basic Pitch＋pYIN＋onset 検出でピアノロール化。低信頼の音をオレンジで強調
- 🎸 **推定TAB**：4 弦 / 5 弦 / 半音下げ / カスタムのチューニングで、弦・フレットを曲全体で最適化
- ✏️ 音高・時刻・弦フレットを修正（取り消し・やり直し付き）→ **MIDI / CSV / TAB 印刷用 PDF** に書き出し
- 🌐 **YouTube などブラウザで再生中の曲** を「タブの音を録音」で取り込み可能（Chrome / Edge）
- 🔒 すべてこの PC 内で処理（外部送信なし。初回のモデル取得のみネットに接続）

> 個人の練習用途を想定しています。取り込んだ楽曲の共有・配布はしないでください（アプリにも共有機能はありません）。
> 保存するデータ・外部との通信・診断レポートの中身は [SECURITY.md](SECURITY.md) にまとめています。

## 動作環境

- Windows 10 / 11（macOS / Linux でもおおむね動く構成ですが未検証）
- Python 3.11（3.10〜3.12）
- Chrome または Edge（タブ録音は Chromium 系のみ）
- GPU は不要（CPU で 3 分の曲を約 25 秒で分離。Core Ultra 7 265K 実測）

## セットアップと起動

```bat
setup.bat          :: 仮想環境 .venv を作り依存をインストール（CPU 版 torch）
run.bat            :: サーバーを起動してブラウザで http://127.0.0.1:8765/ を開く
```

NVIDIA GPU を使う場合は `setup.bat -Gpu`（CUDA 12.6 版 torch。GTX 10 系は未検証）。
Demucs のモデル（約 110 MB）は初回の分離時に自動でダウンロードされます。

## 使い方

1. **ファイルを開く**（WAV / MP3 / FLAC / M4A、ドラッグ＆ドロップ可）か **タブの音を録音**。
   読込 → 音源分離 → 採譜が自動で進みます（下部に進捗・中断・再試行）。分離が終わった時点で再生できます。
2. 各ステムの **M**（ミュート）/ **S**（ソロ）/ 音量で聞きたいパートを強調。
3. 波形をドラッグして **A/B ループ**、速度を落として練習。
4. ピアノロール / TAB の音符を **クリックするとその位置から再生**。誤りは下のキーで修正して **保存（Ctrl+S）**。
5. **書き出し** から MIDI / CSV / PDF。テンポ（BPM・拍子・1 小節目の位置）を設定すると小節割りの PDF や量子化も使えます。

| 操作 | キー |
|---|---|
| 再生 / 停止 | Space |
| A 点 / B 点 / ループ ON・OFF | A / B / L |
| 速度 −10% / +10% | [ / ] |
| 前 / 次の音符 | ← / → |
| 音高 ±半音 / ±オクターブ（音そのものの修正） | ↑↓ / Ctrl+↑↓ |
| 同じ音高のまま弦を変更（運指の修正） | Alt+↑↓（TAB 上で数字を上下にドラッグでも可） |
| 削除 / 再生位置で分割 / 結合 | Delete / S / M |
| 音符の追加 | ピアノロールの空き部分をダブルクリック |
| 取り消し / やり直し | Ctrl+Z / Ctrl+Y |
| 保存 | Ctrl+S |
| EQ / コンプの表示・非表示 | E（ステムの **FX** ボタンでそのステムを直接開く） |
| 拡大縮小 / 横スクロール | Ctrl+ホイール / ホイール（ピアノロールの音域は Shift+ホイール） |

## うまくいかないとき（診断）

右上の **診断** ボタン（処理が失敗したときは下部の **診断** ボタン）から:

| タブ | 内容 |
|---|---|
| 動作チェック | 作業フォルダ・ディスク・メモリ、WAV / FLAC / MP3 / M4A の読込、分離モデル、採譜エンジン、PDF フォント、ブラウザの機能（AudioWorklet・44.1 kHz・タブ録音など）を確認 |
| ファイル診断 | ファイルを選ぶと取り込まずに中身を調べる（形式・コーデック・サンプルレート・WAV のチャンクとタグの文字コード・壊れ・無音・クリップ）。開いている曲の元ファイルも診断可 |
| エラー・ログ | サーバーの出来事（取り込み・ジョブの開始/終了/失敗・内部エラー）とブラウザのエラーを時系列で表示 |
| ジョブ履歴 | 分離・採譜の結果・所要時間・失敗理由・ワーカーログ（再起動後も残る） |
| レポート書き出し | 上記一式の ZIP（音声は含まない。ファイル名・パスは含む） |

ログは `workspace/logs/`（`app.log`、`events.jsonl`、`client.jsonl`）とジョブごとの `workspace/jobs/*.log` / `*.result.json` に残ります。

## データの保存場所

`workspace/projects/<ID>/` に 1 曲 1 フォルダで保存されます（git 管理外）。

```
project.json       設定・モデル名・実行環境・処理時間
source/            読み込んだ元ファイル
audio/mix.wav      44.1 kHz に揃えた原音（全処理の時間基準）
stems/htdemucs_6s/ 6 ステム（同じ長さ・同じ先頭）と stems.json
notes/notes.json   音符（手修正込み）、auto-*.json は自動採譜の生結果
history/           取り消し履歴・保存ごとのスナップショット・編集ログ
```

再採譜しても、手修正した音符（音高・時刻・運指）と削除した音符は上書きされません。削除したプロジェクトは `workspace/trash/` に移動します。

## 構成

```
bss/                 Python（FastAPI サーバーと解析）
  separation/        音源分離（Demucs）… Separator を実装して REGISTRY に登録すれば交換可能
  transcription/     ピッチ検出（basic_pitch / pyin / fused）… Transcriber を実装して登録
  fingering/         チューニング・候補列挙・運指の全体最適化（Viterbi）
  export/            MIDI / CSV / PDF
  worker.py, jobs.py 重い処理を子プロセスで実行（進捗・中断・再試行）
web/                 フロントエンド（ビルド不要の ES Modules + Canvas + Web Audio）
  js/audio/engine.js 12ch 伸縮ノード（Signalsmith Stretch）＋ミキサー＋リミッター
  js/audio/fx.js     EQ / フィルター / コンプのチェーン（ステムごと＋マスター）
  js/views/fxpanel.js FX ラックの画面（スペクトル・EQ カーブ・伝達カーブ・エンベロープ）
tools/               合成試験データ生成・精度評価・ベンチマーク
tests/               pytest
docs/SPEC.md         開発仕様書
docs/MEASUREMENTS.md 実測値と残る誤認識例
```

## テスト・計測

```bat
.venv\Scripts\python -m pytest -q                              :: テスト（35 件。API は一時フォルダで実行）
.venv\Scripts\python tools\make_testset.py                     :: 正解付き合成曲を生成
.venv\Scripts\python tools\evaluate.py --testsets              :: ベース単独 / ミックスの採譜精度
.venv\Scripts\python tools\evaluate.py --audio bass.wav --reference ref.csv   :: 手持ちの正解データで評価
.venv\Scripts\python tools\bench_pipeline.py song.mp3          :: 分離時間・メモリ（サーバー起動中に実行）
```

同期と音割れの計測は起動中に http://127.0.0.1:8765/measure.html を開いて「計測開始」。

## 既知の制約

- 分離品質は曲次第です。htdemucs_6s のピアノ分離は公式にも品質の留保があり、ギター / ピアノ / その他は混ざることがあります。
- 採譜は単音主体のベースラインを「手直しできる水準」にすることが目標で、ベンド・スライド・ゴースト・和音・極端な低音は保証外です。TAB は推定です。
- 合成曲での精度は上限の目安です（[docs/MEASUREMENTS.md](docs/MEASUREMENTS.md)）。
- ブラウザはステム全体を保持するため、3 分の曲で約 370 MB を使います。

## 利用しているソフトウェア

[Demucs](https://github.com/facebookresearch/demucs)（MIT）/ [Basic Pitch](https://github.com/spotify/basic-pitch)（Apache-2.0）/
[Signalsmith Stretch](https://signalsmith-audio.co.uk/code/stretch/)（MIT、`web/js/vendor` に同梱）/ [librosa](https://librosa.org/)（ISC）/
PyTorch / FastAPI / PyAV（FFmpeg）/ soundfile / mido / reportlab ほか。
