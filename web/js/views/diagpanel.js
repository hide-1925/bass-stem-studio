// Diagnostics dialog: self-check, file probe, logs, job history and a report download.

import { clientChecks, clientInfo } from '../diag.js';
import { el, fmtTime } from '../util.js';

const TABS = [['check', '動作チェック'], ['probe', 'ファイル診断'], ['logs', 'エラー・ログ'], ['jobs', 'ジョブ履歴'], ['report', 'レポート書き出し']];
const ICON = { ok: '✓', info: 'i', warn: '!', fail: '×', error: '×' };

async function getJson(url, opts) {
  const res = await fetch(url, opts);
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(j.detail || `${res.status}`);
  return j;
}

export class DiagPanel {
  constructor(app, capture) {
    this.app = app;
    this.capture = capture;
  }

  open(tab = 'check', opts = {}) {
    this.body = el('div', { class: 'diag-body' });
    this.tabBar = el('div', { class: 'diag-tabs' }, ...TABS.map(([id, label]) => {
      const b = el('button', { dataset: { tab: id } }, label);
      b.addEventListener('click', () => this.show(id));
      return b;
    }));
    const close = this.app.modal('診断', el('div', {}, this.tabBar, this.body),
      [el('button', { onclick: () => close() }, '閉じる')]);
    document.getElementById('modal').classList.add('wide');
    const back = document.getElementById('modal-back');
    const obs = new MutationObserver(() => {
      if (back.hidden) { document.getElementById('modal').classList.remove('wide'); obs.disconnect(); }
    });
    obs.observe(back, { attributes: true });
    this.show(tab, opts);
  }

  show(tab, opts = {}) {
    this.tab = tab;
    for (const b of this.tabBar.children) b.classList.toggle('sel', b.dataset.tab === tab);
    this.body.replaceChildren(el('p', { class: 'dim' }, '読み込み中…'));
    const fn = { check: () => this.renderCheck(), probe: () => this.renderProbe(opts), logs: () => this.renderLogs(),
      jobs: () => this.renderJobs(), report: () => this.renderReport() }[tab];
    fn().catch((e) => this.body.replaceChildren(el('p', { class: 'warn-text' }, `取得できませんでした: ${e.message}`)));
  }

  // ---- self-check ----------------------------------------------------------------------
  async renderCheck() {
    const run = el('button', { class: 'primary' }, 'チェックを実行');
    const out = el('div');
    const sysBox = el('div', { class: 'diag-sys' });
    run.addEventListener('click', async () => {
      run.disabled = true;
      out.replaceChildren(el('p', { class: 'dim' }, '実行中…（数秒かかります）'));
      try {
        const [server, browser, sys] = await Promise.all([getJson('/api/diag/selfcheck'), clientChecks(this.app.engine), getJson('/api/diag/system')]);
        const rows = [...server.checks.map((c) => ({ ...c, side: 'PC' })), ...browser];
        const bad = rows.filter((r) => r.status === 'fail').length;
        const warn = rows.filter((r) => r.status === 'warn').length;
        out.replaceChildren(
          el('p', { class: `verdict ${bad ? 'fail' : warn ? 'warn' : 'ok'}` }, bad ? `問題が ${bad} 件あります` : warn ? `注意が ${warn} 件あります` : 'すべて正常です'),
          table(['', '項目', '結果', '場所', '時間'], rows.map((r) => [
            el('span', { class: `st ${r.status}` }, ICON[r.status] || r.status), r.label, r.detail, r.side || '', r.ms !== undefined ? `${r.ms} ms` : ''])));
        sysBox.replaceChildren(el('h4', {}, '環境'), kv({
          'アプリ': sys.app, 'OS': sys.platform, 'CPU': `${sys.cpu}（${sys.cpu_count} スレッド）`,
          'メモリ': sys.memory_gb ? `${sys.memory_gb.total} GB（空き ${sys.memory_gb.available} GB）` : '–',
          'ディスク': sys.disk_gb ? `空き ${sys.disk_gb.free} GB` : '–',
          'torch / GPU': sys.torch ? `${sys.torch.version} / ${sys.torch.gpu || 'CPU'}` : '–',
          'FFmpeg (libavformat)': sys.ffmpeg ? sys.ffmpeg.libavformat : '–', 'libsndfile': sys.libsndfile || '–',
          '分離モデル': sys.model_cache_mb ? `取得済み ${sys.model_cache_mb} MB` : '未取得', '作業フォルダ': sys.workspace,
          'プロジェクト数': sys.projects,
        }));
      } catch (e) {
        out.replaceChildren(el('p', { class: 'warn-text' }, e.message));
      } finally {
        run.disabled = false;
      }
    });
    this.body.replaceChildren(el('p', { class: 'dim' }, 'この PC とブラウザで、読込・分離・採譜・再生に必要なものが揃っているかを確認します。'), run, out, sysBox);
    run.click();
  }

  // ---- file probe ----------------------------------------------------------------------
  async renderProbe(opts = {}) {
    const input = el('input', { type: 'file', accept: 'audio/*,.wav,.mp3,.flac,.m4a,.aac,.ogg,.opus,.webm,.aif,.aiff' });
    const out = el('div');
    const runFile = async (file) => {
      out.replaceChildren(el('p', { class: 'dim' }, `${file.name} を診断中…`));
      const fd = new FormData();
      fd.append('file', file, file.name);
      try {
        out.replaceChildren(renderProbe(await getJson('/api/diag/probe', { method: 'POST', body: fd })));
      } catch (e) {
        out.replaceChildren(el('p', { class: 'warn-text' }, e.message));
      }
    };
    input.addEventListener('change', () => input.files[0] && runFile(input.files[0]));
    const p = this.app.project;
    const projBtn = p ? el('button', {}, `開いている曲の元ファイルを診断（${p.source?.filename || p.title}）`) : null;
    const runProject = async () => {
      out.replaceChildren(el('p', { class: 'dim' }, '診断中…'));
      try {
        out.replaceChildren(renderProbe(await getJson(`/api/projects/${p.id}/probe`)));
      } catch (e) {
        out.replaceChildren(el('p', { class: 'warn-text' }, e.message));
      }
    };
    if (projBtn) projBtn.addEventListener('click', runProject);
    this.body.replaceChildren(
      el('p', { class: 'dim' }, '開けない・おかしいファイルの中身（形式・コーデック・タグの文字コード・破損・無音など）を調べます。取り込みはしません。'),
      el('div', { class: 'inline' }, input, projBtn), out);
    if (opts.project && p) runProject();
  }

  // ---- logs ----------------------------------------------------------------------------
  async renderLogs() {
    const src = el('select', {}, el('option', { value: 'all' }, 'まとめ（サーバー＋ブラウザ）'), el('option', { value: 'events' }, 'サーバーの出来事'),
      el('option', { value: 'client' }, 'ブラウザのエラー'), el('option', { value: 'app' }, 'サーバーログ（app.log）'));
    const onlyErr = el('input', { type: 'checkbox' });
    const out = el('div', { class: 'diag-log' });
    const load = async () => {
      out.replaceChildren(el('p', { class: 'dim' }, '読み込み中…'));
      await this.capture.flush();
      if (src.value === 'app') {
        const j = await getJson('/api/diag/logs?source=app&n=500');
        const lines = onlyErr.checked ? j.lines.filter((l) => /ERROR|WARNING|Traceback|Error/.test(l)) : j.lines;
        out.replaceChildren(el('pre', { class: 'log' }, lines.join('\n') || '（ログなし）'));
        return;
      }
      const lists = [];
      if (src.value !== 'client') lists.push(...(await getJson('/api/diag/logs?source=events&n=400')).entries.map((e) => ({ ...e, from: 'サーバー' })));
      if (src.value !== 'events') lists.push(...(await getJson('/api/diag/logs?source=client&n=400')).entries.map((e) => ({ ...e, kind: e.kind || 'browser', from: 'ブラウザ' })));
      let rows = lists.sort((a, b) => String(b.t).localeCompare(String(a.t)));
      if (onlyErr.checked) rows = rows.filter((r) => r.level === 'error' || r.level === 'warn');
      rows = rows.slice(0, 300);
      out.replaceChildren(rows.length ? el('div', {}, ...rows.map(logRow)) : el('p', { class: 'dim' }, '記録はありません。'));
    };
    src.addEventListener('change', load);
    onlyErr.addEventListener('change', load);
    this.body.replaceChildren(el('div', { class: 'inline' }, src, el('label', { class: 'inline' }, onlyErr, 'エラー・警告のみ'),
      el('button', { onclick: load }, '更新')), out);
    await load();
  }

  // ---- jobs ----------------------------------------------------------------------------
  async renderJobs() {
    const j = await getJson('/api/diag/jobs?limit=50');
    const titles = {};
    try {
      for (const p of await getJson('/api/projects')) titles[p.id] = p.title;
    } catch (_) { /* ignore */ }
    const stageJa = { prepare: '読込', separate: '分離', transcribe: '採譜' };
    const rows = j.jobs.map((job) => {
      const detail = el('button', { class: 'small' }, 'ログ');
      detail.addEventListener('click', () => {
        const pre = el('pre', { class: 'log' }, [job.error ? `エラー: ${job.error}\n\n` : '', job.error_detail || '', '\n--- ワーカーログ（末尾）---\n', (job.log_tail || []).join('\n')].join(''));
        detail.replaceWith(pre);
      });
      const st = { done: 'ok', error: 'fail', cancelled: 'warn', running: 'info', queued: 'info' }[job.status] || 'info';
      return [
        new Date((job.created_at || 0) * 1000).toLocaleString(),
        titles[job.project_id] || job.project_id,
        { pipeline: '全体', prepare: '読込', separate: '分離', transcribe: '採譜' }[job.type] || job.type,
        el('span', { class: `st ${st}` }, job.status),
        Object.entries(job.stage_times || {}).map(([k, v]) => `${stageJa[k] || k} ${v}s`).join(' / '),
        job.error ? el('span', { class: 'warn-text' }, job.error) : '',
        job.error_detail || (job.log_tail && job.log_tail.length) ? detail : '',
      ];
    });
    this.body.replaceChildren(el('p', { class: 'dim' }, '分離・採譜の実行履歴（サーバーを再起動しても残ります）。'),
      rows.length ? table(['開始', '曲', '種類', '結果', '所要時間', 'エラー', ''], rows) : el('p', { class: 'dim' }, '履歴はまだありません。'));
  }

  // ---- report --------------------------------------------------------------------------
  async renderReport() {
    const incl = el('input', { type: 'checkbox', checked: !!this.app.project });
    const btn = el('button', { class: 'primary' }, '診断レポートをダウンロード（ZIP）');
    const msg = el('p', { class: 'dim' });
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      msg.textContent = '作成中…（動作チェックを含むため数秒かかります）';
      try {
        await this.capture.flush();
        const res = await fetch('/api/diag/report', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_id: incl.checked && this.app.project ? this.app.project.id : null, client: clientInfo(this.app.engine) }),
        });
        if (!res.ok) throw new Error(`${res.status}`);
        const blob = await res.blob();
        const name = (res.headers.get('content-disposition') || '').match(/filename="([^"]+)"/)?.[1] || 'bss-diagnostics.zip';
        const a = el('a', { href: URL.createObjectURL(blob), download: name });
        document.body.append(a);
        a.click();
        a.remove();
        msg.textContent = `${name}（${(blob.size / 1024).toFixed(0)} KB）を保存しました。`;
      } catch (e) {
        msg.textContent = `作成に失敗: ${e.message}`;
      } finally {
        btn.disabled = false;
      }
    });
    this.body.replaceChildren(
      el('p', {}, '不具合を相談するときに渡せる ZIP を作ります。'),
      el('ul', { class: 'dim' },
        el('li', {}, '含むもの：環境情報、動作チェック結果、サーバーログ、ブラウザのエラー、分離・採譜ジョブのログ、（選んだ場合）開いている曲の設定・音符・元ファイルの診断結果'),
        el('li', {}, '含まないもの：音声ファイル（元の曲・ステム）'),
        el('li', {}, '注意：ファイル名・フォルダのパス・曲名が含まれます。送る前に中身を確認してください。')),
      el('label', { class: 'inline' }, incl, `開いている曲の情報を含める${this.app.project ? `（${this.app.project.title}）` : '（曲が開かれていません）'}`),
      el('div', { style: 'margin-top:10px' }, btn), msg);
  }
}

// ---- rendering helpers -------------------------------------------------------------------
function table(head, rows) {
  return el('table', { class: 'plist diag-table' },
    el('tr', {}, ...head.map((h) => el('th', {}, h))),
    ...rows.map((r) => el('tr', {}, ...r.map((c) => el('td', {}, c ?? '')))));
}

function kv(obj) {
  return el('dl', { class: 'diag-kv' }, ...Object.entries(obj).flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, String(v ?? '–'))]));
}

function logRow(e) {
  const level = e.level || 'info';
  const msg = e.message || e.error || [e.kind, e.filename, e.status, e.type].filter(Boolean).join(' ') || '';
  const extra = { ...e };
  for (const k of ['t', 'level', 'from', 'message', 'received']) delete extra[k];
  const details = el('details', {}, el('summary', {},
    el('span', { class: `st ${level === 'error' ? 'fail' : level}` }, ICON[level === 'error' ? 'fail' : level] || 'i'),
    el('span', { class: 'mono dim' }, ` ${String(e.t || '').replace('T', ' ').slice(0, 19)} `),
    el('b', {}, `[${e.from}] ${e.kind || ''} `), msg.slice(0, 200)),
  el('pre', { class: 'log' }, JSON.stringify(extra, null, 1)));
  return details;
}

function renderProbe(r) {
  const f = r.file;
  const ff = r.ffmpeg || {};
  const a = (ff.streams || []).find((s) => s.type === 'audio') || {};
  const verdictText = { ok: '読み込めます', warn: '読み込めますが注意点があります', error: '読み込めません' }[r.verdict];
  const box = el('div', {},
    el('p', { class: `verdict ${r.verdict === 'error' ? 'fail' : r.verdict}` }, `${f.name}：${verdictText}`),
    el('ul', { class: 'findings' }, ...r.findings.map((x) => el('li', { class: x.level }, el('span', { class: `st ${x.level === 'error' ? 'fail' : x.level}` }, ICON[x.level] || 'i'), ' ', x.message))),
    el('h4', {}, '詳細'),
    kv({
      'サイズ': `${f.size_mb} MB`, '中身の形式': f.detected, '先頭バイト': f.magic_hex,
      'コンテナ': ff.format_long || ff.format || '–', '長さ': ff.duration_sec ? fmtTime(ff.duration_sec) : '–',
      'コーデック': a.codec || '–', 'サンプルレート': a.sample_rate ? `${a.sample_rate} Hz` : '–', 'チャンネル': a.channels ?? '–',
      'ビットレート': ff.bit_rate ? `${Math.round(ff.bit_rate / 1000)} kbps` : '–',
      'FFmpeg（標準設定）': ff.strict_open || '–',
      'soundfile': r.soundfile && r.soundfile.ok ? `${r.soundfile.format} ${r.soundfile.subtype}` : (r.soundfile && r.soundfile.error) || '–',
      'アプリでの読込': r.decode && r.decode.ok ? `OK（${r.decode.decode_ms} ms、ピーク ${r.decode.peak_dbfs} dBFS）` : `失敗: ${r.decode && r.decode.error}`,
    }));
  if (r.wav) {
    box.append(el('h4', {}, 'WAV の構造'), kv({
      'チャンク': r.wav.chunks.map((c) => `${c.id.trim()}(${c.size})`).join(' '),
      'fmt': r.wav.fmt ? `${r.wav.fmt.format} ${r.wav.fmt.channels}ch ${r.wav.fmt.sample_rate}Hz ${r.wav.fmt.bits}bit` : '–',
      'タグ': r.wav.info_tags.length ? r.wav.info_tags.map((t) => `${t.id}=${t.text}（${t.encoding}）`).join(' / ') : 'なし',
    }));
  }
  box.append(el('details', {}, el('summary', {}, '生データ（JSON）'), el('pre', { class: 'log' }, JSON.stringify(r, null, 1))));
  return box;
}
