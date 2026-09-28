"""Bass tunings. ``strings`` are open-string MIDI pitches from the LOWEST string to the highest.

TAB string numbers follow the usual convention: string 1 = highest-pitched string.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..notes import note_name, parse_note_name


@dataclass(frozen=True)
class Tuning:
    strings: tuple[int, ...]  # low -> high
    frets: int = 24
    name: str = ""

    def __post_init__(self):
        if not 1 <= len(self.strings) <= 8:
            raise ValueError("弦の数は1〜8本にしてください。")
        if not 1 <= self.frets <= 36:
            raise ValueError("フレット数は1〜36にしてください。")

    @property
    def n(self) -> int:
        return len(self.strings)

    def string_number(self, idx_low: int) -> int:
        """Index from low (0-based) -> TAB string number (1 = highest)."""
        return self.n - idx_low

    def index_low(self, string_number: int) -> int:
        return self.n - string_number

    def open_pitch(self, string_number: int) -> int:
        return self.strings[self.index_low(string_number)]

    @property
    def lowest(self) -> int:
        return min(self.strings)

    @property
    def highest(self) -> int:
        return max(self.strings) + self.frets

    def labels(self) -> list[str]:
        """Names from string 1 (highest) to string n (lowest)."""
        return [note_name(p) for p in reversed(self.strings)]

    def to_dict(self) -> dict:
        return {"name": self.name, "strings": list(self.strings), "frets": self.frets}


def _t(name: str, notes: str, frets: int = 24) -> dict:
    return {"name": name, "strings": [parse_note_name(x) for x in notes.split()], "frets": frets}


PRESETS: list[dict] = [
    _t("4弦 レギュラー (E A D G)", "E1 A1 D2 G2"),
    _t("5弦 レギュラー (B E A D G)", "B0 E1 A1 D2 G2"),
    _t("4弦 半音下げ (Eb Ab Db Gb)", "D#1 G#1 C#2 F#2"),
    _t("4弦 ドロップD (D A D G)", "D1 A1 D2 G2"),
    _t("4弦 全音下げ (D G C F)", "D1 G1 C2 F2"),
    _t("5弦 ハイC (E A D G C)", "E1 A1 D2 G2 C3"),
    _t("6弦 (B E A D G C)", "B0 E1 A1 D2 G2 C3"),
]


def tuning_from_dict(d: dict | None) -> Tuning:
    d = d or PRESETS[0]
    strings = d.get("strings")
    if not strings:
        raise ValueError("チューニングの弦が指定されていません。")
    parsed = tuple(parse_note_name(s) if isinstance(s, str) else int(s) for s in strings)
    if list(parsed) != sorted(parsed):
        raise ValueError("弦は低い方から順に指定してください。")
    return Tuning(strings=parsed, frets=int(d.get("frets", 24)), name=str(d.get("name", "")))
