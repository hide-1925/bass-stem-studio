// Sync / clipping measurement with the real playback engine (see measure.html).
//
// Sync: every stem has identical clicks at known input times. A tap on the stretch output
// detects each click per stem (inter-stem spread), and a tap after the limiter gives the time
// the click actually leaves the graph. The "display error" is engine.position()'s mapping
// evaluated at that moment minus the true click time (converted to wall-clock ms).
// Clipping: loud stems at +10 dB; the limiter output peak must stay <= -1 dBFS.

import { AudioEngine } from './audio/engine.js';

const SR = 44100;
const DUR = 24;
const CLICKS = [];
for (let t = 1.0; t < DUR - 1; t += 0.75) CLICKS.push(+t.toFixed(3));
const NAMES = ['vocals', 'drums', 'bass', 'guitar', 'piano', 'other'];
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function wavUrl(ch) {
  const n = ch.length;
  const buf = new ArrayBuffer(44 + n * 4);
  const v = new DataView(buf);
  const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, 'RIFF'); v.setUint32(4, 36 + n * 4, true); w(8, 'WAVE'); w(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 2, true); v.setUint32(24, SR, true);
  v.setUint32(28, SR * 4, true); v.setUint16(32, 4, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 4, true);
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, ch[i])) * 32767;
    v.setInt16(44 + i * 4, s, true);
    v.setInt16(46 + i * 4, s, true);
  }
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
}

function makeStems(bed) {
  return NAMES.map((name, k) => {
    const x = new Float32Array(DUR * SR);
    if (bed) for (let i = 0; i < x.length; i++) x[i] = 0.35 * Math.sin(2 * Math.PI * (55 + 10 * k) * i / SR);
    const len = Math.round(0.006 * SR);
    for (const t of CLICKS) {
      const i0 = Math.round(t * SR);
      for (let i = 0; i < len; i++) x[i0 + i] += 0.8 * Math.sin(2 * Math.PI * 1500 * i / SR) * Math.sin(Math.PI * i / len);
    }
    return { name, label: name, url: wavUrl(x) };
  });
}

const stats = (a) => {
  if (!a.length) return { n: 0 };
  const abs = a.map(Math.abs);
  return { n: a.length, mean: a.reduce((s, x) => s + x, 0) / a.length, maxAbs: Math.max(...abs) };
};

async function run() {
  $('run').disabled = true;
  const eng = new AudioEngine();
  const log = [];
  const say = (m) => { $('state').textContent = m; log.push(m); };
  say('ステム生成中…');
  await eng.init();
  await eng.load(makeStems(false), (i, n) => say(`読み込み ${i}/${n}`));
  await eng.ctx.audioWorklet.addModule(new URL('./audio/tap-worklet.js', import.meta.url));
  const tapStems = new AudioWorkletNode(eng.ctx, 'bss-tap', {
    numberOfInputs: 1, numberOfOutputs: 0, channelCount: 12, channelCountMode: 'explicit', channelInterpretation: 'discrete',
    processorOptions: { threshold: 0.25, pairs: 6 },
  });
  const tapOut = new AudioWorkletNode(eng.ctx, 'bss-tap', {
    numberOfInputs: 1, numberOfOutputs: 0, channelCount: 2, channelCountMode: 'explicit', channelInterpretation: 'discrete',
    processorOptions: { threshold: 0.12, pairs: 1 },
  });
  eng.stretch.connect(tapStems);
  eng.limiter.connect(tapOut); // after both FX chains and the limiter
  let stemHits = [];
  let outHits = [];
  tapStems.port.onmessage = (e) => e.data.pair !== undefined && stemHits.push(e.data);
  // Evaluate the displayed position right when the click leaves the graph (live segment list).
  tapOut.port.onmessage = (e) => {
    if (e.data.pair === undefined) return;
    const t = e.data.time - eng.outputDelay;
    outHits.push({ ...e.data, shown: eng.inputAt(t), rate: eng._segAt(t).rate });
  };
  for (const n of NAMES) eng.setStemGain(n, 1 / 6);
  await eng.resume();

  const conditions = [
    { label: '100%', rate: 1.0, start: 0.6, secs: 6 },
    { label: '50%', rate: 0.5, start: 0.6, secs: 7 },
    { label: '150%', rate: 1.5, start: 0.6, secs: 6 },
    { label: '100% ループ 2.0–5.0s', rate: 1.0, start: 1.8, secs: 8, loop: [2.0, 5.0] },
    { label: '70% ループ 2.0–4.0s', rate: 0.7, start: 1.8, secs: 8, loop: [2.0, 4.0] },
    { label: '100%→130% 再生中に変更', rate: 1.0, start: 0.6, secs: 7, change: { at: 2.5, rate: 1.3 } },
  ];
  const rows = [];
  const detail = [];
  window.measureDetail = detail;
  for (const c of conditions) {
    say(`計測中: ${c.label}`);
    eng.setRate(c.rate);
    eng.setLoop(!!c.loop, c.loop ? c.loop[0] : 0, c.loop ? c.loop[1] : 0);
    stemHits = [];
    outHits = [];
    await eng.play(c.start);
    const tStart = eng.ctx.currentTime;
    if (c.change) {
      await sleep(c.change.at * 1000);
      eng.setRate(c.change.rate);
    }
    await sleep((c.secs - (c.change ? c.change.at : 0)) * 1000);
    const tStop = eng.ctx.currentTime; // audio after this is the stop transition, not playback
    eng.pause();
    await sleep(400);
    // skip the first 0.4 s after (re)start and the transition around a live rate change
    const valid = (t) => t > tStart + eng.ahead + 0.4 && t < tStop && (!c.change || Math.abs(t - (tStart + c.change.at + eng.ahead)) > 0.5);
    // inter-stem spread: group detections by click
    const groups = new Map();
    for (const h of stemHits.filter((h) => valid(h.time))) {
      const key = Math.round(h.time * 10);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(h);
    }
    const spreads = [...groups.values()].filter((g) => g.length === 6).map((g) => (Math.max(...g.map((h) => h.time)) - Math.min(...g.map((h) => h.time))) * 1000);
    // display error: mapping at the heard moment vs. true click time
    const errs = [];
    let boundary = 0;
    const perClick = [];
    for (const h of outHits.filter((h) => valid(h.time))) {
      const nearest = CLICKS.reduce((b, t) => (Math.abs(t - h.shown) < Math.abs(b - h.shown) ? t : b), CLICKS[0]);
      // A click sitting exactly on the loop end is partly played before the jump (the stretcher reads
      // a short window past B), while the display has already wrapped to A: count it separately.
      const err = ((h.shown - nearest) / h.rate) * 1000;
      const clickOnB = c.loop && CLICKS.some((t) => Math.abs(t - c.loop[1]) < 0.03);
      if (clickOnB && h.shown - c.loop[0] >= 0 && h.shown - c.loop[0] < 0.4 && Math.abs(err) > 100) {
        boundary++;
        continue;
      }
      errs.push(err);
      perClick.push([+(h.time - tStart).toFixed(3), +err.toFixed(1)]);
    }
    const s = stats(errs);
    detail.push({ label: c.label, errs: perClick, boundary });
    rows.push({ label: c.label, clicks: groups.size, complete: spreads.length, spreadMax: spreads.length ? Math.max(...spreads) : NaN,
      errMean: s.mean, errMax: s.maxAbs, n: s.n, boundary });
  }

  // clipping test
  say('計測中: +10 dB 音割れ試験');
  eng.stop();
  await eng.load(makeStems(true));
  eng.stretch.connect(tapStems);
  const tapPre = new AudioWorkletNode(eng.ctx, 'bss-tap', {
    numberOfInputs: 1, numberOfOutputs: 0, channelCount: 2, channelCountMode: 'explicit', channelInterpretation: 'discrete',
    processorOptions: { threshold: 10, pairs: 1 },
  });
  const tapPost = new AudioWorkletNode(eng.ctx, 'bss-tap', {
    numberOfInputs: 1, numberOfOutputs: 0, channelCount: 2, channelCountMode: 'explicit', channelInterpretation: 'discrete',
    processorOptions: { threshold: 10, pairs: 1 },
  });
  eng.headroomNode.connect(tapPre);
  eng.limiter.connect(tapPost);
  const peakOf = (node) => new Promise((r) => { node.port.onmessage = (e) => e.data.peak !== undefined && r(e.data.peak); node.port.postMessage('peak'); });
  eng.setHeadroom(1);
  for (const n of NAMES) eng.setStemGain(n, n === 'bass' ? 1 : Math.pow(10, 10 / 20));
  eng.setRate(1);
  eng.setLoop(false, 0, 0);
  await peakOf(tapPre); await peakOf(tapPost);
  await eng.play(0.5);
  // toggle mute / solo during playback (must not interrupt audio)
  for (let k = 0; k < 8; k++) {
    await sleep(500);
    eng.setStemGain(NAMES[k % 6], k % 2 ? Math.pow(10, 10 / 20) : 0);
  }
  const pre = await peakOf(tapPre);
  const post = await peakOf(tapPost);
  eng.pause();
  const db = (x) => (20 * Math.log10(Math.max(x, 1e-9))).toFixed(2);

  const fmt = (x) => (isFinite(x) ? x.toFixed(1) : '–');
  const ok = (x, lim) => (isFinite(x) && Math.abs(x) < lim ? 'ok' : 'ng');
  $('out').innerHTML = `
    <h3>同期（目標: 100 ms 未満）</h3>
    <table><tr><th>条件</th><th>クリック数</th><th>6ステム揃い</th><th>ステム間ずれ 最大 [ms]</th><th>表示誤差 平均 [ms]</th><th>表示誤差 最大 [ms]</th><th>ループ境界上のクリック（除外）</th></tr>
    ${rows.map((r) => `<tr><td style="text-align:left">${r.label}</td><td>${r.clicks}</td><td>${r.complete}</td>
      <td class="${ok(r.spreadMax, 100)}">${fmt(r.spreadMax)}</td><td>${fmt(r.errMean)}</td><td class="${ok(r.errMax, 100)}">${fmt(r.errMax)}</td><td>${r.boundary || 0}</td></tr>`).join('')}
    </table>
    <h3>音割れ（+10 dB × 5 ステム + 大音量の低音ベッド）</h3>
    <table><tr><th>リミッター前ピーク</th><th>リミッター後ピーク</th><th>判定（≤ −1 dBFS）</th></tr>
    <tr><td>${db(pre)} dBFS</td><td>${db(post)} dBFS</td><td class="${post <= 0.8913 + 1e-4 ? 'ok' : 'ng'}">${post <= 0.8913 + 1e-4 ? 'クリップなし' : 'クリップ'}</td></tr></table>
    <p class="dim">FX（コンプ）遅延補正 ${(eng.fxLatency * 1000).toFixed(1)} ms, stretch latency ${eng.stretchLatency?.toFixed(3)} s, schedule ahead ${eng.ahead.toFixed(3)} s, outputLatency ${eng.ctx.outputLatency ?? '–'} s, baseLatency ${eng.ctx.baseLatency} s</p>`;
  window.measureResult = { rows, clip: { preDb: +db(pre), postDb: +db(post) }, latency: eng.stretchLatency };
  say('完了');
}

$('run').addEventListener('click', () => run().catch((e) => { $('state').textContent = `エラー: ${e.message}`; console.error(e); }));
