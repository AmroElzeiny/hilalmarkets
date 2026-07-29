from __future__ import annotations

from hm_chatbot_eval.scenarios import build_randomized_scenario_plan
from hm_chatbot_eval.topics import TOPICS


def _ids(selection_seed: int) -> list[str]:
    return [
        scenario.id
        for scenario in build_randomized_scenario_plan(
            TOPICS[:8],
            count_per_topic=2,
            global_seed=20260723,
            selection_seed=selection_seed,
        )
    ]


def test_randomized_plan_is_reproducible_from_its_selection_seed() -> None:
    assert _ids(9173) == _ids(9173)


def test_randomized_plan_changes_case_indexes_and_order_between_runs() -> None:
    first = _ids(9173)
    second = _ids(9174)

    assert first != second
    assert [value.split("-")[-2] for value in first] != ["001"] * len(first)


def test_randomized_plan_samples_without_replacement_per_topic() -> None:
    plan = build_randomized_scenario_plan(
        TOPICS[:4],
        count_per_topic=5,
        global_seed=7,
        selection_seed=11,
    )
    by_topic: dict[str, set[str]] = {}
    for scenario in plan:
        by_topic.setdefault(scenario.topic_id, set()).add(scenario.id)

    assert all(len(ids) == 5 for ids in by_topic.values())
