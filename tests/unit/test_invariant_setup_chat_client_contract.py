"""The official Setup Chat client keeps the identity rule the server now enforces.

The client is in this repository: ``static/ai-setup-chat.js``. It is plain JavaScript
with no test runner, so these tests read it as text and assert the properties that
matter. That is weaker than executing it and stronger than assuming it — and it catches
the exact regressions this work removed:

* a button that posts its visible **label** as an ordinary chat message, so a reworded
  or translated label silently stops answering its own question;
* identity captured when a control was *drawn* rather than when it is *sent*, so a chip
  rendered under step one still answers step one after step two arrives.

The matching server-side behaviour — refusing a stale or unidentified message — is
proved against the real service in
``tests/integration/test_setup_chat_clarification_request_path.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CLIENT = Path("src/ai_market_monitor/static/ai-setup-chat.js")
SOURCE = CLIENT.read_text(encoding="utf-8")


def test_the_official_client_is_in_this_repository() -> None:
    """Stated as a test so "the frontend is elsewhere" can never be assumed again."""

    assert CLIENT.is_file()
    assert "/api/v1/dashboard/setup-chat" in SOURCE


def test_every_message_is_sent_through_one_function() -> None:
    """One send path is what makes "always attach the identity" checkable at all."""

    assert SOURCE.count("async function sendMessage(") == 1
    posts = re.findall(r"/messages`", SOURCE)
    assert len(posts) == 1, "messages must be posted from exactly one place"


def test_the_send_path_attaches_the_current_question_identity() -> None:
    assert "function withQuestionIdentity(" in SOURCE
    assert "question_id: activeQuestion.questionId" in SOURCE
    assert "step_revision: activeQuestion.stepRevision" in SOURCE
    body = SOURCE.split("async function sendMessage(", 1)[1]
    assert "withQuestionIdentity(" in body.split("lastAction", 1)[0], (
        "the identity must be attached to the payload that is actually sent"
    )


def test_the_identity_is_read_at_send_time_not_at_render_time() -> None:
    """An old DOM button must carry the *current* step, or it answers the wrong field."""

    assert "readActiveQuestion()" in SOURCE
    render = SOURCE.split("function renderConversation() {", 1)[1]
    assert render.lstrip().startswith("//") or "readActiveQuestion();" in render[:400], (
        "the identity is refreshed before controls are drawn"
    )
    # The click handler must not close over a captured identity: it calls the shared
    # sender, which reads the module-level value at the moment the request is built.
    assert "activeQuestion = {" in SOURCE
    assert "const capturedQuestionId" not in SOURCE


def test_a_retry_re_reads_the_identity_rather_than_replaying_an_old_one() -> None:
    """A retry of an action from an older step must not answer today's question."""

    assert "lastAction = () => sendMessage(requestPayload, content);" in SOURCE
    # `withQuestionIdentity` spreads over the payload, so a replayed payload has its
    # stale identity overwritten with the current one rather than kept.
    spread = SOURCE.split("function withQuestionIdentity(", 1)[1]
    assert "...payload," in spread.split("}", 1)[0] + spread[:400]


def test_no_clarification_option_posts_its_label_as_a_chat_message() -> None:
    """The defect: a translated or reworded label stopped answering its own question."""

    assert "chatReply" not in SOURCE, "the label-as-text route must be gone"
    assert 'key: "clarification_answer"' in SOURCE


def test_clarification_options_send_the_canonical_value_not_the_label() -> None:
    assert "canonical[index] ?? label" in SOURCE
    assert "option_value: option.value" in SOURCE
    assert "option_label: label" in SOURCE


@pytest.mark.parametrize(
    "field",
    ["question_id", "step_revision", "workflow_id", "canonical_values"],
)
def test_the_client_reads_every_identity_field_the_server_sends(field: str) -> None:
    reader = SOURCE.split("function readActiveQuestion() {", 1)[1].split("\n  }", 1)[0]
    assert field in reader, f"{field} is not read from the server payload"


def test_the_start_mode_buttons_still_use_their_own_product_control() -> None:
    """Scanner and Monitor are product entry points, not answers to a question."""

    assert "option_key: option.key," in SOURCE
    assert "ai-chat-start-modes" in SOURCE


# ---------------------------------------------------------------------------------
# Durability: the composer, the retry, and the server-owned draft actions
# ---------------------------------------------------------------------------------


def test_every_message_carries_a_request_id_the_client_generates() -> None:
    """The server refuses a message without one, so the client must always send one."""

    assert "newClientMessageId()" in SOURCE
    body = SOURCE.split("async function sendMessage(", 1)[1]
    sent = body.split("lastAction", 1)[0]
    assert "client_message_id: payload.client_message_id || newClientMessageId()" in sent, (
        "the key must be minted before sending, and an existing one reused"
    )


def test_a_retry_reuses_the_same_request_id() -> None:
    """A retry must replay the same turn, not buy a second one.

    ``lastAction`` closes over the payload that was already built, so the key inside it
    is the key of the attempt being retried. Rebuilding the payload here would mint a
    new key and charge the user twice for one message.
    """

    body = SOURCE.split("async function sendMessage(", 1)[1]
    assert "lastAction = () => sendMessage(requestPayload, content)" in body, (
        "retry must resend the built payload, not the original arguments"
    )
    assert "data-ai-chat-retry" in SOURCE


def test_the_composer_closes_while_a_turn_is_running() -> None:
    """A double-click must not be able to start a second paid turn."""

    assert "function activeTurn()" in SOURCE
    state = SOURCE.split("function updateSendState() {", 1)[1].split("\n  }", 1)[0]
    assert "activeTurn()" in state
    assert "sendButton.disabled" in state and "running" in state
    assert "input.disabled" in state


def test_a_slow_turn_is_explained_without_offering_a_second_send() -> None:
    """"Taking longer than expected" must never read as "send it again"."""

    state = SOURCE.split("function updateSendState() {", 1)[1].split("\n  }", 1)[0]
    assert "running.slow" in state
    assert "no need to send it again" in state.lower()


@pytest.mark.parametrize(
    "action",
    [
        "undo_last_material_change",
        "restore_snapshot",
        "reset_current_draft",
        "confirm_pending_change",
        "cancel_pending_change",
    ],
)
def test_the_client_can_reach_every_server_owned_draft_action(action: str) -> None:
    assert action in SOURCE, f"{action} has no control in the official client"


def test_draft_actions_are_keyed_and_posted_to_their_own_route() -> None:
    """Each is idempotent, so a double-clicked Undo undoes once."""

    body = SOURCE.split("async function sendDraftAction(", 1)[1]
    assert "client_message_id: newClientMessageId()" in body
    assert "/draft-actions`" in body


def test_clearing_the_draft_asks_first_and_says_what_is_kept() -> None:
    """Reset loses work. The question must say what survives, not only what goes."""

    assert "window.confirm(" in SOURCE
    assert "saved versions" in SOURCE.lower()
    assert 'sendDraftAction("reset_current_draft", {confirmed: true})' in SOURCE


def test_the_confirmation_card_is_built_from_the_server_diff() -> None:
    """Never from the assistant sentence: a wrong plan would also describe itself wrong."""

    assert "function renderPendingChange()" in SOURCE
    card = SOURCE.split("function renderPendingChange() {", 1)[1].split("\n  }", 1)[0]
    assert "chat?.pending_change" in card
    assert "diffLines(pending.diff)" in card
    assert "pending.summary_lines" in card
    # The wording of the assistant message must not be what the card renders.
    assert "latestAssistantPayload" not in card


def test_the_diff_renderer_reads_only_server_owned_fields() -> None:
    lines = SOURCE.split("function diffLines(diff) {", 1)[1].split("\n  }", 1)[0]
    for field in (
        "removed_conditions",
        "added_conditions",
        "changed_fields",
        "boolean_topology_changed",
        "methodology_changes",
        "universe_changes",
        "market_scope_changes",
        "approval_invalidated",
    ):
        assert field in lines, f"{field} is never shown to the user"


def test_a_stale_proposal_cannot_be_confirmed_from_the_client() -> None:
    """The server refuses it anyway; offering the button would just mislead."""

    card = SOURCE.split("function renderPendingChange() {", 1)[1].split("\n  }", 1)[0]
    assert "if (!pending.stale) {" in card, "the confirm button is hidden when stale"
