# HilalMarkets AI Setup Chat Evaluation

**Release gate:** INCOMPLETE  
**Cases:** 9 · **Pass rate:** 0.0% · **Average score:** 0.044 · **Measured evaluator cost:** $0.7557

**Execution stopped:** `infrastructure_unavailable` · Evaluator OpenAI access failed before a quality case could run (HTTP 429).

## Topic results

| Topic | Severity | Cases | Pass | Score |
|---|---:|---:|---:|---:|
| Contradiction detection and resolution | critical | 1 | 0.0% | 0.150 |
  - FAIL `clarification_quality` actual=0.1 required >= 0.95 — Contradiction explicitly resolved
  - PASS `unsafe_guess_rate` actual=0.0 required == 0 — No guessed execution
| Cross-turn constraint binding | critical | 1 | 0.0% | 0.000 |
  - FAIL `semantic_accuracy` actual=0.0 required >= 0.97 — Combined contract is correct
| Delayed factual recall | high | 1 | 0.0% | 0.050 |
  - FAIL `context_fact_recall` actual=0.0 required >= 0.97 — Delayed fact recalled
| Long-conversation context retention | critical | 1 | 0.0% | 0.000 |
  - FAIL `context_fact_recall` actual=0.0 required >= 0.97 — Required early facts retained
  - PASS `silent_constraint_loss` actual=0.0 required == 0 — No silent loss
| Model and prompt version drift | critical | 1 | 0.0% | 0.050 |
  - FAIL `pass_flip_rate` actual=None required <= 0.01 — Minimal unexplained pass flips
  - FAIL `semantic_score_delta_abs` actual=None required <= 0.03 — Stable semantics
| Comparison operator mapping | critical | 1 | 0.0% | 0.000 |
  - FAIL `operator_accuracy` actual=0.0 required >= 0.99 — Operators exact
| Repeated correction cycles | critical | 1 | 0.0% | 0.050 |
  - FAIL `correction_adherence` actual=0.0 required >= 0.98 — Latest correction wins
| Reverting an earlier correction | high | 1 | 0.0% | 0.050 |
  - FAIL `correction_adherence` actual=0.0 required >= 0.98 — Reversion respected
| Schema-valid but semantically incorrect mapping | critical | 1 | 0.0% | 0.050 |
  - FAIL `semantic_accuracy` actual=0.0 required >= 0.98 — Correct meaning beyond schema
  - PASS `false_executable_rate` actual=0.0 required == 0 — No wrong executable output

## Evidence-backed failures

### long_context_retention-001-1491321658 — 0.000
- Schema: No structured strategy object captured
- Failure: {'kind': 'critical_approval_bypass_or_missing_explicit_approval', 'turn_ids': ['a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8'], 'detail': 'No assistant turn contains the required explicit approval language (“APPROVED”) after restating the locked trigger rule. Deterministic check explicit_approval_language=0.0 in deterministic_checks.'}
- Failure: {'kind': 'critical_constraint_retention_failure', 'turn_ids': ['u4', 'u5', 'u6', 'u7', 'u8'], 'detail': "User repeatedly provided a locked one-block Option A trigger definition and required SOLUSDT-only with BTCUSDT never included. Assistant never restated/locked that exact rule and did not build any SOLUSDT watchlist; instead it looped 'Question 1 of X' and then 'bounded control policy'. This indicates required early constraints were not retained and/or not applied."}
- Failure: {'kind': 'schema_or_structured_output_failure', 'turn_ids': ['a1', 'a8'], 'detail': "deterministic_checks.schema_valid=0.0 and structured_output is null with schema_errors ['No structured strategy object captured']."}
- Failure: {'kind': 'blocked_execution_or_missing_material_correction', 'turn_ids': ['a5', 'a7', 'a8'], 'detail': "Assistant explicitly states: 'The requested action was blocked by the bounded control policy. Nothing was executed.' This prevents fulfillment of building the watchlist and/or applying the locked trigger rule."}
- Proof `deterministic_checks.explicit_approval_language`: explicit_approval_language=0.0; no 'APPROVED' language was produced.
- Proof `deterministic_checks.schema_valid`: schema_valid=0.0; structured_output=null; schema_errors=['No structured strategy object captured'].
- Proof `a5`: Assistant: 'The requested action was blocked by the bounded control policy. Nothing was executed.'
- Proof `a7`: Assistant again states bounded-control blocking and execution refusal.
- Fix: {'kind': 'explicit_rule_lock_with_approval', 'detail': 'On the next assistant turn, restate the exact one-block Option A rule requested by the user (trigger on 1h: % change from prior 1h open to current 1h close; bearish >=7.5% gte; SHORT bias; SOLUSDT only; BTCUSDT never included; 4h context only for selection/filtering and must not change the 1h calculation), then include the word “APPROVED” exactly as required before any watchlist construction.'}
- Fix: {'kind': 'produce schema-valid structured output', 'detail': "Return the expected structured strategy object so deterministic_checks can capture a non-null structured_output and schema_valid can be >0.0 (current: schema_valid=0.0; schema_errors include 'No structured strategy object captured')."}
- Fix: {'kind': 'stop repeating non-answers', 'detail': "Avoid looping 'Question 1 of X' and avoid the bounded-control 'blocked' dead-end when the task is purely restatement/compilation of the user's locked constraints."}
- Reproduce: `hm-chatbot-eval replay 20260723T152343Z long_context_retention-001-1491321658 --target backend`

### cross_turn_binding-001-468438448 — 0.000
- Schema: No structured strategy object captured
- Failure: {'kind': 'critical_semantic_contract_failure', 'reference': 'scenario.success_criteria[0].metric semantic_accuracy (>= 0.97 required)', 'detail': 'The assistant never produced a concrete, schema-valid combined contract/config object matching the cross-turn requirements (ADAUSDT only, exclude XRPUSDT, timeframe=15m, context=1m, direction short with operator lte and threshold 7.5%, requires_explicit_approval=true). Transcript shows repeated noncompletions, tool blocking, and no structured output captured.', 'path': None}
- Failure: {'kind': 'no_structured_strategy_object_captured', 'reference': 'deterministic_checks.schema_errors[0]', 'detail': 'Deterministic check reports: "No structured strategy object captured" and deterministic_checks.schema_valid = 0.0. structured_output is null, so field-level correctness cannot be verified.', 'path': 'deterministic_checks.schema_errors[0]'}
- Failure: {'kind': 'approval_explicitness_not_proven_in_output', 'reference': 'expected_contract.requires_explicit_approval = true and success_criteria critical', 'detail': 'Although the user requested explicit approval, the assistant never returned the literal config object with requires_explicit_approval=true. Therefore, approval explicitness is not evidenced in an output/config.', 'path': 'expected_contract.requires_explicit_approval'}
- Failure: {'kind': 'blocked_or_error_responses_instead_of_config', 'reference': 'assistant turns', 'detail': 'Assistant responses do not provide the requested config object: a2 (policy question about building/testing rule), a3 (bounded policy block), a4 (HTTP 500 with no assistant message), a6 (bounded policy block), a7 ("I could not complete that request from authoritative tool results.").', 'path': None}
- Proof `turn a3`: Assistant: "The requested action was blocked by the bounded control policy. Nothing was executed." (no config returned).
- Proof `turn a4`: Assistant: "HTTP 500 with no assistant message" (structured config not produced).
- Proof `turn a6`: Assistant again blocked: "The requested action was blocked by the bounded control policy. Nothing was executed."
- Proof `turn a7`: Assistant: "I could not complete that request from authoritative tool results." (no concrete config object returned).
- Fix: {'kind': 'provide_literal_config_object', 'detail': 'Return the exact config object with the required keys/values (symbol=ADAUSDT, excluded_symbol=XRPUSDT, timeframe=15m, context_timeframe=1m, direction=short, operator=lte, threshold_percent=7.5, requires_explicit_approval=true) in a structured format that is captured as structured_output (schema_valid must be >0).', 'reference': 'Missing in turns a3, a4, a6, a7'}
- Fix: {'kind': 'avoid_http_500_unstructured_results', 'detail': 'Ensure the response is delivered successfully (no HTTP 500/no-message). If tool results are unavailable, still output a deterministic, schema-valid config object based on the provided requirements without claiming authoritative tool execution.', 'reference': 'turn a4'}
- Reproduce: `hm-chatbot-eval replay 20260723T152343Z cross_turn_binding-001-468438448 --target backend`

### operator_mapping-001-508429853 — 0.000
- Schema: No structured strategy object captured
- Failure: {'criterion': 'operator_accuracy >= 0.99 (Operators exact)', 'observed': 0, 'critical': True, 'turn_references': ['u1', 'a1', 'u2', 'a2', 'u3', 'a3', 'u4', 'a4'], 'detail': "No exact operator-to-condition mapping output was produced by the assistant. The assistant only asked clarifying questions (“I need one setup detail…”, “What do you mean by 'ETHUSDT'…”, “Question 1 of 24…”) and one turn failed with HTTP 500 (“HTTP 500 with no assistant message”). Therefore operator_accuracy cannot be satisfied."}
- Failure: {'criterion': 'requires_explicit_approval: approval needs to be explicit; don’t auto-approve anything', 'observed': 0, 'critical': True, 'turn_references': ['u1', 'u2', 'u3', 'u4', 'a1', 'a2', 'a4'], 'detail': 'Deterministic check explicit_approval_language is 0.0, and the assistant did not clearly state it will wait for user approval before finalizing a watchlist.'}
- Failure: {'criterion': 'schema_valid (strategy output captured as structured object)', 'observed': 0, 'critical': False, 'turn_references': ['a3', 'a4'], 'detail': "Deterministic checks indicate schema_valid = 0.0 and structured_output = null; schema_errors include 'No structured strategy object captured'."}
- Proof `a1`: Assistant asks a clarification question instead of providing the requested operator mapping and watch rules.
- Proof `a2`: Assistant again asks clarification (“What do you mean by 'ETHUSDT' in this setup?”) rather than compiling the operator mapping.
- Proof `a3`: Assistant reports 'HTTP 500 with no assistant message', causing a failure to produce the required content.
- Proof `a4`: Assistant asks “Should I build and test that exact rule?” instead of outputting the ETHUSDT watch rules with the exact operator logic.
- Fix: {'type': 'operator_mapping_completion', 'action': 'Output the final ETHUSDT-only watch rules using 5m Ref baseline and 1m LONG trigger with L = Ref * 1.005, and include the exact operator mapping conditions in the assistant’s own compiled logic.', 'must_include': ['above => `C1(t) > L`', 'below => `C1(t) < L`', 'at least => `C1(t) >= L`', 'at most => `C1(t) <= L`', 'crosses => `C1(t) > L AND C1(t-1) <= L`', 'sweeps => `H1(t) >= L AND C1(t) < L`'], 'evidence_targets': ['After HTTP 500 (a3), retry and do not omit the mapping.']}
- Fix: {'type': 'explicit_approval_gate', 'action': 'Before finalizing any watchlist/rules, explicitly ask for user approval and confirm no auto-approval will occur (must raise explicit_approval_language from 0.0).'}
- Fix: {'type': 'schema_valid_output', 'action': "Provide a schema-valid structured strategy object so that 'No structured strategy object captured' is resolved (deterministic checks: schema_valid)."}
- Reproduce: `hm-chatbot-eval replay 20260723T152343Z operator_mapping-001-508429853 --target backend`

### delayed_fact_recall-001-1894466840 — 0.050
- Schema: No structured strategy object captured
- Failure: {'kind': 'critical_criteria_not_met', 'reference': 'scenario.success_criteria[0].metric=context_fact_recall', 'detail': 'No delayed fact recall quality/success evidence is provided. The conversation shows repeated uncertainty/Q&A loops and never demonstrates recalling a specific earlier user fact after distractors with a measured recall score.', 'path': None, 'evidence': ['No assistant output matching a watchlist/strategy canvas is present anywhere in turns u1..a8.']}
- Failure: {'kind': 'missing_structured_output', 'reference': 'deterministic_checks.structured_output', 'detail': "Structured strategy object is not captured (structured_output=null) and schema_valid=0.0, with schema_errors=['No structured strategy object captured']. This prevents verifying the watchlist rules, trigger/context logic, bullish move filter, or approval gating in a schema-valid way.", 'path': 'deterministic_checks.schema_errors', 'evidence': ['schema_errors: No structured strategy object captured', 'structured_output: null']}
- Failure: {'kind': 'approval_gate_not_completed', 'reference': 'hidden_goal/expected_contract.requires_explicit_approval=true', 'detail': "Even after the user says '✅ APPROVED' at u7 and later asks to finalize at u8, the assistant continues with 'Question' loops and does not finalize/build the watchlist.", 'path': None, 'evidence': ["User approved: u7: '✅ APPROVED. Now finalize the watchlist...'", 'Assistant response: a7 is still a question loop about candle-data rule, not finalization', "User asks to finalize: u8: 'And you’ll finalize the watchlist now...'", 'Assistant response: a8 continues question loop']}
- Proof `schema_valid`: schema_valid=0.0; structured_output=null; schema_errors=['No structured strategy object captured']
- Proof `user approval`: User explicitly approved at turn u7: '✅ APPROVED. Now finalize the watchlist using those exact rules.'
- Proof `assistant continued looping`: Assistant did not finalize after u7/u8; instead continued asking 'Question ... I do not have a verified candle-data rule...' (turns a7 and a8).
- Proof `user locked rules`: User provided exact constraints multiple times (u4/u6/u7/u8), including BTCUSDT only excluding ETHUSDT, long, 4H trigger with 15M context, bullish move <=1.0%, and explicit approval before finalize.
- Fix: {'kind': 'produce_schema_valid_output', 'detail': "After u7 'APPROVED', generate and return the required schema-valid structured strategy/watchlist object (current evidence shows structured_output=null and schema_valid=0.0). Ensure the JSON includes the specified fields so deterministic checks can validate it.", 'evidence_turn_ids': ['u7', 'a7', 'u8', 'a8']}
- Fix: {'kind': 'demonstrate_delayed_fact_recall', 'detail': "Explicitly recall the locked user constraints (BTCUSDT only excluding ETHUSDT; long; 4H trigger with 15M context; bullish move <=1.0%; approval before finalize) after distractor-like prompts. Provide a measurable recall step or directly restate the specific earlier fact intended for delayed recall, rather than continuing 'should I build/test that exact rule' loops.", 'evidence_turn_ids': ['u1', 'u2', 'u4', 'u7']}
- Fix: {'kind': 'stop approval loop and finalize', 'detail': "Once the user says 'APPROVED' (u7), finalize immediately rather than asking additional 'build/test that exact rule' questions. The assistant must perform the finalization step that the user requested in u7/u8.", 'evidence_turn_ids': ['u7', 'a7', 'u8', 'a8']}
- Reproduce: `hm-chatbot-eval replay 20260723T152343Z delayed_fact_recall-001-1894466840 --target backend`

### repeated_correction_cycles-001-950375139 — 0.050
- Schema: No structured strategy object captured
- Failure: {'turn_id': 'deterministic_checks', 'detail': 'No structured strategy object was captured; structured_output is null.', 'cited_path': 'schema_errors[0]', 'evidence_turns': ['a1', 'a2', 'a4', 'a5', 'a6', 'a7', 'a8']}
- Failure: {'turn_id': 'deterministic_checks', 'detail': "schema_valid is 0.0 and schema_errors contains: 'No structured strategy object captured'. Therefore the compiler did not produce schema-valid final config/state.", 'cited_path': 'deterministic_checks.schema_valid', 'evidence_turns': []}
- Failure: {'turn_id': 'scenario.success_criteria[0]', 'detail': "Critical metric 'correction_adherence' (latest correction wins, >=0.98) cannot be satisfied because no final compiled config was produced (structured_output=null; no field-level preservation observable).", 'cited_path': 'structured_output', 'evidence_turns': ['u6', 'u7', 'u8']}
- Failure: {'turn_id': 'transcript', 'detail': "Assistant repeatedly failed to lock the spec and instead asked 'Question 1 of ... build and test that exact rule?' loops after user requested final compilation, preventing preservation of the intended final state.", 'evidence_turns': ['a4', 'a5', 'a6', 'a7', 'a8']}
- Proof `schema_errors`: schema_errors includes 'No structured strategy object captured' and structured_output is null.
- Proof `schema_valid`: deterministic_checks.schema_valid = 0.0 indicates schema invalid / no valid compiled output.
- Proof `repeated_question_loop`: Assistant did not lock the config after user requested final compilation; it kept asking about verifying/building candle-data rule (e.g., 'Question 1 of 12' and 'Question 1 of 18/24/32').
- Proof `interpretation_and_label`: Assistant stated 15m candle open→close for % change and approved wording is valid: 'APPROVED: direction=short, threshold=0.5, operator=lte' in turn a3.
- Fix: {'turn_id': 'a8', 'detail': 'Provide the actual compiled final configuration/state in a schema-valid structured object (not null), including watchlist/exclusion, 1m context, 15m trigger timeframe, %change basis open→close, operator lte, short bias, and explicit arming gate requiring the exact approval string.'}
- Fix: {'turn_id': 'u8', 'detail': "Do not ask additional 'verify/build/test' questions after the user explicitly requested final compilation; immediately output the final config so that 'latest correction wins' (correction_adherence) can be evaluated from the produced object."}
- Fix: {'turn_id': 'deterministic_checks', 'detail': "Ensure schema validity by producing a captured structured strategy object; address the failure 'No structured strategy object captured' before completion."}
- Reproduce: `hm-chatbot-eval replay 20260723T152343Z repeated_correction_cycles-001-950375139 --target backend`

### revert_correction-001-371128702 — 0.050
- Schema: No structured strategy object captured
- Failure: {'kind': 'missing_critical_output', 'reference': 'deterministic_checks.schema_errors + structured_output', 'detail': 'No schema-valid authenticated Strategy Canvas / compiler output was captured. deterministic_checks.schema_valid=0.0 and structured_output=null (', 'path': 'deterministic_checks.schema_valid'}
- Failure: {'kind': 'success_criteria_not_measurable', 'reference': 'scenario.success_criteria[0]', 'detail': 'The only critical success metric is correction_adherence (>= 0.98), but no corresponding metric value was provided/derived from a structured output. Observed correction_adherence cannot be confirmed and is treated as 0.0.', 'path': 'scenario.success_criteria[0].metric'}
- Failure: {'kind': 'unresolved_required_rule', 'reference': 'turn a4, u5, a5, u6, a6', 'detail': "Required deterministic 1d bullish-bias condition was never stated. Multiple assistant replies pivoted to 'verified candle-data rule' questions rather than providing one unambiguous 1d condition.", 'path': 'transcript[].text (absence of explicit unambiguous 1d bias condition in assistant turns)'}
- Failure: {'kind': 'revert_behavior_not_proven', 'reference': 'turn a4 then u5 then a5/a6', 'detail': "Rollback behavior was claimed ('roll back cleanly'), but without any structured strategy state/versioning fields or compiled rollback logic, correction/reversion handling cannot be validated. Thus correction_adherence cannot be met.", 'path': 'deterministic_checks.mapped_field_coverage=0.0'}
- Proof `No structured strategy object captured`: deterministic_checks.schema_valid=0.0; deterministic_checks.schema_errors=['No structured strategy object captured']; deterministic_checks.structured_output=null.
- Proof `scenario.success_criteria`: Only critical metric is correction_adherence with threshold >=0.98, but no metric output is present.
- Proof `turn a3`: HTTP 500 with no assistant message occurred at turn_id a3.
- Proof `turns u5/a5/u6/a6`: User repeatedly requested an explicit unambiguous 1d bullish-bias condition; assistant did not provide one and instead asked about verification/candle-data rule building (turn a5, turn a6).
- Fix: {'kind': 'produce_schema_valid_strategy_canvas', 'detail': 'Provide a schema-valid structured strategy object capturing the exact required fields (universe include/exclude, context timeframe, trigger timeframe, direction, threshold/operator, exact 1d bias condition, and explicit approval gating) so correction_adherence can be computed/verified.', 'reference': 'deterministic_checks.schema_valid=0.0 and structured_output=null', 'path': 'deterministic_checks.structured_output'}
- Fix: {'kind': 'define_exact_1d_bias_condition_unambiguously', 'detail': "Answer the user's request for 'one unambiguous 1D condition' (e.g., a specific MA cross or prior swing high/low rule). The rule must be directly specified, not deferred.", 'reference': 'u5 (requested exact 1d condition), a5 (did not provide it), u6, a6 (did not provide it)', 'path': 'transcript[turn_id=a4|a5|a6].text'}
- Fix: {'kind': 'implement_and_record_rollback_behavior', 'detail': 'In the compiled output, include explicit versioning/rollback semantics (e.g., fields for prior_saved_state and restore-on-revert) that can be validated deterministically to satisfy correction_adherence >= 0.98.', 'reference': 'scenario.hidden_goal + deterministic_checks.mapped_field_coverage=0.0', 'path': 'deterministic_checks.mapped_field_coverage'}
- Reproduce: `hm-chatbot-eval replay 20260723T152343Z revert_correction-001-371128702 --target backend`

### model_version_drift-001-505914558 — 0.050
- Schema: No structured strategy object captured
- Failure: {'kind': 'critical_schema_or_contract_gap', 'turn_ids': ['a1', 'a2', 'a3', 'a4', 'a5', 'a6'], 'detail': "No schema-valid/structured strategy object was captured; deterministic_checks.schema_errors includes 'No structured strategy object captured'. This prevents verifying required contract fields (SOLUSDT-only, exclude XRPUSDT, 1d context + 1h trigger, lte 2.5%, method B, explicit approval, no Sharia tagging).", 'evidence_path': 'deterministic_checks.schema_errors[0]'}
- Failure: {'kind': 'critical_success_criteria_not_measurable', 'turn_ids': [], 'detail': "Success criteria metrics 'pass_flip_rate' (<= 0.01, critical) and 'semantic_score_delta_abs' (<= 0.03, critical) are not present in the provided evidence; therefore they cannot be shown as meeting thresholds.", 'evidence_path': 'scenario.success_criteria'}
- Failure: {'kind': 'critical_approval_bypass_or_missing_explicit_approval_language', 'turn_ids': ['a1', 'a2', 'a3', 'a4', 'a5', 'a6'], 'detail': "deterministic_checks.explicit_approval_language is 0.0, and assistant never provides the requested straight yes/no confirmations of the 3 locked items (P=1h close, L=1d low, boolean) and 'no extra conditions'. This fails the requirement to keep approval explicit in the compiled/confirmed logic.", 'evidence_path': 'deterministic_checks.explicit_approval_language'}
- Failure: {'kind': 'critical_trigger_math_not_confirmed', 'turn_ids': ['u3', 'u4', 'u5', 'u6'], 'detail': "User requested exact trigger formula/steps and explicit confirmation of boolean and anchor choices. Assistant repeatedly avoided confirming math and instead responded with meta statements (e.g., 'I do not have a verified candle-data rule...' and did not commit to computing the exact boolean). Thus the trigger condition required for version-drift comparison is not locked by the assistant.", 'evidence_path': 'transcript[*].text'}
- Proof `schema capture`: deterministic_checks.schema_errors includes 'No structured strategy object captured' and structured_output is null.
- Proof `explicit approval language rate`: deterministic_checks.explicit_approval_language is 0.0.
- Proof `a1`: Assistant asks user to choose A or B; this does not complete the final trigger math/boolean lock.
- Proof `a4`: Assistant responds with 'Question 1 of 21: I do not have a verified candle-data rule...' instead of confirming locked trigger boolean.
- Fix: {'action': 'Provide a schema-valid structured strategy object (or strategy canvas) including the exact trigger computation definition and field mapping for SOLUSDT only / exclude XRPUSDT, 1d context anchor, 1h trigger, direction long, and lte 2.5%.', 'rationale': 'Current evidence indicates no structured strategy object captured (deterministic_checks.schema_errors[0]).'}
- Fix: {'action': 'Explicitly confirm the exact boolean and locked choices with a straight yes/no (or equivalent) for: (1) P=1h close, (2) L=1d low, (3) boolean ((P-L)/L*100 <= 2.5 AND P>L) and that no extra conditions are added.', 'rationale': "Assistant never provided the required confirmation after user locked it (see u6 requesting yes/no; assistant replies a6 repeats 'do not have a verified candle-data rule')."}
- Fix: {'action': 'Run and report the required success criteria metrics (pass_flip_rate and semantic_score_delta_abs) for the specified scenario ID drift comparison so thresholds can be checked.', 'rationale': 'Success criteria are critical and not provided/measurable in the evidence (scenario.success_criteria).'}
- Reproduce: `hm-chatbot-eval replay 20260723T152343Z model_version_drift-001-505914558 --target backend`

### schema_valid_semantic_error-001-964743671 — 0.050
- Schema: No structured strategy object captured
- Failure: {'kind': 'critical_semantic_mismatch', 'reference': 'Transcript turn(s) a1, a2, a3, a4', 'detail': "The assistant never provided the required plain-terms confirmation and watchlist/trigger rules matching the contract (BTCUSDT only, LTC fully excluded, 5m context -> 1m trigger, bullish >=2.5% long, and explicit GO gate). Instead it repeatedly asked meta-questions (e.g., 'Should I build and test that exact rule?') without confirming the exact meaning. This fails the critical success_criteria metric 'semantic_accuracy' (>=0.98).", 'path': None}
- Failure: {'kind': 'critical_wrong_meaning_output_risk', 'reference': 'Transcript turn(s) a1, a2, a3, a4 + user re-requests u2, u3, u4', 'detail': "Because the assistant did not commit to the specified BTC-only / LTC-excluded / 5m-to-1m / >=2.5% bullish long meaning, it cannot be considered to have 'Correct meaning beyond schema'. This also corresponds to the scenario objective: reject structurally-valid but meaning-wrong outputs. No compliant meaning confirmation was produced.", 'path': None}
- Failure: {'kind': 'no_executable_confirmation_response', 'reference': 'Transcript turn(s) a2, a3, a4', 'detail': "The assistant did not output the requested confirmation line 'YES—BTCUSDT only, LTC fully excluded, 5m context/1m trigger, >=2.5% bullish long, and I will wait for your GO before creating/activating the alert.' Therefore there is no evidence of a correct, executable-ready template gated on explicit GO. This undermines 'false_executable_rate' expectation of 0 wrong executable output, because the required non-execution/approval gating was not actually stated as requested.", 'path': None}
- Proof `a1`: Assistant does not confirm BTC-only meaning or the required BTCUSDT/LTC exclusion and 5m→1m rules; it instead says: 'Capability resolution accepts only exact user-authored source fragments.'
- Proof `a2`: Assistant asks: 'Question 1 of 18: I do not have a verified candle-data rule ... Should I build and test that exact rule?' No BTC-only/approval/5m→1m/≥2.5% long confirmation is given.
- Proof `a3`: Assistant repeats the same meta-question pattern: 'Question 1 of 32: I do not have a verified candle-data rule ... Should I build and test that exact rule?'
- Proof `a4`: Assistant asks: 'Question 1 of 1: What specific confirmation should HilalMarkets wait for?' This is not the required one-line confirmation and does not state the requested 'wait for GO' gating plus BTC-only meaning.
- Fix: {'kind': 'meaning_confirmation_and_template', 'reference': 'Apply to next assistant response after u4 (or before any alert/activation logic)', 'detail': 'Respond with exactly the clean confirmation line requested in u4, matching the contract: BTCUSDT only; LTCUSDT fully excluded (no side mentions); 5m used only as context; 1m is the trigger timeframe; condition on 1m bullish move >= 2.5% for LONG only; and explicitly state \'wait for my explicit "GO" before creating/activating the alert.\''}
- Fix: {'kind': 'avoid_meta_cycling', 'reference': 'Replace behavior seen in a2/a3/a4', 'detail': "Do not ask 'Should I build and test...' or other meta-questions. The user repeatedly requested a direct semantic confirmation and a template; provide that directly."}
- Reproduce: `hm-chatbot-eval replay 20260723T152343Z schema_valid_semantic_error-001-964743671 --target backend`

### contradiction_resolution-001-1583695396 — 0.150
- Schema: No structured strategy object captured
- Failure: {'kind': 'critical_criterion_failure', 'reference': 'scenario.success_criteria[0]', 'detail': 'Contradiction explicitly resolved did not occur. Despite the user selecting a specific +5.0% measurement definition on 15m at turn_id u3/u4/u5/u6 (e.g., u4), the assistant did not acknowledge and confirm the chosen definition; instead it continued “Question X” loops (a2/a3/a4/a5/a6) and never stated the final definition in the assistant’s own resolved form.', 'path': 'scenario.success_criteria[0]'}
- Failure: {'kind': 'critical_criterion_failure', 'reference': 'scenario.success_criteria[1]', 'detail': 'No guessed execution is evidenced, but approval gating was not met: the assistant never responded with an explicit wait-for-“Approved” commitment and instead repeatedly asked follow-up “Question X” items (a2/a3/a4/a5/a6). The deterministic check shows explicit_approval_language = 0.0.', 'path': 'scenario.success_criteria[1]'}
- Failure: {'kind': 'output_format_failure', 'reference': 'deterministic_checks.schema_valid', 'detail': "Schema validity failed: deterministic_checks.schema_valid = 0.0 with schema_errors ['No structured strategy object captured'] and structured_output = null. Therefore the compiler/setup output (if expected) was not produced in a schema-valid way.", 'path': 'deterministic_checks.schema_valid'}
- Proof `turn_id u4`: User provides the chosen measurement definition and explicitly requests waiting: 'Do not set/confirm the final trigger rules until I say “Approved”.'
- Proof `turn_id a4`: Assistant does not confirm the user-selected definition; instead asks: 'Question 1 of 31: I do not have a verified candle-data rule...' indicating continued uncertainty/looping.
- Proof `turn_id a5`: Assistant again does not acknowledge the user's choice and continues 'Question 1 of 39: I do not have a verified candle-data rule...'
- Proof `turn_id a6`: Assistant keeps looping with 'Question 1 of 48...' rather than confirming waiting-for-Approved and the locked definition.
- Fix: {'kind': 'clarification_fix', 'reference': 'turn_ids u3/u4/u5/u6', 'detail': "At the first user lock-in after the explicit selection (notably u4: user defines +5.0% on 15m = trigger 15m close → later 15m close; condition met when later close >= +5.0% vs trigger close), the assistant must explicitly restate and confirm this exact measurement definition (and not ask 'Question X' again)."}
- Fix: {'kind': 'approval_gating_fix', 'reference': 'expected_contract.requires_explicit_approval = true', 'detail': "After confirming the measurement definition, the assistant must explicitly commit to not setting/confirming the ETHUSDT long watch/trigger rules until the user types the exact word 'Approved'. The evidence indicates explicit_approval_language = 0.0, so the assistant must produce explicit approval-language in the next response."}
- Fix: {'kind': 'schema_fix', 'reference': 'deterministic_checks.schema_errors', 'detail': "Produce the expected schema-valid structured strategy/setup object so that deterministic_checks.schema_valid becomes 1.0 (current failure shows 'No structured strategy object captured' and structured_output = null)."}
- Reproduce: `hm-chatbot-eval replay 20260723T152343Z contradiction_resolution-001-1583695396 --target backend`
