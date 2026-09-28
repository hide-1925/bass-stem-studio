# Bass Stem Studio 開発仕様書 v1.0

作成日：2026-09-27 / 対象要件：「6ステム音源分離・ベース採譜アプリ 要件定義 v1.0」

参考にした X 投稿（@refuge_akira, 2026-09-18）の本文で確認できた機能：「音源を6種類の楽器で分割」「ベース以外を10dBずつ上下」「トラック別ミュート、ループ再生、再生速度10%ずつ変更」。本仕様はこれに、要件定義のベース採譜・推定TAB・保存・書出しを加えたものです。動画の内容は確認していないため、画面構成は本仕様で独自に決めています。

---

## 0. 用語

| 用語 | 意味 |
|---|---|
| ステム | 分離後の各音源（vocals / drums / bass / guitar / piano / other）。`other` は残余成分 |
| プロジェクト | 1曲分の原音・ステム・音符・運指・編集履歴・設定をまとめたフォルダ |
| 音符（note） | 採譜結果の1音。秒単位の開始・終了、MIDI音高、信頼度などを持つ |
| 推定TAB | 音符ごとに弦・フレットを割り当てた結果。確定譜ではない |
| 手修正 | ユーザーが音高・時刻（`edited`）または弦・フレット（`fingering_edited`）を変更したこと |

---

## 1. 方針

- **ローカル完結**：Python（解析）＋ブラウザ（再生・編集）。サーバーは `127.0.0.1` のみで待ち受け、外部への送信は行わない（初回のモデル取得を除く）。
- **入力は2系統**
  1. 音声ファイル：WAV / MP3 / FLAC / M4A（ドラッグ＆ドロップ可）。他に OGG / WEBM / AAC なども PyAV が読めれば受け付ける。
  2. **ブラウザのタブ音声の録音**：YouTube 等を再生しているタブを `getDisplayMedia` で共有し、非圧縮 PCM で録音してから解析する。分離は曲全体を見るモデルのため、リアルタイム処理ではなく「録音 → 分離」の2段階とする。
- **第三者楽曲の共有・配布機能は設けない**。動画サイトからのダウンロード機能も設けない（タブ録音は利用者自身の再生音の取り込みに限る）。
- **独立モジュール**：音源分離（`bss.separation`）、ピッチ検出（`bss.transcription`）、運指決定（`bss.fingering`）をそれぞれ差し替え可能なインターフェースで実装する。

---

## 2. システム構成

```
┌──────────────── Browser (Chrome / Edge) ────────────────┐
│  UI (Vanilla ES Modules + Canvas 2D)                     │
│   波形レーン×6 / ピアノロール / TAB / ミキサー / インスペクタ │
│  Audio Engine (Web Audio)                                │
│   Stretch(12ch AudioWorklet, Signalsmith Stretch WASM)   │
│    → Splitter → Stem Gain×6 → Headroom Gain → Limiter    │
└───────────────▲──────────────────────────────────────────┘
                │ HTTP (localhost:8765)  JSON / WAV / PDF / MIDI
┌───────────────┴──────────── Python (FastAPI) ────────────┐
│  API / Project Store / Job Manager                       │
│  Worker process (python -m bss.worker)                   │
│   separation: demucs htdemucs_6s                         │
│   transcription: basic_pitch / pyin / fused              │
│   fingering: DP optimizer                                │
│  export: MIDI / CSV / PDF(reportlab)                     │
└──────────────────────────────────────────────────────────┘
```

| 層 | 採用技術 | 理由 |
|---|---|---|
| サーバー | Python 3.11, FastAPI, uvicorn | 解析ライブラリが Python |
| 音声読込 | PyAV（FFmpeg 同梱 wheel）＋ soxr | M4A/AAC も外部 ffmpeg なしで読める |
| 分離 | Demucs 4.1.0 `htdemucs_6s`（torch, CPU 既定 / CUDA 任意） | 6ステム分離の公式モデル |
| 採譜 | Basic Pitch 0.4.0（ONNX Runtime）、librosa pYIN / onset | 要件指定 |
| 伸縮再生 | Signalsmith Stretch 1.3.2（MIT, WASM/AudioWorklet） | 高品質・多チャンネル・ループ・スケジュール対応 |
| PDF | reportlab（日本語は内蔵 CID フォント HeiseiKakuGo-W5） | 追加フォント不要 |
| フロント | ビルド不要の ES Modules | Node.js 不要で起動を簡単にする |

### 2.1 ディレクトリ構成

```
bass-stem-studio/
  bss/                      Python パッケージ
    app.py                  FastAPI（API とフロント配信）
    config.py               パス・定数
    audio_io.py             デコード / 書込 / リサンプル
    project.py              プロジェクト保存・読込・履歴
    jobs.py                 ジョブ管理（子プロセス・進捗・中断・再試行）
    worker.py               子プロセスのエントリ（分離・採譜）
    peaks.py                波形ピーク / ミックスピーク計算
    notes.py                音符データ型・再解析時のマージ
    quantize.py             テンポ・拍への丸め
    separation/             分離器インターフェースと Demucs 実装
    transcription/          採譜器インターフェースと Basic Pitch / pYIN / 統合
    fingering/              チューニング・候補列挙・DP 最適化
    export/                 MIDI / CSV / PDF
  web/                      フロントエンド（静的ファイル）
    js/vendor/SignalsmithStretch.mjs
  tests/                    pytest
  tools/                    試験データ生成・評価・計測スクリプト
  docs/                     本仕様・計測結果
  workspace/                プロジェクト保存先（git 管理外）
```

---

## 3. データ仕様

### 3.1 プロジェクトフォルダ

```
workspace/projects/<project_id>/
  project.json                 メタ情報・設定（3.2）
  source/original.<ext>        読み込んだ元ファイル（そのまま）
  audio/mix.wav                44.1 kHz / stereo / PCM16 に変換した原音（全処理の時間基準）
  stems/<model>/<stem>.wav     分離結果（44.1 kHz / stereo / PCM16、全ステム同一長）
  stems/<model>/stems.json     分離メタ（モデル・実行環境・所要時間・整合チェック）
  analysis/peaks.json          波形表示用ピーク
  notes/auto-<transcriber>.json 採譜器の生出力（正規化済み）
  notes/notes.json             現在の音符（手修正込み）
  history/undo.json            取り消し／やり直しスタック（最大200手）
  history/snapshots/notes-<時刻>.json 保存ごとのスナップショット（最新50件）
  history/edits.jsonl          編集操作ログ
```

### 3.2 project.json（主要項目）

```jsonc
{
  "schema_version": 1,
  "id": "20260927-153012-a1b2c3",
  "title": "曲名",
  "source": { "filename": "song.mp3", "kind": "file|tab_capture", "sha256": "…",
              "duration_sec": 183.2, "sample_rate": 44100, "length_samples": 8079120 },
  "separation": { "separator": "demucs", "model": "htdemucs_6s", "stems": ["vocals","drums","bass","guitar","piano","other"],
                  "device": "cpu", "shifts": 1, "overlap": 0.25,
                  "env": { "demucs": "4.1.0", "torch": "2.x", "python": "3.11.2", "cpu": "…", "gpu": null },
                  "elapsed_sec": 95.2, "peak_rss_mb": 2100, "stem_gain_db": 0.0,
                  "alignment": { "length_match": true, "lag_samples": 0, "residual_db": -24.1 } },
  "transcription": { "transcriber": "fused", "params": {…}, "elapsed_sec": 21.0, "versions": {…} },
  "mixer":   { "gains_db": { "vocals": 0, "drums": 0, "guitar": 0, "piano": 0, "other": 0 },
               "mute": { "bass": false, … }, "solo": { … } },
  "playback":{ "rate": 1.0, "loop": { "enabled": false, "a": 0.0, "b": 0.0 }, "position": 0.0 },
  "tuning":  { "name": "4弦 レギュラー", "strings": [28, 33, 38, 43], "frets": 24 },
  "fingering": { "preferred_position": 3, "weights": {…} },
  "tempo":   { "bpm": null, "beats_per_bar": 4, "beat_unit": 4, "offset_sec": 0.0 },
  "quantize":{ "display": false, "export": false, "grid": "1/16" },
  "confidence_threshold": 0.5
}
```

- `tuning.strings` は **低い弦から順** の開放弦 MIDI 番号。
- ベース音量は 0 dB 固定のため `gains_db` に `bass` を持たない。

### 3.3 音符（note）

| フィールド | 型 | 説明 |
|---|---|---|
| `id` | string | 一意 ID（例 `n_000123`、手動追加は `m_…`） |
| `start_sec`, `end_sec` | float | 原曲（mix.wav）基準の秒。量子化しない値を保持 |
| `midi_pitch` | int | MIDI 番号（E1=28） |
| `confidence` | float 0–1 | 推定の確からしさ。閾値未満を低信頼として強調 |
| `source` | string | `basic_pitch` / `pyin` / `fused` / `manual` |
| `edited` | bool | 音高・時刻を手修正したか |
| `string` | int\|null | **1 = 最も高い弦**（TAB の慣例。4弦なら 1=G, 4=E）。音域外は null |
| `fret` | int\|null | フレット番号。0 = 開放 |
| `technique` | string\|null | `slide` / `hammer` / `pull` / `ghost` / `mute` 等（手入力。自動推定しない） |
| `fingering_edited` | bool | 弦・フレットを手修正したか（最適化時にロックされる） |
| `flags` | string[] | `octave_fixed` / `low_confidence` / `out_of_range` / `pitch_disagree` / `poly` など（任意） |

`notes.json` は `{ "schema_version": 1, "notes": [...], "suppressed": [...] }`。`suppressed` は **ユーザーが削除した自動音符の痕跡**（開始・音高）で、再解析時に同じ音符を復活させないために使う。

### 3.4 編集の区別

| 操作 | 変わるもの | フラグ |
|---|---|---|
| 音高変更（↑↓, Ctrl+↑↓, インスペクタ） | `midi_pitch`。弦は可能なら維持し、不可なら近傍から再選択 | `edited = true` |
| 時刻変更（ドラッグ, インスペクタ） | `start_sec` / `end_sec` | `edited = true` |
| 弦・フレット変更（Alt+↑↓, TAB ドラッグ, インスペクタ） | `string` / `fret`（**音高は不変**。候補以外は選べない） | `fingering_edited = true` |
| 追加・削除・分割・結合 | 音符の集合 | 追加は `source = manual, edited = true` |

---

## 4. 処理仕様

### 4.1 読込（`audio_io`）

1. PyAV でデコード → float32。モノラルは複製してステレオ化、3ch 以上は L/R にダウンミックス。
2. 44.1 kHz でない場合は soxr（HQ）で 44.1 kHz に変換。
3. `audio/mix.wav`（PCM16）として保存。**以後の全処理の時間基準は mix.wav の 0 サンプル目**。
4. エラー（未対応形式・壊れたファイル・長さ0・10秒未満の警告）は理由を日本語で返す。

### 4.2 音源分離（`separation`）

- インターフェース：`Separator.separate(mix: ndarray[2, N], sr, progress_cb, cancel_check) -> dict[str, ndarray[2, N]]`。`name`, `stems`, `env_info()` を持つ。登録名で切替（初期実装 `demucs:htdemucs_6s`）。
- Demucs は `apply_model(split=True, overlap=0.25, shifts=1)`。進捗はコールバックの `state=end` 回数 ÷ 総セグメント数で算出。
- デバイス：`auto`（CUDA が使えれば GPU、なければ CPU）/ `cpu` / `cuda`。
- 出力後処理：
  - 全ステムを mix と同じ長さ `N` に揃える（Demucs は同長を返すが、念のため切詰／ゼロ詰め）。
  - いずれかのステムのピークが 0 dBFS を超える場合、**全ステムに同じ係数** をかけて PCM16 に収める（係数は `stem_gain_db` に記録し、再生時の相対バランスは不変）。
  - **整合チェック**：`Σステム` と `mix` の相互相関のラグ（±2048 サンプル探索）と残差レベルを記録。ラグ ≠ 0 ならエラーではなく警告として表示。
- モデル名・Demucs/torch バージョン・デバイス・CPU/GPU 名・所要時間・子プロセスのピークメモリを `stems.json` と `project.json` に保存。

### 4.3 採譜（`transcription`）

インターフェース：`Transcriber.transcribe(bass: ndarray, sr, params, progress_cb) -> list[Note]`。登録名 `basic_pitch` / `pyin` / `fused`（既定）。

**前処理**：bass ステムをモノラル化し 22.05 kHz に変換。RMS（2048/256）から無音ゲート（上位 1% 音量から −40 dB かつ −60 dBFS 以下を無音）を作る。**無音区間には音符を作らない**。
**onset**：低域（〜2.5 kHz）の対数スペクトル差分（n_fft 1024, hop 256）をピーク検出。強さは「典型的なアタック（検出ピークの 90 パーセンタイル）」を 1 とする相対値。

**basic_pitch**：ONNX モデルで `onset_threshold=0.5, frame_threshold=0.3, minimum_note_length=58ms, 周波数 27–450 Hz, melodia_trick` として音符イベントを得て、倍音ゴースト除去・無音除去・単音化のみ行う。

**pyin**：`librosa.pyin(fmin=A0, fmax=C5, frame_length=2048, hop=256, resolution=0.25)` の f0 を半音に丸め、5 フレームのメディアン後、(a) onset 位置、(b) 3 フレーム以上続く半音変化、で区切って 60 ms 以上の区間を音符化。開始は直前の onset に引き戻す（pYIN は有声判定が遅れるため）。

**fused（既定）**：Basic Pitch を音符イベントの主候補、pYIN と onset を比較・補正に使う。
1. **倍音ゴースト除去**：低音 L と高音 H の音程が第 2〜10 倍音に相当し、H が L の内側にあり L より前に始まっていない場合、L が基音テスト（下記）を通れば H を除去。H が L の途中から始まる場合は「L の再アタック」とみなして L をそこで分割する（Basic Pitch は低音の連打を 1 音にまとめることがあるため）。L が通らなければ L を除去。
2. **オクターブ誤り抑制**：pYIN が有声 60% 以上で同じ音高なら維持。pYIN が別オクターブを示す場合は、低い方が基音テストを通れば低い方。pYIN が使えない場合は、下げるには強い証拠（比 0.35 以上）、上げるのは検出音高がテストに落ちたときだけ。基音テスト＝候補 f0 の奇数倍音（1,3,5 倍）と偶数倍音（2,4,6 倍）の振幅比（真の基音 ≥ 0.24、1 オクターブ下の偽候補 ≤ 0.13 を実測。しきい値 0.16）。
3. **分割**：pYIN が 1 半音以上・60 ms 以上変化した位置、および強い onset＋音量の再上昇（3 dB）がある位置で分割。
4. **アタック整合**：開始を近傍の onset に吸着。窓は音高で変える（E1〜G#1 ±90 ms、A1〜C#2 ±50 ms、それ以上 ±30 ms。Basic Pitch の開始時刻は極低音ほどばらつくため：実測 SD 27 ms、onset 検出は 8 ms）。
5. **断片の結合**：実装済みだが **既定では無効**（試験で本物の連打まで結合して F 値が下がったため。`merge_gap_sec` で有効化）。
6. **短音・無音除去**：50 ms 未満かつ振幅 0.4 未満、または無音ゲート内が 70% 以上の音符を除去。
7. **取りこぼし補完**：pYIN が 100 ms 以上安定して有声なのに音符がない区間を `source=pyin` として追加。
8. **単音化**：前の音が次の音の開始をまたぐ場合は次の開始で切る（開始差 30 ms 以内の同時音は `poly` フラグ）。
9. **信頼度**：`0.45·BP振幅(正規化) + 0.35·pYIN一致度 + 0.20·onset支持`。オクターブ補正した音は −0.1、同時音は ×0.8。pYIN と音高が食い違うと一致度 0（`pitch_disagree`）。

ベンド・スライド・ゴースト・コード・極端な低音は精度保証の対象外。疑わしい音は低信頼として表示する。

### 4.4 運指決定（`fingering`）

- **候補列挙**：各音高について `0 ≤ pitch − open[s] ≤ frets` を満たす (弦, フレット) を全列挙。候補なし＝音域外（`string=null`）。
- **全体最適化**：時間順に並べた音符列に対し動的計画法（Viterbi）で総コスト最小の候補列を選ぶ。
  - フレット移動：直前の押弦ポジション（開放弦はポジションを変えない）からの距離。2 フレット以内は軽く、超えた分を重く。直前との間隔が長い（休符）ほど移動コストを減衰。
  - 弦移動：弦番号差（弦飛びは線形に増加）。
  - ポジション：指定ポジション（既定 3 フレット）からの距離。
  - 高フレット：12 フレット超に軽いペナルティ。開放弦：小さいボーナス（設定可）。
  - 同時発音（開始差 30 ms 以内）：同じ弦は禁止。
  - `fingering_edited` の音符は割当を固定（候補を1つに制限）。
- **局所再選択**：音高編集時はその音符だけを前後の音符から最小コストで選び直す（全体が勝手に変わらないように）。全体の再最適化はボタンで明示的に実行。
- **チューニング変更**：候補を再計算。手修正された運指があれば「手修正を破棄して再計算／新チューニングで成立する手修正は残す／キャンセル」を確認する。
- プリセット：4弦レギュラー E1-A1-D2-G2、5弦 B0-E1-A1-D2-G2、4弦半音下げ、4弦ドロップD、6弦 B0-…-C3、カスタム（開放弦を音名で入力、フレット数 12–30）。

### 4.5 量子化（`quantize`）

- 既定は **量子化なし**（原曲の揺れを保持）。
- テンポ情報（BPM・拍子・1小節目の開始秒）を手入力すると、グリッド（1/4, 1/8, 1/16, 1/8T, 1/16T）に開始・終了を丸めて **表示** と **書出し** に個別に適用できる。保存データ自体は丸めない。
- テンポ不明でも秒ベースのロール／TAB／PDF（秒割り）は出せる。

### 4.6 書出し（`export`）

| 形式 | 内容 |
|---|---|
| MIDI | SMF Type 1、1トラック、Program 33（Electric Bass finger）。テンポ未設定時は 120 BPM を仮置きし、秒→tick を厳密換算（時刻はずれない）。オプションで弦ごとに ch1〜chN に振り分け（Guitar Pro 系での弦情報取り込み用） |
| CSV | `id,start_sec,end_sec,duration_sec,midi_pitch,note_name,confidence,source,edited,string,fret,technique,fingering_edited`（量子化時は `q_start_sec,q_end_sec` を追加） |
| PDF | A4 横。見出しに曲名・チューニング・「推定TAB（自動採譜・要確認）」。テンポ設定時は小節割り（1段4小節）、未設定時は秒割り（1段8秒）。低信頼の音は `(5)` のように括弧付き |

---

## 5. 再生エンジン（ブラウザ）

### 5.1 共通時間軸

- 6ステム（各 L/R）を **12ch の1つの伸縮ノード** に読み込む。全ステムが同一の読み出し位置・速度で伸縮されるため、速度変更後もステム間のずれは原理的に 0 サンプル。
- `AudioContext({ sampleRate: 44100 })`。ステムと同じサンプルレートにして再サンプルを避ける。
- 再生位置は伸縮ノードに与えたスケジュール（出力時刻 `t0`、入力位置 `p0`、速度 `r`、ループ `[A,B)`）から `p(t) = p0 + (t − t0)·r`（ループ折返し込み）で算出し、`t` には `AudioContext.getOutputTimestamp()` で得た **スピーカー出力中の時刻** を使う。波形・ピアノロール・TAB の再生ヘッドはすべてこの1つの関数から描画する。

### 5.2 速度・ループ

- 速度：50〜150%、10% 刻み。音程維持（Signalsmith Stretch）。再生中に変更可。
- A/B：波形レーン／ルーラーをドラッグで範囲指定、クリックでシーク。`A`/`B` キーで現在位置を設定、`L` でループ ON/OFF。ループ ON 時に再生位置が範囲外なら A へ移動。

### 5.3 ミキサーとピーク制御

- ステム行：ミュート（M）、ソロ（S）、音量（ベース以外 −30/−20/−10/0/+10 dB、ベースは 0 dB 固定）。ソロが1つでもあればソロ以外は無音。
- 音量変更は `GainNode.setTargetAtTime`（時定数 15 ms）で無停止・無クリック。
- **ピーク制御（2段）**
  1. **自動ヘッドルーム**：現在のゲイン構成で実際に加算したときのピーク（サーバーが全サンプルで計算、`/mixpeak`）が −1 dBFS を超える場合、全体を必要量だけ下げる（相対バランスは維持）。
  2. **ルックアヘッド・リミッター**（AudioWorklet、先読み 5 ms、しきい値 −1 dBFS、リリース 80 ms）：伸縮処理による瞬間的な超過や、ヘッドルーム計算前の過渡状態を抑える。先読みぶんの遅延（5 ms）は再生位置の計算で補正する。
  - UI に「ヘッドルーム −x.x dB」「リミッター作動中 −x.x dB」を表示。ブラウザのデジタル出力が ±1.0 を超えないことを保証する。

### 5.4 エフェクト（EQ / フィルター / コンプ）と再生音量（v1.1 追加）

- 対象は **マスター（全体）** と **各ステム**。各ステムは `ステムゲイン → ステム FX` を通って合算され、`自動ヘッドルーム → マスター FX → リミッター → 再生音量 → 出力` となる。
- FX チェーン：HPF → LOW（ローシェルフ）→ LO-MID / MID / HI-MID / PRES（ピーキング）→ HIGH（ハイシェルフ）→ LPF → コンプ → メイクアップ → 出力トリム。すべてネイティブの Web Audio ノード。
- EQ のゲインは ±15 dB、**5 dB 刻み**（Shift で 0.5 dB）。グラフの目盛りも 5 dB ごと。フィルターは 12 / 24 dB/oct（24 は 2 段の Butterworth）。
- 無効なフィルター・バンドは「0 dB のピーキング」（数学的に恒等）に切り替えるので、ON/OFF でチェーンの遅延が変わらず、ステム間のずれも生じない。
- コンプ（DynamicsCompressorNode）には Chromium で固定の先読み遅延（実測 6.0 ms）があり、ステム＋マスターの 2 段で 12 ms。起動時に OfflineAudioContext で実測し、再生位置の計算で補正する。
- 画面：EQ は対数周波数（20 Hz–20 kHz）× dB のグラフに、対象のリアルタイムスペクトル（FFT 8192）・バンドごとの特性・合成特性（`getFrequencyResponse` による実際のフィルター特性）を重ね、点のドラッグで周波数とゲイン、ホイールで Q / スロープを変える。コンプは入出力の伝達カーブ（しきい値・レシオ・ニー・メイクアップを反映）に、実測した入力ピークと実際の GR から求めた動作点を重ねる。アタック / リリースは試験バースト（0.5 秒）への応答として図示し、GR メーターと 4 秒の履歴を表示。
- EQ でブーストした分のピークはリミッターが受け止め、作動量をピーク制御表示に出す（自動ヘッドルームはステムゲインのみを対象に計算）。
- 設定はプロジェクトの `fx`（対象ごと）に保存。再生音量は視聴者ごとの好みなのでブラウザ（localStorage）に保存。

### 5.5 診断ログ（v1.2 追加）

- **サーバーログ**：`workspace/logs/app.log`（2 MB × 6 世代でローテーション）。
- **出来事ログ**：`workspace/logs/events.jsonl`（取り込み・取り込み拒否・ジョブ開始/終了/失敗・内部エラー・ファイル診断・音符の保存競合・サーバー起動）。サーバー内部エラーは例外ハンドラで捕捉してスタックトレースごと記録し、画面には要約を返す。ログ記録の失敗は本来の処理を止めない。
- **ブラウザのエラー**：未捕捉例外・Promise の拒否・console.error / warn・画面に出したエラーメッセージを 5 秒ごと（エラー時は即時）に `/api/diag/client` へ送り `client.jsonl` に保存。曲 ID と再生エンジンの状態を添える。
- **ジョブ履歴**：終了時に `workspace/jobs/<id>.result.json` として保存（サーバー再起動後も参照可）。ワーカーの標準エラーは `<id>.log`。
- **動作チェック**：PC 側（作業フォルダ書込、空き容量、メモリ、各形式の読込の往復試験、torch、分離モデル取得済みか、Basic Pitch、pYIN、PDF フォント）とブラウザ側（AudioWorklet、WebAssembly、44.1 kHz、getOutputTimestamp、タブ録音、WAV デコード、メモリ上限、保存領域）。
- **ファイル診断**：取り込まずに調べる。先頭バイトによる形式判定と拡張子の不一致、WAV のチャンク構造と LIST/INFO タグの文字コード（UTF-8 / Shift-JIS 判定）、FFmpeg の標準設定で開けるか（タグの文字コードで失敗する旧不具合の検出）、ストリーム情報、soundfile での情報、アプリの読込処理での実デコード（ピーク・クリップ率・無音）を行い、所見（ok / info / warn / error）を日本語で返す。
- **診断レポート**：上記と環境情報・動作チェック結果・（任意で）開いている曲の設定・音符・元ファイル診断をまとめた ZIP。音声は含めない。

---

## 6. 画面仕様

```
┌ ヘッダ：プロジェクト選択｜ファイルを開く｜タブ録音｜保存｜書出し｜設定 ┐
├ トランスポート：▶/⏸ ■｜時刻｜速度｜A/B・ループ｜ピーク制御表示        ┤
├ 全体波形（ミニマップ・表示範囲枠）                                   ┤
├ ルーラー（秒 / テンポ設定時は小節）                                   ┤
├ ステムレーン×6：名前 M S 音量｜波形（A/B 範囲・再生ヘッド）            ┤
├ ピアノロール（ベース）：鍵盤ラベル｜音符（信頼度で色分け）             ┤
├ 推定TAB：弦名｜フレット番号＋長さバー                                ┤
├ インスペクタ：選択音符の開始/終了/音高/弦/フレット/奏法/信頼度/出典     ┤
└ ジョブ表示：段階・進捗・中断・再試行・エラー理由                       ┘
```

- 全レーンは同じ横スクロール・ズーム（Ctrl+ホイール）。再生中は「追従」ON で表示範囲が自動で進む。
- ピアノロール操作：クリック＝選択＋その位置から試聴、ドラッグ＝移動（縦で音高）、端ドラッグ＝長さ、ダブルクリック（空き）＝追加、Delete＝削除、↑↓＝半音、Ctrl+↑↓＝オクターブ、`S`＝再生位置で分割、`M`＝選択音符の結合、Ctrl+Z / Ctrl+Y。
- TAB 操作：クリック＝選択（ピアノロールと共有）、上下ドラッグ／Alt+↑↓＝同じ音高のまま弦変更。
- 低信頼の音符はオレンジ系＋斜線、手修正済みは白枠、運指ロックは TAB 上で下線。
- 保存：Ctrl+S。未保存で離れようとすると警告。

---

## 7. API（抜粋）

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/api/system` | バージョン・デバイス・モデルキャッシュ状況 |
| GET/POST | `/api/projects` | 一覧 / 新規（multipart。`auto=1` で分離→採譜を自動開始） |
| GET/DELETE | `/api/projects/{id}` | 取得 / 削除 |
| PATCH | `/api/projects/{id}` | 設定の部分更新（ミキサー・再生・チューニング・テンポ等） |
| GET | `/api/projects/{id}/stems/{stem}.wav` | ステム音声 |
| GET | `/api/projects/{id}/peaks` | 波形ピーク |
| GET | `/api/projects/{id}/mixpeak?g=…` | 指定ゲインでの加算ピーク |
| GET/PUT | `/api/projects/{id}/notes` | 音符（PUT で保存・スナップショット・undo スタック） |
| POST | `/api/projects/{id}/jobs` | `separate` / `transcribe` / `pipeline` の開始 |
| GET | `/api/jobs/{job_id}` | 状態・進捗・エラー |
| POST | `/api/jobs/{job_id}/cancel`, `/retry` | 中断・再試行 |
| POST | `/api/fingering/optimize` | 運指の全体最適化（ロック尊重） |
| GET | `/api/projects/{id}/export/{midi|csv|pdf}` | 書出し |

---

## 8. ジョブ管理

- 重い処理は **子プロセス**（`python -m bss.worker`）で実行し、JSON Lines で進捗を返す。UI は 0.5 秒ごとにポーリング。
- 中断：子プロセスを終了。途中生成物は一時フォルダに書き、完了時に置換するので中断しても既存結果は壊れない。
- 再試行：同じパラメータで再投入。失敗理由（例外の要約）と詳細ログを保持。
- 同時実行は1ジョブ（CPU を奪い合わないため）。後続はキュー待ち。
- **再解析しても手修正は上書きしない**：`edited` / `fingering_edited` の音符を保持し、それらと重なる新しい自動音符と `suppressed` に一致する自動音符は追加しない。未編集の自動音符だけを入れ替える。

---

## 9. 試験・計測

| 項目 | 方法 | 自動化 |
|---|---|---|
| 読込 | WAV/MP3/FLAC/M4A を生成して読込み、長さ・SR を確認 | pytest |
| 分離 | 約3分の試験曲で 6 ファイル生成、長さ一致・ラグ 0 を確認。所要時間・ピークメモリ・モデル取得サイズを記録 | `tools/bench_separation.py` |
| ミキサー | 各段のゲインが dB→線形で設定されること、+10 dB＋ソロ切替で出力ピーク ≤ 1.0 | ブラウザ内計測ページ |
| 再生同期 | 既知位置にクリックを置いたステムを 50/100/150% とループで再生し、出力で検出したクリック時刻と表示位置の差を計測（目標 100 ms 未満） | ブラウザ内計測ページ |
| 採譜 | **ベース単独** と **混合曲** を別々に評価：音符 P/R/F（onset ±50 ms・±50 cent）、onset 誤差、音高正解率、見落とし・余計、TAB 弦フレット一致率 | `tools/evaluate.py` |
| 運指 | 既知フレーズで妥当な運指、ロック尊重、チューニング変更 | pytest |
| 保存 | 保存→再読込で音符・運指・設定・undo が一致、書出し内容が編集後と一致 | pytest |

- 試験データ：`tools/make_testset.py` が正解 MIDI/TAB 付きの合成ベース（単独）と、合成ドラム・ギター・鍵盤・声・パッドを混ぜた混合曲を生成する。合成音は実録音より容易なので、**合成での数値は上限の目安** として扱い、実録音での評価は利用者が用意した正解データで `tools/evaluate.py` を実行して行う。
- 採譜精度の合格閾値は対象曲と正解データを選んだ後に決める。最初の実用判定は「単音主体の 8〜16 小節を手直しできる水準」。

---

## 10. 開発段階

| 段階 | 内容 | 完了条件 |
|---|---|---|
| 1 | 読込・6分離・共通時間軸・ミキサー・ピーク制御・ループ・速度・タブ録音 | 短い曲で同期と音割れを検証 |
| 2 | ベース採譜（3方式）・ピアノロール・編集・undo | 合成ベース単独で時刻・音高・休符を検証 |
| 3 | チューニング・運指最適化・TAB 表示/編集・保存・MIDI/CSV/PDF | 混合曲で再検証、保存→再起動で復元 |

---

## 11. 既知の制約・リスク

- htdemucs_6s のピアノ分離は公式にも品質の留保がある。ギター/ピアノ/その他の境界は曲によって混ざる。
- 伸縮ノードは 12ch を常時処理する（100% 速度でも STFT を通る）。音質は高いが原音とビット一致ではない。
- ブラウザ内メモリ：ステム全体を float32 で保持するため 1 分あたり約 127 MB（3 分で約 380 MB）。
- タブ録音は Chrome / Edge のみ（「タブの音声も共有」を ON にする必要あり）。録音は実時間かかる。
- GTX 1060（Pascal）で GPU を使う場合は、Pascal をサポートする CUDA 版 torch（cu126 等）が必要。既定は CPU。

---

## 12. 実装・計測で確定した事項（2026-09-27）

- 実測値と残る誤認識例は [MEASUREMENTS.md](MEASUREMENTS.md) を参照。受入基準の「分離」「ミキサー」「再生」「編集・保存」は合成データと自動計測で確認済み。「音符・TAB」は合成データで計測し、実録音の正解データでの評価はツール（`tools/evaluate.py`）を用意した段階。
- 合成ベースは加算合成（エレキベースに近いスペクトル）を使う。Karplus-Strong の撥弦音は Demucs が「ベース」と判定せず（分離 SDR 0 dB）、ミックス評価が成り立たなかった。
- 採譜方式の比較：クリーンなベース単独では pyin が最良（F=1.0）だが、ミックス分離後は fused が最良（F 0.70–0.81）。本アプリの主用途はミックスなので既定は fused とし、設定で切り替え可能にした。
- Basic Pitch 0.4.0 は Windows + Python 3.11 で TensorFlow を要求するメタデータになっているため、`--no-deps` で入れて ONNX Runtime で動かす（setup.ps1）。
- Demucs 4.1.0 はモデルを Hugging Face（`adefossez/HTDemucs-6s`）から取得し、音声入出力に torchaudio を使わない。本アプリの入出力は PyAV / soundfile で行う。
