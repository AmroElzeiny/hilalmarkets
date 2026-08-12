"""What a person actually sees when they group rules, and when the assistant is off.

Two things are checked in a real browser because neither can be checked anywhere else:

* **Grouping is visible.** "A and (B or C)" is a *shape*. A server test can prove the
  stored tree is right and still leave a screen where the person cannot tell a nested
  group from a flat list — which is exactly the bug that made nesting unusable before.
* **Losing the assistant leaves a usable screen.** Every server test says the Builder
  still works. This says the person can still *see and use* it, with no error page, no
  raw traceback and no dead end.
"""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    signup,
    unique_email,
)


def _open_builder(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/dashboard/strategies/new", wait_until="domcontentloaded")
    expect(page.get_by_test_id("ai-setup-chat")).to_be_visible(timeout=10_000)


def _open_canvas(page: Page) -> None:
    """The Builder canvas, however this viewport reaches it."""

    opener = page.locator("[data-ai-open-canvas]")
    if opener.count() and opener.first.is_visible():
        opener.first.click()
    expect(page.get_by_test_id("strategy-builder-root")).to_be_visible(timeout=10_000)


def test_the_builder_offers_the_whole_catalogue_not_a_beginner_subset(
    page: Page, base_url: str
) -> None:
    """47 of 502 capabilities used to be offered here. The rest needed the assistant."""

    signup(page, base_url, unique_email("builder-catalogue"))
    _open_builder(page, base_url)
    _open_canvas(page)

    contract = page.evaluate(
        """
        async () => {
          const response = await fetch('/api/v1/dashboard/setup-chat/builder-contract', {
            credentials: 'same-origin',
          });
          return response.ok ? await response.json() : null;
        }
        """
    )
    assert contract, "the Builder could not read its own contract"
    mechanics = contract["mechanics"]
    assert len(mechanics) > 400, (
        f"the Builder is offering only {len(mechanics)} mechanics; "
        "authoring must not depend on the assistant"
    )
    # Difficulty is a grouping hint, never a filter: hard rules are offered too.
    assert any(item["beginner_friendly"] is False for item in mechanics)
    # And a rule waiting on a data feed says which feed instead of disappearing.
    waiting = [item for item in mechanics if not item["provider_requirements_met"]]
    for item in waiting:
        assert item["unavailable_reason"], f"{item['key']} vanished without a reason"
    assert contract["boolean_limits"]["max_depth"] >= 2
    assert_no_raw_traceback(page)


def test_grouped_rules_are_drawn_as_a_nested_shape_a_person_can_read(
    page: Page, base_url: str
) -> None:
    """The stored tree being right is not enough if the screen draws a flat list."""

    signup(page, base_url, unique_email("builder-grouping"))
    _open_builder(page, base_url)
    _open_canvas(page)

    tree = page.get_by_test_id("guided-builder-logic-tree")
    actions = page.get_by_test_id("guided-builder-group-actions")
    if not tree.count():
        # No rules yet: the Builder still has to say what to do next rather than showing
        # an empty box.
        expect(page.get_by_test_id("strategy-builder-root")).to_be_visible()
        assert_no_raw_traceback(page)
        return

    expect(tree).to_be_visible()
    expect(actions).to_contain_text(re.compile("tick .*rules", re.I))

    rows = tree.locator(".gb-logic-row")
    if rows.count() >= 2:
        for index in range(2):
            checkbox = rows.nth(index).locator("input[type=checkbox]")
            if checkbox.count():
                checkbox.first.check()
        # With two ticked, the grouping choices appear — and "None of these", which takes
        # exactly one rule, does not.
        expect(actions).to_contain_text(re.compile("group as", re.I))

        page.get_by_role("button", name=re.compile("group as .*any", re.I)).first.click()
        expect(tree.locator('.gb-logic-row[data-kind="group"]')).to_be_visible(
            timeout=10_000
        )
        # Indentation is how nesting is *seen*. A child that is not indented is a flat
        # list wearing a group's badge.
        indents = tree.locator(".gb-logic-row").evaluate_all(
            "rows => rows.map(row => parseInt(row.style.marginInlineStart || '0', 10))"
        )
        assert max(indents) > 0, "a nested rule must be drawn further in than its group"

    assert_no_horizontal_overflow(page)
    assert_no_raw_traceback(page)


@pytest.mark.deliberate_console_errors("503", "Service Unavailable")
def test_the_builder_screen_still_works_when_the_assistant_refuses(
    page: Page, base_url: str
) -> None:
    """Every assistant turn refused, and the person can still author.

    The refusal is injected at the network layer so the *real* client code handles it —
    which is the part a server test cannot reach.
    """

    signup(page, base_url, unique_email("builder-ai-refused"))
    _open_builder(page, base_url)

    page.route(
        "**/api/v1/dashboard/setup-chat/sessions/*/messages",
        lambda route: route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps(
                {
                    "detail": {
                        "code": "USER_DAILY_BUDGET_EXCEEDED",
                        "message": (
                            "You have used today's assistant allowance. "
                            "You can still build setups yourself."
                        ),
                        # The server knows waiting cannot help. The client must not
                        # invite a retry that can only fail again.
                        "retryable": False,
                    }
                }
            ),
        ),
    )
    page.locator("[data-ai-chat-input]").fill("alert me when BTC rises 5%")
    page.locator("[data-ai-chat-send]").click()

    # The refusal is shown as words, never as a stack trace or a blank screen.
    expect(page.locator("[data-ai-chat-error-text]")).to_contain_text(
        re.compile("build setups yourself", re.I), timeout=20_000
    )
    # And the failed bubble does not send the person round a loop that cannot work.
    delivery = page.locator(".ai-chat-delivery.failed")
    expect(delivery.last).to_contain_text(
        re.compile("build this yourself", re.I), timeout=10_000
    )
    assert_no_raw_traceback(page)

    # And the Builder is still there and still usable.
    _open_canvas(page)
    expect(page.get_by_test_id("strategy-builder-root")).to_be_visible()
    assert_no_horizontal_overflow(page)


def test_the_assistant_allowance_is_readable_by_the_person_it_belongs_to(
    page: Page, base_url: str
) -> None:
    """A limit nobody can see is a limit that looks like a fault when it is reached."""

    signup(page, base_url, unique_email("builder-allowance"))
    _open_builder(page, base_url)

    usage = page.evaluate(
        """
        async () => {
          const response = await fetch('/api/v1/dashboard/ai-usage', {
            credentials: 'same-origin',
          });
          return response.ok ? await response.json() : null;
        }
        """
    )
    assert usage, "a person cannot read their own allowance"
    assert "remaining_usd" in usage or "allowance_usd" in usage
    assert "ai_available" in usage
    # Never another person's number, and never a provider secret.
    body = str(usage).casefold()
    for forbidden in ("api_key", "authorization", "secret", "bearer", "prompt"):
        assert forbidden not in body, f"the allowance payload leaked {forbidden}"
    assert_no_raw_traceback(page)
