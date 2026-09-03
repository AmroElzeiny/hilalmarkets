"""Every shipped environment example must be loadable by the application.

The examples are the file an operator copies to make a real deployment. Both of them
carried ``SETUP_BUILDER_USER_IDS=`` and ``SETUP_AI_LANGUAGES=``, and both settings are
``list[str]``. A bare ``=`` makes pydantic read ``""`` as JSON, so copying the template,
filling in the secrets and starting the product produced a refusal to boot with an error
that named neither the file, nor the line, nor the fix.

An existing test already checks that every setting *appears* in both examples. Appearing
is not the same as working, which is why that one passed the whole time.

These tests assert the rule for every structured setting rather than for the two that were
broken, so the next list or dict added to the templates is checked on the way in.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, get_args, get_origin

import pytest
from pydantic_settings import NoDecode

from ai_market_monitor.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = (".env.example", ".env.production.example")
KEY = re.compile(r"^([A-Z][A-Z0-9_]*)=")
STRUCTURED = (list, dict, set, frozenset, tuple)


def _reads_json(field: Any) -> bool:
    """Does the loader parse this field's value as JSON?

    Not every structured setting is a JSON one. A field marked ``NoDecode`` is handed to
    its own validator as the raw string, on purpose — that is how a setting an operator
    edits by hand can be written as ``HILAL25=25`` instead of as a JSON object. Applying
    the JSON rule to those would demand a shape the loader never asks for, and refuse a
    line that loads perfectly well.
    """

    return not any(
        item is NoDecode or isinstance(item, NoDecode) for item in field.metadata
    )


def _structured_settings() -> dict[str, Any]:
    """Every setting whose value the loader parses as JSON rather than as plain text."""

    found: dict[str, Any] = {}
    for name, field in Settings.model_fields.items():
        if not _reads_json(field):
            continue
        annotation = field.annotation
        candidates = [annotation, *get_args(annotation)]
        for candidate in candidates:
            if (get_origin(candidate) or candidate) in STRUCTURED:
                found[name.upper()] = annotation
                break
    return found


def _hand_written_settings() -> dict[str, Any]:
    """Every structured setting the loader hands to a validator as plain text.

    The JSON rules above cannot describe these, but the *real* rule still holds and is
    checked below: whatever the example writes has to load.
    """

    found: dict[str, Any] = {}
    for name, field in Settings.model_fields.items():
        if _reads_json(field):
            continue
        annotation = field.annotation
        candidates = [annotation, *get_args(annotation)]
        for candidate in candidates:
            if (get_origin(candidate) or candidate) in STRUCTURED:
                found[name] = annotation
                break
    return found


def _written_values(example: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / example).read_text(encoding="utf-8").splitlines():
        match = KEY.match(line)
        if match and match.group(1) not in values:
            values[match.group(1)] = line.split("=", 1)[1]
    return values


@pytest.mark.parametrize("example", EXAMPLES)
def test_the_example_file_loads(example: str) -> None:
    """The whole point of a template is that it works when copied."""

    Settings(_env_file=str(ROOT / example))


@pytest.mark.parametrize("example", EXAMPLES)
def test_no_structured_setting_is_left_blank(example: str) -> None:
    """A structured setting must never be written as a bare ``KEY=``.

    "Empty" for a list is ``[]`` and for a mapping ``{}``. An empty string is not a
    document, and the loader says so at startup rather than at review time.
    """

    structured = _structured_settings()
    written = _written_values(example)
    blank = sorted(
        key for key, value in written.items() if key in structured and value.strip() == ""
    )
    assert not blank, (
        f"{example} leaves structured settings blank instead of [] or {{}}: {blank}"
    )


@pytest.mark.parametrize("example", EXAMPLES)
def test_every_structured_value_is_the_shape_its_setting_expects(example: str) -> None:
    """Not merely non-blank: it has to parse into the declared type.

    Blankness was how it broke this time. The rule is the wider one, so a value that is
    present but malformed is caught by the same test.
    """

    import json

    structured = _structured_settings()
    written = _written_values(example)
    wrong: list[str] = []
    for key, annotation in structured.items():
        if key not in written:
            continue
        try:
            parsed = json.loads(written[key])
        except json.JSONDecodeError:
            wrong.append(f"{key} (not valid JSON, expects {annotation})")
            continue
        wants_mapping = "dict" in str(annotation)
        if wants_mapping and not isinstance(parsed, dict):
            wrong.append(f"{key} (expects a mapping)")
        if not wants_mapping and isinstance(parsed, dict):
            wrong.append(f"{key} (expects a sequence)")

    assert not wrong, f"{example} has values that cannot become their setting: {wrong}"


@pytest.mark.parametrize("example", EXAMPLES)
def test_a_hand_written_structured_value_really_loads(example: str) -> None:
    """The rule the JSON checks above are a proxy for, asserted directly.

    A setting the loader hands to its own validator as plain text cannot be judged by
    whether it looks like JSON — but it must still become its declared type. So the value
    the example ships is fed through the real setting, which is a stronger check than the
    shape rule and would have caught the blank list that started this file.
    """

    written = _written_values(example)
    wrong: list[str] = []
    for name, annotation in _hand_written_settings().items():
        key = name.upper()
        if key not in written:
            continue
        try:
            loaded = getattr(Settings(**{name: written[key]}), name)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            wrong.append(f"{key} ({type(exc).__name__}, expects {annotation})")
            continue
        origin = get_origin(annotation) or annotation
        if not isinstance(loaded, origin):
            wrong.append(f"{key} (loaded as {type(loaded).__name__}, expects {annotation})")

    assert not wrong, f"{example} has values that cannot become their setting: {wrong}"
