from __future__ import annotations

import unicodedata

#: Every byte that can start a multi-byte UTF-8 character.
_ALL_LEAD_BYTES = bytes(range(0xC2, 0xF5))

#: The lead bytes whose Latin-1 reading is itself evidence of damage: Latin-1 symbols
#: (C2), accented Latin (C3), Cyrillic (D0, D1), Arabic (D8, D9), general punctuation and
#: mathematics (E2) and emoji (F0). Deliberately not widened to every lead byte, because
#: as single characters "é" and "à" are ordinary French, not damage.
_LATIN1_MARKERS = tuple(bytes((0xC2, 0xC3, 0xD0, 0xD1, 0xD8, 0xD9, 0xE2, 0xF0)).decode("latin1"))

#: Read as Windows-1251, the same lead bytes become ordinary Cyrillic letters, so a
#: single one proves nothing — "В", "Г" and "Р" begin real Russian words. What proves it
#: is the *pair*: a lead letter followed by the Cyrillic-1251 reading of a UTF-8
#: continuation byte. Both halves are derived from the byte ranges rather than typed out,
#: so the vocabulary cannot cover one punctuation mark and miss its neighbour. A template
#: reading "5.4 nano В· low" was exactly this: a middle dot, saved one decode too late.
#: 0x98 has no Windows-1251 character, so it is skipped rather than guessed at.
_CP1251_LEADS = frozenset(_ALL_LEAD_BYTES.decode("cp1251"))
_CP1251_CONTINUATIONS = frozenset(
    bytes(range(0x80, 0xC0)).decode("cp1251", errors="ignore")
)

#: A repair that produces private-use, unassigned or surrogate characters did not find
#: the original text; it invented one. Real Russian ending in "…" scores as a pair, and
#: without this check the repairer would turn it into a private-use character and call
#: that an improvement.
_IMPOSSIBLE_CATEGORIES = frozenset({"Co", "Cn", "Cs"})


def repair_utf8_mojibake(value: str) -> str:
    """Repair UTF-8 text that was decoded once with a legacy Windows codec.

    The setup-chat regression corpus contains captured ``≤``/``≥`` symbols and
    Arabic text decoded as Windows-1251. Normal user text is returned unchanged;
    a repair is accepted only when it reduces known mojibake markers.
    """

    current = value
    for _ in range(2):
        baseline = _mojibake_score(current)
        if baseline == 0:
            break
        candidates: list[str] = []
        for encoding in ("cp1251", "latin1"):
            try:
                candidate = current.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if _is_readable(candidate):
                candidates.append(candidate)
        if not candidates:
            break
        repaired = min(candidates, key=_mojibake_score)
        if _mojibake_score(repaired) >= baseline:
            break
        current = repaired
    return current


def _is_readable(value: str) -> bool:
    """Could a person have typed this? A repair into private-use space could not."""

    return all(
        unicodedata.category(character) not in _IMPOSSIBLE_CATEGORIES for character in value
    )


def _mojibake_score(value: str) -> int:
    score = sum(value.count(marker) for marker in _LATIN1_MARKERS)
    return score + sum(
        1
        for lead, tail in zip(value, value[1:], strict=False)
        if lead in _CP1251_LEADS and tail in _CP1251_CONTINUATIONS
    )
