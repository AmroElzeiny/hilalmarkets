"""Reconcile OpenAI strict schemas with Pydantic defaults.

A strict JSON schema marks **every** property required, because that is what makes the
provider guarantee the shape. A model with nothing to put in a container then sends
``null`` — there is no way for it to omit the key. Pydantic rejects that for a field
whose type is not nullable, and a perfectly good plan fails validation on a field the
model had no opinion about.

Dropping those nulls before validation lets the declared default apply, which is what
"the model said nothing here" means. A field that genuinely accepts ``null`` keeps it:
``strategy_patch: StrategyPatch | None`` still distinguishes "no patch" from "a patch".
"""

from __future__ import annotations

from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel


def _accepts_none(annotation: Any) -> bool:
    if annotation is None or annotation is type(None):
        return True
    if get_origin(annotation) in {Union, UnionType}:
        return any(_accepts_none(arg) for arg in get_args(annotation))
    return False


def drop_absent_nulls(model: type[BaseModel], data: Any) -> Any:
    """Remove ``null`` values for fields that are not nullable, so defaults apply."""

    if not isinstance(data, dict):
        return data
    return {
        key: value
        for key, value in data.items()
        if not (
            value is None
            and key in model.model_fields
            and not _accepts_none(model.model_fields[key].annotation)
        )
    }
