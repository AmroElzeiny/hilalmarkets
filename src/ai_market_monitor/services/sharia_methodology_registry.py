"""One owner for what an imported Shariah methodology *is*.

Before this module the import pack knew exactly three authorities, and it knew them
**fourteen times**. ``EXPECTED_COUNTS``, ``PACKAGE_TO_SYSTEM_CODE``, ``DATASET_FILES``,
``SOURCE_FAMILIES`` and ``PUBLICATION_GATES`` were five parallel dictionaries keyed by
the same three identifiers; ``_methodology_rules`` carried two more inside itself; and
``_source_reference``, ``_rights_state``, ``_commercial_display_allowed``,
``_evidence_requirements``, ``_source_snapshot``, ``_validate_bundle`` and
``load_import_pack`` each re-decided the same question with a hand-written ``if`` on the
identifier.

That is the recurring root cause this codebase names: **two places that each understood
a different subset of the same vocabulary**. It had already produced a working system
that could not grow. Adding a fourth authority meant finding all fourteen sites; missing
any one of the five dictionaries raised ``KeyError`` deep inside the import, and missing
one of the ``if`` chains was worse — it silently fell through to the Fasset branch and
gave the new authority Fasset's rights state, Fasset's source reference and Fasset's
publication gate.

Here a methodology is **one record, declared once, in the pack's own
``data/methodologies.json``**. Python holds no table of authorities at all. Adding an
authority is adding a data file and a definition row; no code in this repository names
it, so no code can disagree about it.

What the definition must answer, and why each answer cannot be guessed:

===========================  =================================================
``system_code``              The ``sharia_methodologies.code`` this becomes.
                             Deliberately separate from ``methodology_id``:
                             SC Malaysia's package id and system code already
                             differ, and inferring one from the other would
                             silently repoint an authority's whole history.
``dataset_file``             Which file in ``data/`` holds its rows.
``guard_file``               Optional. Rows the source published as **not**
                             compliant, kept so an importer can never treat
                             every row it can see as eligible.
``source_adapter``           The short machine name used to build criteria and
                             use-case keys. It is written into stored rules,
                             so it must be stable for the life of the source.
``source_family``            Groups snapshots and monitoring runs. Two
                             methodologies from one authority share it.
``publication_gate``         What must happen before a result may be shown.
``rights``                   Whether the source's own text may be displayed
                             commercially, and where that answer is recorded.
``source_reference``         How to name the exact decision a row came from —
                             an SAC meeting number, an assessment date, or the
                             source row id when the source publishes no
                             reference of its own.
===========================  =================================================

Nothing here decides a Shariah status, and nothing here approves anything. A definition
says where a result came from and what may be done with it. The result itself is
whatever the external source published, and publication still runs through the
application's own review and approval route.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Placeholders inside a ``source_reference`` template: ``{field_name}``.
_TEMPLATE_FIELD = re.compile(r"\{([a-z0-9_]+)\}")


class ShariaMethodologyDefinitionError(RuntimeError):
    """A methodology definition in the pack is missing or malformed.

    Raised while loading, never while importing. A half-declared authority must stop
    the whole pack before a single row is written, because the fallbacks it would
    otherwise inherit belong to a different authority.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RightsRule:
    """Where a row's rights answer comes from, and what it is when the row is silent.

    Three shapes exist in the shipped pack and all three are expressed here without a
    branch on the authority's name:

    * an official regulator's public list — a fixed state, display always allowed;
    * a commercial research source — both answers carried per row;
    * a platform's report pages — a fixed state, display carried per row.
    """

    state_field: str | None
    state_default: str | None
    display_field: str | None
    display_default: bool

    def state_for(self, row: Mapping[str, Any]) -> str:
        if self.state_field is not None:
            value = row.get(self.state_field)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if self.state_default is None:
                raise ShariaMethodologyDefinitionError(
                    "rights_state_missing",
                    f"A source row is missing its required {self.state_field}.",
                )
        if self.state_default is None:  # pragma: no cover - guarded at load time
            raise ShariaMethodologyDefinitionError(
                "rights_state_missing",
                "A methodology declares no rights state and no row field for it.",
            )
        return self.state_default

    def display_allowed_for(self, row: Mapping[str, Any]) -> bool:
        if self.display_field is None:
            return self.display_default
        return bool(row.get(self.display_field, self.display_default))

    @classmethod
    def from_definition(
        cls,
        package_id: str,
        payload: Mapping[str, Any],
    ) -> RightsRule:
        state_field = _optional_string(payload.get("state_field"))
        state_default = _optional_string(payload.get("state_default"))
        if state_field is None and state_default is None:
            raise ShariaMethodologyDefinitionError(
                "rights_rule_invalid",
                f"{package_id} declares neither a rights state nor a field holding one.",
            )
        return cls(
            state_field=state_field,
            state_default=state_default,
            display_field=_optional_string(payload.get("display_field")),
            display_default=bool(payload.get("display_default", False)),
        )


@dataclass(frozen=True, slots=True)
class MethodologySpec:
    """Everything the importer needs to know about one external authority."""

    package_id: str
    system_code: str
    display_name: str
    short_label: str
    authority: str
    source_adapter: str
    source_family: str
    dataset_file: str
    guard_file: str | None
    manifest_count_key: str
    manifest_guard_count_key: str | None
    publication_gate: str
    rights: RightsRule
    source_reference_template: str
    records_count: int
    guard_records_count: int
    scope: str
    rights_clearance_required: bool
    definition: Mapping[str, Any]

    @property
    def dataset_path(self) -> str:
        return f"data/{self.dataset_file}"

    @property
    def guard_path(self) -> str | None:
        return None if self.guard_file is None else f"data/{self.guard_file}"

    def source_reference(self, row: Mapping[str, Any]) -> str:
        """Name the exact external decision this row came from.

        The template is the authority's own way of citing itself. SC Malaysia cites an
        SAC meeting and a decision date; the Shariah Review Bureau cites an assessment
        date; Fasset publishes no citation, so its own row identifier stands in. Every
        field the template names must be present and non-empty, so a source that stops
        publishing its reference fails the import rather than producing a passport that
        cites nothing.
        """

        values: dict[str, str] = {}
        for field in _TEMPLATE_FIELD.findall(self.source_reference_template):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ShariaMethodologyDefinitionError(
                    "source_reference_incomplete",
                    f"{self.package_id} row is missing its {field} source reference.",
                )
            values[field] = value.strip()
        return self.source_reference_template.format(**values)


def load_methodology_specs(
    definitions: Mapping[str, Mapping[str, Any]],
) -> dict[str, MethodologySpec]:
    """Turn the pack's methodology definitions into specs, or refuse the pack.

    Fail closed: a definition missing any field is an error here, at load time, not a
    default applied quietly during the import. A default would mean an authority
    inheriting another authority's rights or publication gate, which is the exact
    failure this module exists to remove.
    """

    specs: dict[str, MethodologySpec] = {}
    seen_codes: dict[str, str] = {}
    seen_adapters: dict[str, str] = {}
    seen_datasets: dict[str, str] = {}
    for package_id, definition in definitions.items():
        spec = _spec_from_definition(package_id, definition)
        for value, seen, label in (
            (spec.system_code, seen_codes, "system_code"),
            (spec.source_adapter, seen_adapters, "source_adapter"),
            (spec.dataset_file, seen_datasets, "dataset_file"),
        ):
            if value in seen:
                raise ShariaMethodologyDefinitionError(
                    "methodology_definition_conflict",
                    f"{package_id} reuses the {label} of {seen[value]}.",
                )
            seen[value] = package_id
        specs[package_id] = spec
    if not specs:
        raise ShariaMethodologyDefinitionError(
            "methodology_set_empty",
            "The import pack declares no methodologies.",
        )
    return specs


def _spec_from_definition(
    package_id: str,
    definition: Mapping[str, Any],
) -> MethodologySpec:
    import_rules = definition.get("import_rules")
    if not isinstance(import_rules, Mapping):
        raise ShariaMethodologyDefinitionError(
            "methodology_import_rules_missing",
            f"{package_id} has no import_rules block; the importer cannot place it.",
        )
    rights_payload = import_rules.get("rights")
    if not isinstance(rights_payload, Mapping):
        raise ShariaMethodologyDefinitionError(
            "methodology_rights_missing",
            f"{package_id} declares no rights rule.",
        )
    guard_file = _optional_string(import_rules.get("guard_file"))
    guard_count_key = _optional_string(import_rules.get("manifest_guard_count_key"))
    if (guard_file is None) != (guard_count_key is None):
        raise ShariaMethodologyDefinitionError(
            "methodology_guard_incomplete",
            f"{package_id} must declare a guard file and its manifest count together.",
        )
    records_count = import_rules.get("records_count", definition.get("records_count"))
    return MethodologySpec(
        package_id=package_id,
        system_code=_required_string(package_id, import_rules, "system_code"),
        display_name=_required_string(package_id, definition, "display_name"),
        short_label=_required_string(package_id, import_rules, "short_label"),
        authority=_required_string(package_id, definition, "authority"),
        source_adapter=_required_string(package_id, import_rules, "source_adapter"),
        source_family=_required_string(package_id, import_rules, "source_family"),
        dataset_file=_required_string(package_id, import_rules, "dataset_file"),
        guard_file=guard_file,
        manifest_count_key=_required_string(
            package_id, import_rules, "manifest_count_key"
        ),
        manifest_guard_count_key=guard_count_key,
        publication_gate=_required_string(
            package_id, definition, "default_publication_gate"
        ),
        rights=RightsRule.from_definition(package_id, rights_payload),
        source_reference_template=_required_string(
            package_id, import_rules, "source_reference_template"
        ),
        records_count=_required_count(package_id, records_count, "records_count"),
        guard_records_count=(
            0
            if guard_file is None
            else _required_count(
                package_id,
                import_rules.get("guard_records_count"),
                "guard_records_count",
            )
        ),
        scope=_required_string(package_id, definition, "scope"),
        rights_clearance_required=bool(
            import_rules.get("rights_clearance_required", False)
        ),
        definition=definition,
    )


def _required_string(
    package_id: str,
    payload: Mapping[str, Any],
    key: str,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ShariaMethodologyDefinitionError(
            "methodology_field_missing",
            f"{package_id} is missing the required methodology field {key}.",
        )
    return value.strip()


def _required_count(package_id: str, value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ShariaMethodologyDefinitionError(
            "methodology_count_invalid",
            f"{package_id} declares an invalid {key}.",
        )
    return value


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
