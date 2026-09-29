"""Whole-song string/fret optimization (Viterbi over all playable positions of every note).

Cost model (lower is better):
  * hand shift   : distance from the current hand anchor (fret of the last fretted note;
                   open strings leave the anchor where it was). Cheap within ``span`` frets,
                   expensive beyond. Relaxed after rests, since there is time to move.
  * string cross : number of strings crossed from the previous note.
  * position     : distance from the preferred position (fret), or a slight low-fret bias.
  * high frets   : small penalty above the 12th fret.
  * open strings : ``open_cost`` (0 = neutral, negative = prefer open strings).
  * chords       : simultaneous notes (start within ``chord_window_sec``) may not share a string.
Notes with ``fingering_edited`` are pinned to their string when ``respect_locks`` is set.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from .tuning import Tuning

INF = float("inf")


@dataclass
class FingeringOptions:
    preferred_position: float | None = 3.0
    span: int = 3
    w_shift: float = 1.0
    w_stretch: float = 0.15
    w_string: float = 0.35
    w_position: float = 0.08
    w_high: float = 0.15
    open_cost: float = 0.1
    rest_relax_sec: float = 0.5
    chord_window_sec: float = 0.03

    @classmethod
    def from_dict(cls, d: dict | None) -> "FingeringOptions":
        d = d or {}
        names = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in d.items() if k in names}
        if "preferred_position" in kwargs and kwargs["preferred_position"] in ("", None):
            kwargs["preferred_position"] = None
        return cls(**kwargs)

    def to_dict(self) -> dict:
        return asdict(self)


def candidates(pitch: int, tuning: Tuning) -> list[tuple[int, int]]:
    """All playable (string_number, fret) pairs; string 1 = highest string."""
    out = []
    for idx_low, open_pitch in enumerate(tuning.strings):
        fret = int(pitch) - open_pitch
        if 0 <= fret <= tuning.frets:
            out.append((tuning.string_number(idx_low), fret))
    return out


def _node_cost(fret: int, o: FingeringOptions) -> float:
    if fret == 0:
        return o.open_cost
    c = 0.0
    if o.preferred_position is None:
        c += 0.02 * fret
    else:
        c += o.w_position * abs(fret - float(o.preferred_position))
    c += o.w_high * max(0, fret - 12)
    return c


SAME_STRING_TECHNIQUES = {"slide", "slide_shift", "hammer", "pull"}  # reached from the previous note on its string
SLIDES = {"slide", "slide_shift"}
TECHNIQUE_PENALTY = 4.0


def _transition(prev: dict, cur: dict, pc: tuple[int, int], anchor: int | None, cc: tuple[int, int],
                o: FingeringOptions) -> float:
    ps, pf = pc
    cs, cf = cc
    simultaneous = abs(cur["start_sec"] - prev["start_sec"]) <= o.chord_window_sec
    if simultaneous and ps == cs:
        return INF
    gap = max(0.0, cur["start_sec"] - prev["end_sec"])
    relax = 1.0 / (1.0 + gap / o.rest_relax_sec)
    move = 0.0
    if cf > 0 and anchor is not None:
        d = abs(cf - anchor)
        move = o.w_stretch * min(d, o.span) + o.w_shift * max(0, d - o.span)
    string_move = o.w_string * abs(cs - ps)
    penalty = 0.0
    tech = cur.get("technique")
    if tech == "slide_out_down" and cf <= 2:
        penalty += TECHNIQUE_PENALTY * 0.5  # no room to slide down from the first frets
    if tech in SAME_STRING_TECHNIQUES:
        # h/p and slides only exist on one string; a slide moves the hand by itself and cannot
        # start or end on an open string
        if ps != cs:
            penalty += TECHNIQUE_PENALTY
        elif tech in SLIDES:
            move = 0.0
            if cf == 0 or pf == 0:
                penalty += TECHNIQUE_PENALTY * 0.75
    return relax * move + (0.3 + 0.7 * relax) * string_move + penalty


def optimize(notes: list[dict], tuning: Tuning, options: FingeringOptions | None = None,
             respect_locks: bool = True) -> dict:
    """Return ``{"assignments": {id: [string, fret]}, "unplayable": [...], "dropped_locks": [...], "cost": float}``."""
    o = options or FingeringOptions()
    order = sorted(range(len(notes)), key=lambda i: (notes[i]["start_sec"], notes[i]["midi_pitch"]))
    seq: list[tuple[dict, list[tuple[int, int]]]] = []
    unplayable: list[str] = []
    dropped: list[str] = []
    for i in order:
        n = notes[i]
        cands = candidates(n["midi_pitch"], tuning)
        if not cands:
            unplayable.append(n["id"])
            continue
        if respect_locks and n.get("fingering_edited") and n.get("string") is not None:
            pinned = [c for c in cands if c[0] == int(n["string"])]
            if pinned:
                cands = pinned
            else:
                dropped.append(n["id"])
        seq.append((n, cands))

    if not seq:
        return {"assignments": {}, "unplayable": unplayable, "dropped_locks": dropped, "cost": 0.0}

    first_note, first_cands = seq[0]
    cost = [_node_cost(f, o) for _, f in first_cands]
    anchor: list[int | None] = [f if f > 0 else None for _, f in first_cands]
    back: list[list[int]] = []
    for k in range(1, len(seq)):
        prev_note, prev_cands = seq[k - 1]
        cur_note, cur_cands = seq[k]
        new_cost, new_anchor, bp = [], [], []
        for cc in cur_cands:
            best, best_i = INF, 0
            for i, pc in enumerate(prev_cands):
                if cost[i] == INF:
                    continue
                t = _transition(prev_note, cur_note, pc, anchor[i], cc, o)
                if cost[i] + t < best:
                    best, best_i = cost[i] + t, i
            if best == INF:
                # Only impossible when every option collides with a simultaneous note on the
                # same string; fall back to ignoring the collision rather than failing.
                best_i = min(range(len(prev_cands)), key=lambda i: cost[i])
                best = cost[best_i] + 5.0
            new_cost.append(best + _node_cost(cc[1], o))
            new_anchor.append(cc[1] if cc[1] > 0 else anchor[best_i])
            bp.append(best_i)
        cost, anchor = new_cost, new_anchor
        back.append(bp)

    j = min(range(len(cost)), key=lambda i: cost[i])
    total = cost[j]
    picks = [j]
    for bp in reversed(back):
        j = bp[j]
        picks.append(j)
    picks.reverse()
    assignments = {n["id"]: list(cands[p]) for (n, cands), p in zip(seq, picks)}
    return {"assignments": assignments, "unplayable": unplayable, "dropped_locks": dropped, "cost": round(total, 4)}


def apply_assignments(notes: list[dict], result: dict, clear_dropped_locks: bool = True) -> list[dict]:
    """Write the optimizer result into the notes (in place) and return them."""
    assignments = result["assignments"]
    unplayable = set(result["unplayable"])
    dropped = set(result["dropped_locks"])
    for n in notes:
        flags = set(n.get("flags") or [])
        if n["id"] in assignments:
            n["string"], n["fret"] = assignments[n["id"]]
            flags.discard("out_of_range")
        elif n["id"] in unplayable:
            n["string"], n["fret"] = None, None
            flags.add("out_of_range")
        if n["id"] in dropped and clear_dropped_locks:
            n["fingering_edited"] = False
        n["flags"] = sorted(flags)
    return notes
