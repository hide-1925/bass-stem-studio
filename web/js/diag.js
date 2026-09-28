// Browser-side diagnostics: capture errors (uncaught, promise rejections, console.error/warn and
// user-visible error messages) and send them to the local server log; browser capability checks.

const MAX_BUFFER = 200;

export class ErrorCapture {
  constructor(contextFn) {
    this.contextFn = contextFn;
    this.buffer = [];
    this.recent = [];
    this.origError = console.error.bind(console);
    this.origWarn = console.warn.bind(console);
    window.addEventListener('error', (e) => {
      // resource load errors (img/script 404) have no message
      if (!e.message && e.target && e.target !== window) {
        this.push('error', `resource failed: ${e.target.src || e.target.href || e.target.tagName}`);
        return;
      }
      this.push('error', e.message, { src: e.filename, line: e.lineno, col: e.colno, stack: e.error && e.error.stack });
    }, true);
    window.addEventListener('unhandledrejection', (e) => {
      const r = e.reason;
      this.push('error', r && r.message ? r.message : String(r), { stack: r && r.stack, kind: 'unhandledrejection' });
    });
    console.error = (...args) => {
      this.origError(...args);
      this.push('error', args.map(fmt).join(' '), { kind: 'console' });
    };
    console.warn = (...args) => {
      this.origWarn(...args);
      this.push('warn', args.map(fmt).join(' '), { kind: 'console' });
    };
    setInterval(() => this.flush(), 5000);
    window.addEventListener('pagehide', () => this.flush(true));
  }

  push(level, message, extra = {}) {
    const entry = { t: new Date().toISOString(), level, message: String(message || '').slice(0, 2000), ...extra };
    this.recent.push(entry);
    if (this.recent.length > MAX_BUFFER) this.recent.shift();
    this.buffer.push(entry);
    if (this.buffer.length > MAX_BUFFER) this.buffer.shift();
    if (level === 'error') setTimeout(() => this.flush(), 500);
  }

  async flush(beacon = false) {
    if (!this.buffer.length) return;
    const entries = this.buffer.splice(0);
    let context = {};
    try { context = this.contextFn(); } catch (_) { /* ignore */ }
    const body = JSON.stringify({ entries, context });
    try {
      if (beacon && navigator.sendBeacon) {
        navigator.sendBeacon('/api/diag/client', new Blob([body], { type: 'application/json' }));
        return;
      }
      const res = await fetch('/api/diag/client', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
      if (!res.ok) throw new Error(res.status);
    } catch (_) {
      this.buffer.unshift(...entries.slice(-50)); // server unreachable: keep the latest ones
    }
  }
}

function fmt(a) {
  if (a instanceof Error) return `${a.name}: ${a.message}`;
  if (typeof a === 'object') {
    try { return JSON.stringify(a).slice(0, 500); } catch (_) { return String(a); }
  }
  return String(a);
}

export function clientInfo(engine) {
  const ctx = engine && engine.ctx;
  return {
    userAgent: navigator.userAgent,
    language: navigator.language,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemoryGB: navigator.deviceMemory,
    screen: `${screen.width}x${screen.height}@${devicePixelRatio}`,
    viewport: `${innerWidth}x${innerHeight}`,
    jsHeapMB: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1e6) : undefined,
    audio: ctx ? {
      state: ctx.state, sampleRate: ctx.sampleRate, baseLatency: ctx.baseLatency, outputLatency: ctx.outputLatency,
      stretchLatency: engine.stretchLatency, fxLatencyMs: +(engine.fxLatency * 1000).toFixed(2), playing: engine.playing,
      stemsLoaded: engine.stemNames, durationSec: engine.duration,
    } : null,
  };
}

// Capability checks that only the browser can answer.
export async function clientChecks(engine) {
  const out = [];
  const add = (label, status, detail) => out.push({ label, status, detail, side: 'browser' });
  const ua = navigator.userAgent;
  const chromium = /Chrome\/|Edg\//.test(ua);
  add('ブラウザ', chromium ? 'ok' : 'warn', chromium ? ua.match(/(Edg|Chrome)\/[\d.]+/)[0] : `${ua}（Chrome / Edge を推奨）`);
  add('AudioWorklet', window.AudioWorkletNode ? 'ok' : 'fail', window.AudioWorkletNode ? '利用可' : '再生エンジンが動きません');
  add('WebAssembly', typeof WebAssembly === 'object' ? 'ok' : 'fail', typeof WebAssembly === 'object' ? '利用可' : '伸縮処理が動きません');
  try {
    const ctx = engine && engine.ctx ? engine.ctx : new AudioContext({ sampleRate: 44100 });
    const ok = ctx.sampleRate === 44100;
    add('AudioContext 44.1 kHz', ok ? 'ok' : 'warn', `sampleRate ${ctx.sampleRate} / 状態 ${ctx.state} / 出力遅延 ${ctx.outputLatency ?? '?'} s`);
    if (!(engine && engine.ctx)) ctx.close();
  } catch (e) {
    add('AudioContext 44.1 kHz', 'fail', e.message);
  }
  add('getOutputTimestamp（再生位置の補正）', AudioContext.prototype.getOutputTimestamp ? 'ok' : 'warn',
    AudioContext.prototype.getOutputTimestamp ? '利用可' : '無いと表示が数十 ms ずれることがあります');
  add('タブの音の録音', navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia ? 'ok' : 'warn',
    navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia ? '利用可（共有ダイアログで「タブの音声も共有」を ON）' : 'このブラウザでは使えません');
  try {
    // decode a tiny generated WAV the same way the stems are decoded
    const n = 441;
    const buf = new ArrayBuffer(44 + n * 4);
    const v = new DataView(buf);
    const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    w(0, 'RIFF'); v.setUint32(4, 36 + n * 4, true); w(8, 'WAVE'); w(12, 'fmt '); v.setUint32(16, 16, true);
    v.setUint16(20, 1, true); v.setUint16(22, 2, true); v.setUint32(24, 44100, true); v.setUint32(28, 44100 * 4, true);
    v.setUint16(32, 4, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 4, true);
    const oc = new OfflineAudioContext(2, n, 44100);
    const ab = await oc.decodeAudioData(buf);
    add('WAV のデコード', ab.length === n ? 'ok' : 'warn', `${ab.length} サンプル`);
  } catch (e) {
    add('WAV のデコード', 'fail', e.message);
  }
  if (performance.memory) {
    const lim = performance.memory.jsHeapSizeLimit / 1e9;
    add('ブラウザのメモリ上限', lim > 2 ? 'ok' : 'warn', `JS ヒープ上限 ${lim.toFixed(1)} GB（ステムは 1 分あたり約 130 MB を AudioWorklet 側で使用）`);
  }
  let storage = 'ok';
  try { localStorage.setItem('bss:probe', '1'); localStorage.removeItem('bss:probe'); } catch (_) { storage = 'warn'; }
  add('ブラウザの保存領域', storage, storage === 'ok' ? '利用可（音量・最後に開いた曲を記憶）' : '使えません（プライベートモード？）');
  return out;
}
