from __future__ import annotations

_MOJIBAKE_MARKERS = (
    "Ã",
    "Â",
    "â",
    "Ð",
    "Ñ",
    "Ø",
    "Ù",
    "Ш",
    "Щ",
    "вЂ",
    "в‰",
    "рџ",
)


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
                candidates.append(current.encode(encoding).decode("utf-8"))
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
        if not candidates:
            break
        repaired = min(candidates, key=_mojibake_score)
        if _mojibake_score(repaired) >= baseline:
            break
        current = repaired
    return current


def _mojibake_score(value: str) -> int:
    return sum(value.count(marker) for marker in _MOJIBAKE_MARKERS)
