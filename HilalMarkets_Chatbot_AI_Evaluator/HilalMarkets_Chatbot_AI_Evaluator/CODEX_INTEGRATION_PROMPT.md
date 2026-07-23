Integrate the `HilalMarkets_Chatbot_AI_Evaluator/` package at the repository root into the existing HilalMarkets project. Target only the authenticated **AI Setup Chat / Watchlist Builder assistant and its Strategy Canvas compilation flow**; explicitly exclude the public landing-page support agent.

Inspect the current code before changing anything. Bind the evaluator to the real chat-session creation, message, interpretation, compile/preview, approval-readiness, and Canvas payload paths. Reuse the existing authenticated test-user factory and application services directly where possible; keep the generic HTTP and Playwright adapters as black-box fallbacks. Export the exact current validated strategy DSL JSON Schema to a stable test path and create a canonical field map for symbol/universe, timeframe, direction, thresholds, operators, nested AND/OR groups, exclusions, alert rules, assumptions, confidence, unsupported/provider-required capabilities, approval state, version/hash, and Canvas nodes/edges. Do not weaken, duplicate, or bypass production validation.

Add a **test-environment-only** fault adapter at the LLM boundary, disabled and impossible to enable in production, supporting: `timeout_once`, `429_once`, `empty_once`, `invalid_json_once`, `partial_json_once`, and `stream_disconnect_once`. Select it only through the evaluator header/config; prove production startup rejects fault mode. Add deterministic target-version selection for evals without exposing it to customers, so identical scenarios can compare model/prompt versions.

Map stable Playwright `data-testid` selectors for the AI Setup Chat input/send/new-chat, assistant messages, structured preview, validation errors, assumptions, approval button, and Canvas nodes/groups. Capture the underlying chat/compile API response so UI and backend outputs can be compared. Ensure the UI adapter verifies the AI Setup Chat marker and rejects support-widget pages.

Merge only necessary dependencies into the project’s existing Python 3.12 tooling; preserve current formatting, typing, migrations, auth, tenant isolation, immutable strategy versions, explicit approval, deterministic capability authority, and fail-closed behavior. Add commands:

`python -m hm_chatbot_eval doctor`
`python -m hm_chatbot_eval run --mode smoke --target both`
`python -m hm_chatbot_eval run --mode full --target both --tests-per-topic 24 --judge-mode deferred`

Make CI run the deterministic unit suite and a small mock-backed smoke suite; keep real API/UI full runs manual or scheduled. Update `.env.example` without secrets, document exact setup, and execute Ruff, mypy, pytest, the evaluator doctor, and smoke run. Fix all integration failures. Do not leave TODOs, placeholders, mocked success paths, invented routes, or unverified claims. Finish with exact changed files, commands run, results, remaining environment-only requirements, and the generated report paths.
