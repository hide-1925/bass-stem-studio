// Side panel: exact values of the selected note(s). Pitch edits and string/fret edits are
// separate controls (and separate undo entries), as required by the spec.

import { candidates } from '../notes/fingering.js';
import { el, fmtTime, noteName } from '../util.js';

const SOURCE_LABEL = { basic_pitch: 'Basic Pitch', pyin: 'pYIN', fused: 'Basic Pitch + pYIN/onset 補正', manual: '手入力' };
const FLAG_LABEL = {
  octave_fixed: 'オクターブ補正済み', pitch_disagree: 'pYIN と音高不一致', poly: '同時発音（保証外）',
  out_of_range: '音域外（弾けない）', split_pitch: '音高変化で分割', split_reattack: '再アタックで分割', pyin_only: 'pYIN のみで検出',
};
const TECHNIQUES = [['', 'なし'], ['slide', 'スライド'], ['hammer', 'ハンマリング'], ['pull', 'プリング'], ['ghost', 'ゴースト'], ['mute', 'ミュート'], ['bend', 'ベンド'], ['vibrato', 'ビブラート']];

export class Inspector {
  constructor(root, app) {
    this.root = root;
    this.app = app;
  }

  render() {
    const app = this.app;
    const ids = [...app.selection].filter((id) => app.notes.get(id));
    this.root.replaceChildren();
    if (!app.notes.size && !app.project?.transcription) {
      this.root.append(el('p', { class: 'hint' }, '採譜が完了すると、ここで選択した音符の値を編集できます。'));
      return;
    }
    if (ids.length === 0) {
      this.root.append(
        el('h3', {}, '音符'),
        el('p', { class: 'hint' }, `全 ${app.notes.size} 音 / 低信頼 ${app.notes.sorted().filter((n) => n.confidence < (app.project.confidence_threshold ?? 0.5)).length} 音`),
        el('ul', { class: 'keys' },
          el('li', {}, el('kbd', {}, 'クリック'), ' 選択＋そこから再生'),
          el('li', {}, el('kbd', {}, '↑ ↓'), ' 半音（音高の修正）'),
          el('li', {}, el('kbd', {}, 'Ctrl+↑ ↓'), ' オクターブ'),
          el('li', {}, el('kbd', {}, 'Alt+↑ ↓'), ' 同じ音高で弦を変更'),
          el('li', {}, el('kbd', {}, '← →'), ' 前後の音符'),
          el('li', {}, el('kbd', {}, 'Del'), ' 削除 ', el('kbd', {}, 'S'), ' 分割 ', el('kbd', {}, 'M'), ' 結合'),
          el('li', {}, el('kbd', {}, 'ダブルクリック'), ' 音符を追加'),
          el('li', {}, el('kbd', {}, 'Ctrl+Z / Y'), ' 取り消し / やり直し'),
          el('li', {}, el('kbd', {}, 'Space'), ' 再生 ', el('kbd', {}, 'A'), el('kbd', {}, 'B'), ' ループ点 ', el('kbd', {}, 'L'), ' ループ'),
        ));
      return;
    }
    if (ids.length > 1) {
      const notes = ids.map((id) => app.notes.get(id));
      const s = Math.min(...notes.map((n) => n.start_sec));
      const e = Math.max(...notes.map((n) => n.end_sec));
      this.root.append(
        el('h3', {}, `${ids.length} 音を選択`),
        el('p', { class: 'mono' }, `${fmtTime(s)} – ${fmtTime(e)}`),
        el('div', { class: 'row-btns' },
          el('button', { onclick: () => app.pitchSelected(-1) }, '♭ 半音下'),
          el('button', { onclick: () => app.pitchSelected(1) }, '♯ 半音上'),
          el('button', { onclick: () => app.notes.merge(ids) }, '結合'),
          el('button', { class: 'danger', onclick: () => app.deleteSelected() }, '削除')),
        el('button', { onclick: () => app.setLoop(true, s, e, true) }, 'この範囲をループ'));
      return;
    }
    const n = app.notes.get(ids[0]);
    const tun = app.tuning;
    const num = (value, onchange, step = 0.001) => {
      const i = el('input', { type: 'number', step, value: value.toFixed(3) });
      i.addEventListener('change', () => onchange(Number(i.value)));
      return i;
    };
    const lo = Math.min(...tun.strings) - 5;
    const hi = Math.max(...tun.strings) + tun.frets + 2;
    const pitchSel = el('select', {}, ...Array.from({ length: hi - lo + 1 }, (_, k) => lo + k).map((p) =>
      el('option', { value: p, selected: p === n.midi_pitch }, `${noteName(p)}  (${p})`)));
    pitchSel.addEventListener('change', () => app.notes.setPitch([n.id], () => Number(pitchSel.value), tun));
    const cands = candidates(n.midi_pitch, tun);
    const labels = [...tun.strings].reverse().map(noteName);
    const stringSel = el('select', { disabled: !cands.length },
      ...cands.map((c) => el('option', { value: c.string, selected: c.string === n.string }, `${c.string}弦 (${labels[c.string - 1]}) – ${c.fret}F`)));
    stringSel.addEventListener('change', () => app.notes.setString(n.id, Number(stringSel.value), tun));
    const techSel = el('select', {}, ...TECHNIQUES.map(([v, l]) => el('option', { value: v, selected: (n.technique || '') === v }, l)));
    techSel.addEventListener('change', () => app.notes.setFields(n.id, { technique: techSel.value || null }, '奏法'));
    const lock = el('input', { type: 'checkbox', checked: n.fingering_edited });
    lock.addEventListener('change', () => app.notes.setFields(n.id, { fingering_edited: lock.checked }, lock.checked ? '運指を固定' : '運指の固定を解除'));
    const conf = Math.round(n.confidence * 100);
    const low = n.confidence < (app.project.confidence_threshold ?? 0.5);
    this.root.append(
      el('h3', {}, `${noteName(n.midi_pitch)}`, el('span', { class: `badge ${low ? 'warn' : ''}` }, `信頼度 ${conf}%`)),
      el('div', { class: 'field' }, el('label', {}, '開始 (秒)'), num(n.start_sec, (v) => app.notes.moveResize([{ id: n.id, start_sec: Math.min(v, n.end_sec - 0.01), end_sec: n.end_sec }], '時刻変更'))),
      el('div', { class: 'field' }, el('label', {}, '終了 (秒)'), num(n.end_sec, (v) => app.notes.moveResize([{ id: n.id, start_sec: n.start_sec, end_sec: Math.max(v, n.start_sec + 0.01) }], '時刻変更'))),
      el('div', { class: 'field' }, el('label', {}, '音高'), pitchSel),
      el('div', { class: 'sub' }, '↑ 音そのものを変える修正'),
      el('div', { class: 'field' }, el('label', {}, '弦・フレット'), cands.length ? stringSel : el('span', { class: 'warn-text' }, 'このチューニングでは弾けません')),
      el('div', { class: 'sub' }, '↑ 音高は同じまま運指だけ変える修正'),
      el('div', { class: 'field' }, el('label', {}, '運指を固定'), lock),
      el('div', { class: 'field' }, el('label', {}, '奏法'), techSel),
      el('dl', { class: 'meta' },
        el('dt', {}, '検出元'), el('dd', {}, SOURCE_LABEL[n.source] || n.source),
        el('dt', {}, '状態'), el('dd', {}, [n.edited ? '音高/時刻を手修正' : '自動', n.fingering_edited ? '運指手修正' : ''].filter(Boolean).join('・')),
        ...(n.flags && n.flags.length ? [el('dt', {}, '注意'), el('dd', {}, n.flags.map((f) => FLAG_LABEL[f] || f).join('、'))] : [])),
      el('div', { class: 'row-btns' },
        el('button', { onclick: () => app.audition(n) }, '▶ ここから再生'),
        el('button', { onclick: () => app.setLoop(true, n.start_sec, n.end_sec + 0.25, true) }, 'この音をループ'),
        el('button', { class: 'danger', onclick: () => app.deleteSelected() }, '削除')),
    );
  }
}
