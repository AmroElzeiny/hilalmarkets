"""The nine OI-3 validation cases.

Case numbers match the phase brief so the two can be read side by side. Every secret in
this file is invented and matches nothing real.

Case 8 is a conditional: "under P4 FAIL, confirm skills refuse cross-process and
pre-restart conclusions". P4 **passes** at this commit — the phase 5 closeout landed, and
`tests/unit/test_durable_metrics.py` proves restart survival and cross-process
aggregation. So the test asserts the condition that actually holds: that durability is
real, and that the skills are therefore allowed to reason across processes. Writing a
test for a restriction that does not apply would be theatre.
"""

from __future__ import annotations

import pytest

from hm_oi.builder_permissions import builder_policy
from hm_oi.evidence import (
    EVIDENCE_SOURCES,
    Environment,
    Evidence,
    EvidenceRefused,
    Pseudonymiser,
    assert_single_environment,
    collect,
    refuse_live_production,
    sanitize,
)
from hm_oi.investigation import (
    Alternative,
    Claim,
    Confidence,
    Diagnosis,
    Insufficient,
    InvestigationRefused,
    ProblemKind,
    SupportKind,
)
from hm_oi.permissions import Decision

FAKE_SEED = (
    "abandon ability able about above absent absorb abstract absurd abuse access accident"
)
FAKE_KEY = "sk-proj0000FAKE1111NOTREAL2222abcdefghijklmn"
FAKE_PLAN_TEXT = "Alert me when BTC RSI goes under 30 and the close is below the open"


@pytest.fixture
def anon() -> Pseudonymiser:
    return Pseudonymiser(salt=b"deterministic-salt-for-tests")


def _ev(source: str, payload, environment: Environment, anon: Pseudonymiser) -> Evidence:
    return collect(source, payload, environment, pseudonymiser=anon)


# ----------------------------------------------------------------------------------
# Case 1 — a seeded provider incident is identified as infrastructure, with metrics.
# ----------------------------------------------------------------------------------


def test_case_1_a_429_burst_then_circuit_open_is_infrastructure(anon) -> None:
    circuit = _ev(
        "provider.circuit",
        {"status_429": 214, "status_5xx": 0, "circuit": "open", "opened_after": 214},
        Environment.STAGING,
        anon,
    )
    stage = _ev(
        "ai.failure_stage",
        {"failure_class": "PROVIDER_FAILURE", "owner": "PROVIDER", "count": 214},
        Environment.STAGING,
        anon,
    )

    diagnosis = Diagnosis.build(
        question="Why did Setup Chat turns start failing at 14:02?",
        kind=ProblemKind.INFRASTRUCTURE,
        summary="The provider rate-limited us 214 times, and the circuit opened.",
        claims=(
            Claim("429 responses rose from 0 to 214 in six minutes.", (circuit,)),
            Claim("Every failed turn is typed PROVIDER_FAILURE.", (stage,)),
        ),
        alternatives=(
            Alternative(
                "A prompt change made turns longer and tripped a token limit.",
                ruled_out_by="the failures are 429, not 400, and token counts are flat",
            ),
        ),
        falsified_by="a 429 count that does not line up with the failed turn count",
        confidence=Confidence.HIGH,
    )

    assert diagnosis.kind is ProblemKind.INFRASTRUCTURE
    assert diagnosis.environment is Environment.STAGING
    assert "provider.circuit" in diagnosis.render()
    assert all(claim.evidence for claim in diagnosis.claims)


def test_case_1_a_circuit_that_never_closed_is_application_logic(anon) -> None:
    """The distinction the skill exists to make: recovery is our code, not theirs."""

    circuit = _ev(
        "provider.circuit",
        {"status_429": 0, "circuit": "open", "provider_healthy_since_minutes": 45},
        Environment.STAGING,
        anon,
    )
    diagnosis = Diagnosis.build(
        question="Why are turns still failing after the provider recovered?",
        kind=ProblemKind.APPLICATION,
        summary="The provider recovered 45 minutes ago and the circuit never closed.",
        claims=(Claim("The circuit is open with zero recent 429s.", (circuit,)),),
        alternatives=(
            Alternative(
                "The provider is still failing silently.",
                ruled_out_by="its health endpoint has reported healthy for 45 minutes",
            ),
        ),
        falsified_by="evidence that the provider is still returning errors we do not count",
        confidence=Confidence.MEDIUM,
    )
    assert diagnosis.kind is ProblemKind.APPLICATION


# ----------------------------------------------------------------------------------
# Case 2 — a cost anomaly, identified without naming anybody.
# ----------------------------------------------------------------------------------


def test_case_2_a_cost_spike_names_no_user(anon) -> None:
    usage = _ev(
        "ai.usage",
        {
            "user_id": "0f9c2a44-1111-2222-3333-444455556666",
            "email": "someone@example.com",
            "input_tokens": 4_100_000,
            "estimated_cost_usd": 41.0,
            "turns": 900,
        },
        Environment.PRODUCTION_SNAPSHOT,
        anon,
    )

    rendered = str(usage.payload)
    assert "someone@example.com" not in rendered
    assert "0f9c2a44" not in rendered
    assert usage.payload["user_id"].startswith("user-")
    assert usage.payload["email"].startswith("[WITHHELD")

    diagnosis = Diagnosis.build(
        question="Why did spend rise 6x on Tuesday?",
        kind=ProblemKind.APPLICATION,
        summary="Turns rose 1.2x while cost per turn rose 5x. One account drove it.",
        claims=(Claim("Cost per turn rose from $0.009 to $0.045.", (usage,)),),
        alternatives=(
            Alternative(
                "Ordinary demand growth.",
                ruled_out_by="turn count rose only 1.2x while cost rose 6x",
            ),
        ),
        falsified_by="a tier distribution showing no shift toward the deep tier",
        confidence=Confidence.MEDIUM,
        recommendation="A person with access must identify the account; this tool cannot.",
    )
    whole = diagnosis.render()
    assert "someone@example.com" not in whole
    assert "0f9c2a44" not in whole


def test_case_2_the_same_id_is_the_same_pseudonym_within_one_investigation(anon) -> None:
    first = _ev("ai.usage", {"user_id": "abc"}, Environment.STAGING, anon)
    second = _ev("scanner.runs", {"user_id": "abc"}, Environment.STAGING, anon)
    assert first.payload["user_id"] == second.payload["user_id"]


def test_case_2_a_different_investigation_cannot_join_the_names() -> None:
    one = Pseudonymiser()
    two = Pseudonymiser()
    assert one.pseudonym("user_id", "abc") != two.pseudonym("user_id", "abc")


# ----------------------------------------------------------------------------------
# Case 3 — an application-logic Setup Chat failure must not be blamed on the model.
# ----------------------------------------------------------------------------------


def test_case_3_a_compiler_refusal_is_application_logic_not_the_model(anon) -> None:
    stage = _ev(
        "ai.failure_stage",
        {"failure_class": "CAPABILITY_UNSUPPORTED", "owner": "APPLICATION", "count": 37},
        Environment.LOCAL,
        anon,
    )
    routing = _ev(
        "ai.routing",
        {"tier": "deep", "reason": "setup_chat_semantics", "turns": 37},
        Environment.LOCAL,
        anon,
    )

    diagnosis = Diagnosis.build(
        question="Why did 37 turns fail to build a rule the user clearly described?",
        kind=ProblemKind.APPLICATION,
        summary=(
            "The model produced a correct draft each time. The compiler refused it "
            "because two modules disagree about what the operator phrase means."
        ),
        claims=(
            Claim("Every failure is typed CAPABILITY_UNSUPPORTED, owner APPLICATION.", (stage,)),
            Claim("All 37 turns routed to the deep tier and returned a draft.", (routing,)),
        ),
        alternatives=(
            Alternative(
                "The model misread the user's wording.",
                ruled_out_by="the draft it returned matches the user's stated threshold",
            ),
            Alternative(
                "The provider truncated the response.",
                ruled_out_by="no provider errors and no truncation flags in the window",
            ),
        ),
        falsified_by="a draft that does not contain the threshold the user gave",
        confidence=Confidence.HIGH,
    )
    assert diagnosis.kind is ProblemKind.APPLICATION
    assert diagnosis.kind is not ProblemKind.SEMANTIC


# ----------------------------------------------------------------------------------
# Case 4 — ambiguity must return INSUFFICIENT EVIDENCE, not a guess.
# ----------------------------------------------------------------------------------


def test_case_4_ambiguous_evidence_returns_insufficient() -> None:
    answer = Insufficient(
        question="Did the deploy at 13:55 cause the latency rise?",
        have=("latency rose at 14:00", "a deploy landed at 13:55"),
        missing=(
            "per-route latency, to tell whether the rise is in the changed route",
            "the same measurement from before the previous deploy, as a control",
        ),
        environment=Environment.STAGING,
    )
    assert answer.verdict == "INSUFFICIENT EVIDENCE"
    assert "INSUFFICIENT EVIDENCE" in answer.render()
    assert answer.missing


def test_case_4_a_diagnosis_with_no_claims_is_refused() -> None:
    with pytest.raises(InvestigationRefused, match="no claims"):
        Diagnosis.build(
            question="q",
            kind=ProblemKind.APPLICATION,
            summary="s",
            claims=(),
            alternatives=(Alternative("other", ruled_out_by="something that rules it out"),),
            falsified_by="something that would falsify this clearly",
            confidence=Confidence.LOW,
        )


def test_case_4_a_claim_with_no_evidence_is_refused(anon) -> None:
    with pytest.raises(InvestigationRefused, match="no evidence"):
        Claim("the provider failed", ())


def test_case_4_time_correlation_alone_cannot_be_high_confidence(anon) -> None:
    ev = _ev("metrics.durable", {"latency_p95_ms": 900}, Environment.STAGING, anon)
    with pytest.raises(InvestigationRefused, match="hypothesis"):
        Diagnosis.build(
            question="Did the deploy cause it?",
            kind=ProblemKind.APPLICATION,
            summary="Latency rose just after the deploy.",
            claims=(Claim("Latency rose at 14:00.", (ev,), SupportKind.CORRELATION),),
            alternatives=(
                Alternative("Traffic rose.", ruled_out_by="request counts were flat"),
            ),
            falsified_by="a latency rise on a route the deploy did not touch",
            confidence=Confidence.HIGH,
        )


def test_case_4_an_alternative_must_actually_be_ruled_out() -> None:
    with pytest.raises(InvestigationRefused, match="not ruled out"):
        Alternative("Something else happened.", ruled_out_by="no")


def test_case_4_a_diagnosis_must_say_what_would_falsify_it(anon) -> None:
    ev = _ev("metrics.durable", {"x": 1}, Environment.LOCAL, anon)
    with pytest.raises(InvestigationRefused, match="falsif|wrong"):
        Diagnosis.build(
            question="q",
            kind=ProblemKind.APPLICATION,
            summary="s",
            claims=(Claim("a thing happened", (ev,)),),
            alternatives=(Alternative("other", ruled_out_by="a real reason here"),),
            falsified_by="dunno",
            confidence=Confidence.LOW,
        )


def test_case_4_undetermined_kind_is_refused(anon) -> None:
    ev = _ev("metrics.durable", {"x": 1}, Environment.LOCAL, anon)
    with pytest.raises(InvestigationRefused, match="not determined"):
        Diagnosis.build(
            question="q",
            kind=ProblemKind.UNDETERMINED,
            summary="s",
            claims=(Claim("a thing happened", (ev,)),),
            alternatives=(Alternative("other", ruled_out_by="a real reason here")),
            falsified_by="something specific that would falsify it",
            confidence=Confidence.LOW,
        )


# ----------------------------------------------------------------------------------
# Case 5 — redaction. Nothing sensitive reaches an agent context or a report.
# ----------------------------------------------------------------------------------


def test_case_5_a_seeded_log_line_loses_its_secrets(anon) -> None:
    evidence = _ev(
        "logs.application",
        {
            "message": f"user pasted {FAKE_SEED} and key {FAKE_KEY}",
            "strategy_text": FAKE_PLAN_TEXT,
            "email": "person@example.com",
            "prompt": "You are a helpful assistant...",
            "user_id": "u-1",
        },
        Environment.LOCAL,
        anon,
    )
    body = str(evidence.payload)
    assert FAKE_SEED not in body
    assert FAKE_KEY not in body
    assert FAKE_PLAN_TEXT not in body
    assert "person@example.com" not in body
    assert "helpful assistant" not in body
    assert evidence.payload["user_id"].startswith("user-")


@pytest.mark.parametrize(
    "field",
    ("prompt", "completion", "conversation", "transcript", "watchlist", "strategy_text",
     "email_body", "reasoning", "chain_of_thought"),
)
def test_case_5_raw_text_fields_are_withheld_whole(field: str, anon) -> None:
    """Refused, not cleaned. The sensitive part of a sentence is its meaning."""

    cleaned = sanitize({field: FAKE_PLAN_TEXT}, pseudonymiser=anon)
    assert cleaned[field].startswith("[WITHHELD")
    assert FAKE_PLAN_TEXT not in str(cleaned)


@pytest.mark.parametrize(
    "field", ("sharia_status", "halal", "haram", "shariah_ruling", "compliance_status")
)
def test_case_5_a_sharia_status_is_never_carried_as_fact(field: str, anon) -> None:
    cleaned = sanitize({field: "halal"}, pseudonymiser=anon)
    assert cleaned[field].startswith("[WITHHELD")


def test_case_5_a_sharia_claim_in_prose_is_withheld(anon) -> None:
    cleaned = sanitize({"note": "BTC is halal per the screen"}, pseudonymiser=anon)
    assert "WITHHELD" in cleaned["note"]


def test_case_5_a_leak_that_survives_sanitization_refuses_the_evidence(anon) -> None:
    """If the sanitizer has a hole, stop - do not ship the evidence anyway."""

    class Sneaky:
        def __repr__(self) -> str:
            return FAKE_KEY

    with pytest.raises(EvidenceRefused, match="survived sanitization"):
        collect("logs.application", {"obj": Sneaky()}, Environment.LOCAL, pseudonymiser=anon)


# ----------------------------------------------------------------------------------
# Case 6 — boundary tests. All four must fail.
# ----------------------------------------------------------------------------------

CASE_6 = {
    "production database write": (
        "psql postgres://u:p@prod.example/db -c \"UPDATE users SET plan='pro'\"",
        "UPDATE operational_alert SET enabled = false",
        "DELETE FROM users WHERE id = 1",
    ),
    "feature flag change": (
        "AI_FEATURES_DISABLED=true",
        "LAUNCH_STAGE=public",
        "update the feature flag for production",
    ),
    "alert suppression": (
        "silence the alert for provider errors",
        "UPDATE operational_alert_delivery SET status = 'resolved'",
        "mute alert ai_failure_rate",
    ),
    "deploy": (
        "docker compose -f docker-compose.prod.yml up -d",
        "kubectl apply -f deploy.yaml",
        "systemctl restart hilalmarkets",
    ),
}


@pytest.mark.parametrize(
    "label,command",
    [(label, cmd) for label, cmds in CASE_6.items() for cmd in cmds],
)
def test_case_6_operational_boundaries_are_denied(label: str, command: str) -> None:
    verdict = builder_policy().evaluate(command)
    assert verdict.decision is Decision.DENY, (
        f"{label}: {command!r} was {verdict.decision.value} (rule {verdict.rule_id})"
    )


def test_case_6_a_live_production_connection_is_refused_in_code() -> None:
    with pytest.raises(EvidenceRefused, match="snapshot"):
        refuse_live_production(Environment.PRODUCTION_SNAPSHOT, "query")


@pytest.mark.parametrize("environment", (Environment.LOCAL, Environment.STAGING))
def test_case_6_local_and_staging_may_be_read_directly(environment: Environment) -> None:
    refuse_live_production(environment, "query")


def test_case_6_reading_operational_signals_is_still_allowed() -> None:
    """A policy that refuses everything is not a policy."""

    for command in (
        "curl -s http://localhost:8000/api/v1/admin/health",
        "git log --oneline -5",
        ".venv/Scripts/python -m pytest tests/unit -q",
    ):
        assert builder_policy().evaluate(command).decision is Decision.ALLOW, command


# ----------------------------------------------------------------------------------
# Case 7 — environment labelling. Mixed evidence is separated or refused.
# ----------------------------------------------------------------------------------


def test_case_7_mixed_environments_are_refused(anon) -> None:
    staging = _ev("metrics.durable", {"errors": 5}, Environment.STAGING, anon)
    production = _ev("metrics.durable", {"errors": 900}, Environment.PRODUCTION_SNAPSHOT, anon)
    with pytest.raises(EvidenceRefused, match="more than one environment"):
        assert_single_environment([staging, production])


def test_case_7_a_diagnosis_cannot_mix_environments(anon) -> None:
    staging = _ev("provider.circuit", {"status_429": 5}, Environment.STAGING, anon)
    production = _ev("ai.usage", {"turns": 900}, Environment.PRODUCTION_SNAPSHOT, anon)
    with pytest.raises(EvidenceRefused, match="more than one environment"):
        Diagnosis.build(
            question="q",
            kind=ProblemKind.INFRASTRUCTURE,
            summary="s",
            claims=(Claim("a", (staging,)), Claim("b", (production,))),
            alternatives=(Alternative("other", ruled_out_by="a real reason here")),
            falsified_by="something specific that would falsify it",
            confidence=Confidence.LOW,
        )


def test_case_7_every_conclusion_carries_its_environment(anon) -> None:
    ev = _ev("metrics.durable", {"x": 1}, Environment.PRODUCTION_SNAPSHOT, anon)
    diagnosis = Diagnosis.build(
        question="q",
        kind=ProblemKind.APPLICATION,
        summary="s",
        claims=(Claim("a thing happened", (ev,)),),
        alternatives=(Alternative("other", ruled_out_by="a real reason here"),),
        falsified_by="something specific that would falsify it",
        confidence=Confidence.LOW,
    )
    assert diagnosis.environment is Environment.PRODUCTION_SNAPSHOT
    assert "production_snapshot" in diagnosis.render()
    assert "production_snapshot" in ev.cite()


def test_case_7_evidence_with_no_environment_cannot_conclude() -> None:
    with pytest.raises(EvidenceRefused, match="no evidence"):
        assert_single_environment([])


# ----------------------------------------------------------------------------------
# Case 8 — the P4 condition. Durability is real at this commit.
# ----------------------------------------------------------------------------------


def test_case_8_durable_cross_process_metrics_exist() -> None:
    """P4 passes here, so the skills may reason across processes and restarts.

    If this import ever fails, P4 has regressed and every investigator skill must go
    back to declaring its evidence single-process and non-durable.
    """

    from ai_market_monitor.observability import durable_metrics

    assert hasattr(durable_metrics, "load_recorder")
    assert hasattr(durable_metrics, "flush_metrics_once")


def test_case_8_the_evidence_allowlist_names_the_durable_source() -> None:
    source = EVIDENCE_SOURCES["metrics.durable"]
    assert "durable_metrics" in source.produced_by
    assert "restart" in source.description or "process" in source.description


def test_case_8_a_skill_can_cite_where_a_signal_is_produced(anon) -> None:
    ev = _ev("metrics.durable", {"x": 1}, Environment.LOCAL, anon)
    assert "durable_metrics.py::load_recorder" in ev.cite()


# ----------------------------------------------------------------------------------
# The allowlist itself.
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    (
        "ai_market_monitor.db",
        "production.postgres",
        "redis://prod",
        "customers",
        "conversations",
        "",
    ),
)
def test_anything_off_the_allowlist_is_refused(source: str, anon) -> None:
    with pytest.raises(EvidenceRefused, match="not an allowed evidence source"):
        collect(source, {"x": 1}, Environment.LOCAL, pseudonymiser=anon)


@pytest.mark.parametrize("key", sorted(EVIDENCE_SOURCES))
def test_every_allowed_source_says_where_it_comes_from(key: str) -> None:
    source = EVIDENCE_SOURCES[key]
    assert source.produced_by.strip(), f"{key} does not say what produces it"
    assert len(source.description) > 20
    assert source.kind in {"metric", "endpoint", "log", "record"}
