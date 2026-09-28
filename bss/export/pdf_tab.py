"""Printable bass TAB (A4 landscape) with reportlab.

With a tempo: bars laid out 4 per system. Without: 8 seconds per system.
Low-confidence notes are printed in parentheses; the document is labeled as an estimated TAB.
"""

from __future__ import annotations

import datetime as dt
import io
import math

from reportlab.lib.colors import Color, black
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from ..notes import note_name
from ..quantize import bar_seconds, tempo_valid

JP_FONT = "BSSJapanese"
# Embedded (subset) TrueType fonts render in every viewer; the CID fallback needs the viewer's
# Japanese font pack.
FONT_CANDIDATES = [("C:/Windows/Fonts/BIZ-UDGothicR.ttc", 0), ("C:/Windows/Fonts/meiryo.ttc", 0),
                   ("C:/Windows/Fonts/msgothic.ttc", 0), ("C:/Windows/Fonts/YuGothR.ttc", 0),
                   ("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", 0),
                   ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0)]
_font_ready = False

TECH_MARK = {"slide": "/", "hammer": "h", "pull": "p", "ghost": "( )", "mute": "x", "bend": "b", "vibrato": "~"}
GRAY = Color(0.55, 0.55, 0.55)
LIGHT = Color(0.8, 0.8, 0.8)
WARN = Color(0.75, 0.2, 0.1)


def _ensure_font():
    global _font_ready, JP_FONT
    if _font_ready:
        return
    from pathlib import Path

    from reportlab.pdfbase.ttfonts import TTFont

    for path, idx in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont("BSSJapanese", path, subfontIndex=idx))
                JP_FONT = "BSSJapanese"
                _font_ready = True
                return
            except Exception:  # noqa: BLE001 - try the next candidate
                continue
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    JP_FONT = "HeiseiKakuGo-W5"
    _font_ready = True


def _auto_layout(notes: list[dict], usable_w: float, seg_dur: float, use_bars: bool,
                 bars_per_system: int | None, seconds_per_system: float | None) -> int:
    """Segments per system so that close notes stay ~14 pt apart (10th percentile spacing)."""
    starts = sorted(n["start_sec"] for n in notes)
    iois = [b - a for a, b in zip(starts, starts[1:]) if b - a > 0.02]
    close = sorted(iois)[len(iois) // 4] if iois else seg_dur  # 25th percentile spacing
    fit = usable_w * close / 12.0  # seconds that fit on one system
    if use_bars:
        if bars_per_system:
            return bars_per_system
        return int(max(2, min(4, fit // seg_dur)))
    if seconds_per_system:
        return int(seconds_per_system)
    return int(max(4, min(12, fit)))


def to_pdf_bytes(notes: list[dict], tuning: dict, tempo: dict | None, title: str, quantized: bool,
                 grid: str | None, confidence_threshold: float = 0.5, bars_per_system: int | None = None,
                 seconds_per_system: float | None = None) -> bytes:
    _ensure_font()
    buf = io.BytesIO()
    W, H = landscape(A4)
    c = canvas.Canvas(buf, pagesize=(W, H))
    c.setTitle(f"{title} - 推定TAB")
    margin = 36.0
    label_w = 22.0
    strings = list(tuning["strings"])  # low -> high
    n_str = len(strings)
    labels = [note_name(p) for p in reversed(strings)]  # string 1 (top line) first
    line_gap = 10.0
    staff_h = line_gap * (n_str - 1)
    block_h = staff_h + 52.0

    use_bars = tempo_valid(tempo)
    if use_bars:
        seg_dur = bar_seconds(tempo)
        origin = float(tempo.get("offset_sec", 0.0))
    else:
        seg_dur = 1.0
        origin = 0.0
    per_system = _auto_layout(notes, W - 2 * margin - label_w, seg_dur, use_bars, bars_per_system, seconds_per_system)
    last_t = max((n["start_sec"] for n in notes), default=0.0) + 1e-3
    first_t = min((n["start_sec"] for n in notes), default=0.0)
    first_seg = min(0, math.floor((first_t - origin) / seg_dur))
    last_seg = max(first_seg, math.ceil((last_t - origin) / seg_dur) - 1)
    segments = list(range(first_seg, last_seg + 1))
    systems = [segments[i:i + per_system] for i in range(0, len(segments), per_system)] or [[0]]

    page = 1

    def header(first_page: bool) -> float:
        y = H - margin
        if first_page:
            c.setFont(JP_FONT, 15)
            c.drawString(margin, y - 12, title or "Untitled")
            c.setFont(JP_FONT, 9)
            tun = " ".join(labels[::-1])
            info = [f"推定TAB（自動採譜・要確認）  チューニング: {tun} / {tuning.get('frets', 24)}F"]
            if use_bars:
                info.append(f"テンポ: {float(tempo['bpm']):g} BPM  {tempo.get('beats_per_bar', 4)}/{tempo.get('beat_unit', 4)}"
                            f"  1小節目: {origin:.3f}s")
            else:
                info.append("テンポ未設定のため秒単位で配置（1段 = %d 秒）" % per_system)
            info.append(f"量子化: {grid if quantized else 'なし（原曲の揺れを保持）'}   "
                        f"( ) = 低信頼（{confidence_threshold:.2f} 未満）   出力: {dt.datetime.now():%Y-%m-%d %H:%M}")
            for k, line in enumerate(info):
                c.drawString(margin, y - 28 - 12 * k, line)
            return y - 28 - 12 * len(info) - 10
        return y - 10

    def footer():
        c.setFont(JP_FONT, 8)
        c.setFillColor(GRAY)
        c.drawRightString(W - margin, margin / 2, f"{title}  —  p.{page}  —  Bass Stem Studio")
        c.setFillColor(black)

    y = header(True)
    usable_w = W - 2 * margin - label_w
    for sys_segs in systems:
        if y - block_h < margin:
            footer()
            c.showPage()
            page += 1
            y = header(False)
        top = y - 18  # room for bar numbers / technique marks
        seg_w = usable_w / per_system
        x0 = margin + label_w
        # staff lines + string labels
        c.setLineWidth(0.5)
        c.setStrokeColor(black)
        c.setFont("Helvetica", 7)
        for k in range(n_str):
            ly = top - k * line_gap
            c.line(x0, ly, x0 + seg_w * len(sys_segs), ly)
            c.drawRightString(x0 - 4, ly - 2.5, labels[k])
        # segment (bar / second) lines
        for m, seg in enumerate(sys_segs + [None]):
            sx = x0 + m * seg_w
            c.setStrokeColor(black if use_bars else LIGHT)
            c.setLineWidth(0.8 if use_bars else 0.4)
            c.line(sx, top, sx, top - staff_h)
            if seg is not None:
                c.setFont("Helvetica", 6.5)
                c.setFillColor(GRAY)
                seg_start = origin + seg * seg_dur
                if use_bars:
                    c.drawString(sx + 2, top + 5, str(seg + 1) if seg >= 0 else "")
                    # beat ticks under the staff
                    beats = int(tempo.get("beats_per_bar", 4))
                    for b in range(beats):
                        bx = sx + seg_w * b / beats
                        c.setStrokeColor(LIGHT)
                        c.line(bx, top - staff_h - 3, bx, top - staff_h - 6)
                elif seg % max(1, per_system // 4) == 0:
                    c.drawString(sx + 2, top - staff_h - 12, f"{int(seg_start // 60)}:{seg_start % 60:04.1f}")
                c.setFillColor(black)
        # notes
        sys_start = origin + sys_segs[0] * seg_dur
        sys_end = origin + (sys_segs[-1] + 1) * seg_dur
        pad = 4.0

        def tx(t: float) -> float:
            k = (t - sys_start) / seg_dur
            m = min(int(k), len(sys_segs) - 1)
            frac = k - m
            return x0 + m * seg_w + pad + frac * (seg_w - 2 * pad)

        for n in notes:
            if not (sys_start <= n["start_sec"] < sys_end):
                continue
            x = tx(n["start_sec"])
            x_end = tx(min(n["end_sec"], sys_end - 1e-6))
            low = float(n.get("confidence", 1.0)) < confidence_threshold
            if n.get("string") is None or n.get("fret") is None:
                c.setFillColor(WARN)
                c.setFont("Helvetica", 7)
                c.drawCentredString(x, top + 5, note_name(n["midi_pitch"]) + "?")
                c.setFillColor(black)
                continue
            ly = top - (int(n["string"]) - 1) * line_gap
            text = str(n["fret"])
            if low:
                text = f"({text})"
            c.setFont("Helvetica-Bold" if n.get("fingering_edited") else "Helvetica", 8)
            tw = pdfmetrics.stringWidth(text, "Helvetica", 8)
            c.setFillColorRGB(1, 1, 1)
            c.rect(x - tw / 2 - 1, ly - 3.6, tw + 2, 7.4, stroke=0, fill=1)
            c.setFillColor(GRAY if low else black)
            c.drawCentredString(x, ly - 2.8, text)
            c.setFillColor(black)
            # duration bar under the staff
            by = top - staff_h - 9
            c.setStrokeColor(GRAY)
            c.setLineWidth(0.6)
            c.line(x, by, max(x + 1.5, x_end), by)
            tech = n.get("technique")
            if tech:
                c.setFont("Helvetica", 6.5)
                c.drawCentredString(x, top + 5 if not use_bars else top + 12, TECH_MARK.get(tech, tech[:3]))
        y -= block_h
    footer()
    c.save()
    return buf.getvalue()

