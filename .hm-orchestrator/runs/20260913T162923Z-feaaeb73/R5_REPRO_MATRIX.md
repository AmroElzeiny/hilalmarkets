# R5 — what each row of the Telegram connect flow did before the fix, and after

The bug a person reported: they enter the long code, press the bot's confirm, and the bot
"keeps sending me the same message to confirm".

This table is measured, not read off the code. Every row runs the real update function
(`process_telegram_update`) against a fake Telegram that records every message the bot
asked to send, in
`tests/integration/test_telegram_connect_flow.py::test_one_answer_ends_the_connect_prompt`.

**Before the fix** was measured twice:

1. on this machine's working tree with the source unchanged — the matrix alone:
   `28 failed / 28`;
2. again in a clean worktree of `HEAD` (`C:\wt-r5`, commit `75b19580`) with only the new
   test files copied in: `53 failed, 8 passed` for the flow file as it now stands (all 28
   matrix rows among the failures), and the adapter unit file could not even be imported:
   `ImportError: cannot import name 'KEYBOARD_INLINE'`.

**After the fix**: `73 passed` for the flow file and `12 passed` for the unit file.
Command:

```powershell
.venv/Scripts/python -m pytest tests/integration/test_telegram_connect_flow.py -q -p no:randomly
```

## What the three outcome words mean

| Word | What the person experienced |
|---|---|
| **loop** | After the decision, the bot sent the whole confirmation question again — for any later message. The question had no end. |
| **crash** | The update raised inside the route. Telegram's side sees a failed webhook, so it retries; the person sees nothing at all. |
| **stuck keys** | The buttons were sent as a *reply* keyboard, which sits above the keyboard and keeps offering the same two choices after the matter is settled. The answer also arrived as a new message, leaving the question on screen beside it. |
| **wrong copy** | The bot answered, but said something a person cannot act on ("Telegram connection cancelled."), or offered no way forward. |

## The matrix — before

Rows are `{account shape} × {how the person answered}`. All 28 failed.

| # | Account shape | Answer | Before the fix | Recorded failure (trimmed) |
|---|---|---|---|---|
| 1 | fresh | tap Confirm | stuck keys + wrong copy | connected, but the answer was a new message and the pending token was never forgotten: `nobody forgot the pending link: it is still in the conversation` |
| 2 | pressed Start before | tap Confirm | **loop** | `the confirmation prompt came back after the person had answered it` — their own account refused them (`telegram_already_linked`), the step stayed open |
| 3 | account holds another Telegram | tap Confirm | **crash** | `PendingRollbackError ... IntegrityError UNIQUE constraint failed: telegram_connections.user_id` |
| 4 | Telegram on a different real account | tap Confirm | **loop** | refused, and then asked again forever |
| 5 | fresh | tap Cancel | wrong copy + stuck keys | `assert 'nothing was connected' in 'telegram connection cancelled.'`; the question message was never edited, so its buttons stayed |
| 6 | pressed Start before | tap Cancel | wrong copy + stuck keys | same |
| 7 | account holds another Telegram | tap Cancel | wrong copy + stuck keys | same |
| 8 | Telegram on a different real account | tap Cancel | wrong copy + stuck keys | same |
| 9 | fresh | type "Confirm" | **loop** | the full prompt came back — "Confirm" matched no rule |
| 10 | pressed Start before | type "Confirm" | **loop** | same |
| 11 | account holds another Telegram | type "Confirm" | **loop** | same |
| 12 | Telegram on a different real account | type "Confirm" | **loop** | same |
| 13 | fresh | type "Yes" | **loop** | the full prompt came back — "Yes" has no "connect" in it |
| 14 | pressed Start before | type "Yes" | **loop** | same |
| 15 | account holds another Telegram | type "Yes" | **loop** | same |
| 16 | Telegram on a different real account | type "Yes" | **loop** | same |
| 17 | fresh | send `/start` again, then tap | stuck keys + wrong copy | the pending token survived the decision |
| 18 | pressed Start before | send `/start` again, then tap | **loop** | refused their own account, step open |
| 19 | account holds another Telegram | send `/start` again, then tap | **crash** | same `IntegrityError` as row 3 |
| 20 | Telegram on a different real account | send `/start` again, then tap | **loop** | refused, then asked again |
| 21 | fresh | tap Confirm twice | stuck keys + wrong copy | second tap reported "Telegram link was already used" as a failure, and the token was never cleared |
| 22 | pressed Start before | tap Confirm twice | **loop** | both taps refused, step open |
| 23 | account holds another Telegram | tap Confirm twice | **crash** | `IntegrityError` on the first tap |
| 24 | Telegram on a different real account | tap Confirm twice | **loop** | refused, step open |
| 25 | fresh | same update delivered twice | stuck keys + wrong copy | the route replayed correctly, but the step and the token outlived the answer |
| 26 | pressed Start before | same update delivered twice | **loop** | refused the person's own account |
| 27 | account holds another Telegram | same update delivered twice | **crash** | `IntegrityError` |
| 28 | Telegram on a different real account | same update delivered twice | **loop** | refused, step open |

## The other rows — before

| Test | Before the fix |
|---|---|
| `test_the_prompt_is_one_message_with_exactly_two_inline_buttons` | failed — buttons were `✅ Yes, connect` / `Cancel` on a persistent reply keyboard, no keyboard style on the message |
| `test_the_prompt_warns_when_it_replaces_a_telegram` | failed — the prompt never mentioned the Telegram it was about to replace |
| `test_a_tap_answers_on_the_prompt_itself` | failed — `edit_message_id` was `None`: the answer went out as a new message |
| `test_cancel_clears_the_question_and_forgets_the_link` | failed — no edit, wrong words |
| `test_a_typed_answer_cannot_edit_and_clears_the_stale_keyboard` | failed — the stale keyboard was never taken away |
| `test_every_confirm_outcome_offers_the_connections_page` | failed — expired, invalid and spent links pointed at "the dashboard Integrations page" in words, with a button that opened the Home dashboard, not the Connections page |
| `test_an_unclear_reply_gets_a_line_not_the_prompt` | failed — "the whole prompt came back: a loop" |
| `test_the_shared_yes_vocabulary_answers_the_prompt[Confirm/confirm/Yes/yes/connect/ok/sure]` | failed for all seven — only the two old keyboard captions were understood |
| `test_the_shared_yes_vocabulary_answers_the_prompt[Yes, connect]` and `[✅ Yes, connect]` | passed before too, and are kept working on purpose: those are the keys still sitting in chats that opened before the fix |
| `test_the_shared_no_vocabulary_ends_the_prompt[No/no]` | failed — "No" re-sent the prompt |
| `test_the_shared_no_vocabulary_ends_the_prompt[Cancel/cancel/Back/back]` | passed before too: those four words were already handled, and are kept working deliberately |
| `test_a_refused_chat_does_not_keep_acting_as_the_account_that_was_refused` | failed — the refused chat kept `user_id` of the account that refused it |
| `test_a_second_link_offer_replaces_the_first_prompt` | passed before too (it pinned behaviour that was already right, so the fix cannot lose it) |
| `test_a_stale_button_tap_after_cancel_does_not_resurrect_the_link` | passed before too (same reason) |
| `test_a_confirm_that_breaks_internally_still_leaves_the_step` | failed — the crash escaped into the route's generic recovery text and the step stayed open |
| `test_the_ownership_rule_has_one_owner` | failed — the dashboard door silently re-pointed a Telegram held by somebody else's real account; the bot door refused the person's own shell account, and no move was ever audited |
| `test_both_doors_refuse_a_telegram_that_is_someone_elses` | failed — only one door refused |
| `test_a_telegram_already_on_this_account_connects_without_duplicates` | failed — the second confirm raised `telegram_link_used` instead of answering "already connected" |
| `test_the_same_token_confirmed_twice_decides_once` | failed — the second confirm raised instead of repeating the first answer |
| `test_the_dashboard_door_is_idempotent_for_the_same_link_too` | failed — same |
| `test_the_same_callback_delivered_twice_connects_once` | failed — the stored answer could not carry a keyboard style at all (see the serialiser rows) |
| `test_confirming_a_link_locks_the_link_row_before_deciding` | failed — completing a link read the row with no lock |
| `tests/unit/test_telegram_adapter_keyboard.py` (12 tests) | failed to import before the fix: `ImportError: cannot import name 'KEYBOARD_INLINE'` — the message had no way to ask for an inline keyboard |

## After the fix

| Row group | After |
|---|---|
| 28 matrix rows | pass. No row loops, no row crashes, every row leaves `telegram_link/confirm`, and the account ends as the one ownership rule says |
| flow tests (45 more) | pass |
| adapter + serialiser unit tests (12) | pass |
| `tests/integration/test_telegram_service.py` (17, including `test_telegram_dashboard_start_link_requires_confirmation_and_connects`) | pass, unedited |
| `tests/integration/test_telegram_webhook.py` | pass |

## What each shape now does, in one line

| Shape | What happens on Confirm |
|---|---|
| fresh | connected, on the dashboard account named in the prompt |
| pressed Start before | the Telegram-only account the bot made gives up the identity, the connection and the chat; its own history stays; one audit event names both accounts |
| account holds another Telegram | the prompt says `This replaces @old_handle`, and Confirm takes the old one off first, so no uniqueness error is possible |
| Telegram on a different real account | refused in plain words, nothing changed, and the chat goes back to the account that actually holds it |

## Still not proven from here

* Real Telegram behaviour. Every payload assertion is against a recording transport, not
  against `api.telegram.org`. Two things in particular are known from Telegram's Bot API
  documentation rather than measured: that `editMessageText` accepts an empty
  `{"inline_keyboard": []}` to remove buttons, and that `{"remove_keyboard": true}` clears
  a persistent keyboard on a new message.
* Two taps landing in *different* transactions at the same instant. SQLite has one
  connection and ignores `FOR UPDATE`, so the offline suite cannot interleave them. The
  lock is asserted as the statement PostgreSQL would run
  (`test_confirming_a_link_locks_the_link_row_before_deciding`), and the re-check after
  the lock is asserted as behaviour; the race itself needs a Postgres run to observe.
