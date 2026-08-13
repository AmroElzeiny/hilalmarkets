"""The adversarial QA harness, proved against its own nine validation cases.

Every test here answers "does the harness work", not "does the product work". The
product's answers are reported in ``docs/OI_ADVERSARIAL_QA_RUN.md``; a harness that
reported them by failing this suite would be red forever and would therefore be ignored,
which is the failure mode this separation exists to prevent.

The one exception is :func:`test_the_known_violation_ledger_is_exact`, which fails in both
directions on purpose: if a recorded defect is fixed, or a new one appears, somebody has
to look.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ai_market_monitor.core.copy_rules import (
    FORBIDDEN_CLAIM_PHRASES,
    FORBIDDEN_PRODUCT_PHRASES,
    customer_copy_sources,
)
from ai_market_monitor.core.launch_stage import (
    STAGE_EXPOSURE,
    LaunchStage,
    resolve_launch_stage,
)
from ai_market_monitor.core.product_boundaries import (
    EVALUATION_MODES,
    EvaluationMode,
    UnsupportedCapability,
    refuse,
)
from ai_market_monitor.services.ai_setup_evaluator_control import EVALUATOR_FAULTS
from hm_oi.conversation_source import ConversationSourceRefused, resolve_corpus
from hm_oi.qa_attacks import (
    ATTACKER_CLAIM_PHRASES,
    ATTACKER_DEPRECATED_PHRASES,
    BOUNDARY_ATTACKS,
    AttackMethod,
    FailureClass,
    Severity,
    attacks_requiring_faults,
    scan_for_claims,
)
from hm_oi.qa_corpus import (
    REQUIRED_SHAPES,
    ConversationInvariant,
    InvariantVerdict,
    load_adversarial_corpus,
    shapes_covered,
)
from hm_oi.qa_evidence import (
    EvidenceLeak,
    EvidenceRecord,
    EvidenceStore,
    FailureStage,
    Interaction,
    StateSnapshot,
)
from hm_oi.qa_findings import (
    BaselineSet,
    Confidence,
    Finding,
    FindingStatus,
    IncompleteFinding,
    PromotionRefused,
    RegressionCandidate,
    classify,
    dedupe,
    rank,
)
from hm_oi.qa_harness import (
    AttackStatus,
    BudgetExceeded,
    RunLimits,
    SpendCap,
    attacks_runnable_against,
    build_report,
)
from hm_oi.qa_target import (
    SUPPORTED_FAULTS,
    FaultInjectionUnsupported,
    TargetKind,
    TargetRefused,
    classify_target,
    require_fault_injection,
)
from tests.oi.adversarial_checks import evaluate

ROOT = Path(__file__).resolve().parents[2]
LEDGER = Path(__file__).resolve().parent / "adversarial_known_violations.json"


def _ledger() -> set[tuple[str, str]]:
    payload = json.loads(LEDGER.read_text(encoding="utf-8"))
    return {
        (str(item["case_id"]), str(item["invariant"]))
        for item in payload["violations"]
    }


def _finding(**overrides: object) -> Finding:
    """A complete finding, so a test can change one field and keep the rest valid."""

    values: dict[str, object] = {
        "finding_id": "TEST-001",
        "title": "A thing went wrong",
        "severity": Severity.HIGH,
        "failure_class": FailureClass.COPY,
        "confidence": Confidence.OBSERVED,
        "summary": "The page said something it must not say.",
        "evidence": "templates/hilal/example.html:12",
        "falsifying_evidence": "The phrase sits inside an explicit denial.",
        "reproduction": ".venv/Scripts/python -m pytest tests/oi -k example -q",
        "environment_label": "isolated_test (APP_ENV=test)",
        "dedupe_key": "copy:example",
    }
    values.update(overrides)
    return Finding(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------
# The corpus itself.
# ---------------------------------------------------------------------------------


def test_the_corpus_covers_every_required_conversational_shape() -> None:
    """All seventeen shapes, or the corpus gives false confidence about coverage."""

    cases = load_adversarial_corpus(ROOT)
    missing = REQUIRED_SHAPES - shapes_covered(cases)
    assert not missing, "shapes with no case: " + ", ".join(
        sorted(item.value for item in missing)
    )


def test_every_corpus_case_is_uniquely_identified_and_explained() -> None:
    cases = load_adversarial_corpus(ROOT)
    identifiers = [case.case_id for case in cases]
    assert len(set(identifiers)) == len(identifiers)
    for case in cases:
        assert case.prompt.strip(), f"{case.case_id} has no prompt"
        assert case.review_note.strip(), f"{case.case_id} has no review note"


def test_no_corpus_case_claims_to_grant_approval() -> None:
    """An adversarial corpus that contains a real approval cannot test for a false one."""

    for case in load_adversarial_corpus(ROOT):
        assert case.grants_approval is False, case.case_id


@pytest.mark.parametrize(
    "case", load_adversarial_corpus(ROOT), ids=lambda case: case.case_id
)
def test_every_invariant_is_evaluated_on_every_case(case: object) -> None:
    """Six verdicts per case, and never a silently skipped one."""

    results = evaluate(case)  # type: ignore[arg-type]
    assert {item.invariant for item in results} == set(ConversationInvariant)
    for item in results:
        assert item.detail.strip(), f"{item.invariant} gave no detail"


def test_approval_is_never_inferred_anywhere_in_the_corpus() -> None:
    """Validation case 3, and the invariant this whole product rests on.

    Not in the ledger and never allowed to be. Every other invariant has a recorded
    violation; if this one ever gets one, the answer is to stop, not to write it down.
    """

    offenders = [
        result
        for case in load_adversarial_corpus(ROOT)
        for result in evaluate(case)
        if result.invariant is ConversationInvariant.APPROVAL_IS_NEVER_INFERRED
        and result.verdict is InvariantVerdict.VIOLATED
    ]
    assert not offenders, [
        f"{item.case_id}: {item.detail}" for item in offenders
    ]


def test_the_known_violation_ledger_is_exact() -> None:
    """Fails when a recorded defect is fixed, and when a new one appears.

    Both are things a person must see. A ledger that only failed in one direction would
    become a place to hide problems in.
    """

    found = {
        (result.case_id, str(result.invariant))
        for case in load_adversarial_corpus(ROOT)
        for result in evaluate(case)
        if result.verdict is InvariantVerdict.VIOLATED
    }
    recorded = _ledger()

    fixed = recorded - found
    fresh = found - recorded
    assert not fixed, (
        "These recorded violations no longer happen - somebody fixed them. Remove them "
        f"from {LEDGER.name} and say so in the report: {sorted(fixed)}"
    )
    assert not fresh, (
        "New invariant violations that are not in the ledger. Investigate before "
        f"recording: {sorted(fresh)}"
    )


# ---------------------------------------------------------------------------------
# Validation case 2 and 4: seeded copy defect, and baseline classification.
# ---------------------------------------------------------------------------------


def test_the_attacker_vocabulary_is_a_superset_of_the_products() -> None:
    """Validation case 2's precondition.

    The attacker may know more than the defender; it may never know less. If the product
    starts refusing a phrase this harness has never heard of, the harness would report a
    clean run on a template containing it.
    """

    attacker = {phrase.casefold() for phrase in ATTACKER_CLAIM_PHRASES}
    product = {phrase.casefold() for phrase in FORBIDDEN_CLAIM_PHRASES}
    missing = product - attacker
    assert not missing, (
        "The product refuses phrases the attacker does not know: "
        + ", ".join(sorted(missing))
        + ". Add them to hm_oi.qa_attacks.ATTACKER_CLAIM_PHRASES."
    )

    attacker_deprecated = {phrase.casefold() for phrase in ATTACKER_DEPRECATED_PHRASES}
    product_deprecated = {phrase.casefold() for phrase in FORBIDDEN_PRODUCT_PHRASES}
    assert not product_deprecated - attacker_deprecated


@pytest.mark.parametrize(
    "seeded",
    [
        "Every asset here is 100% halal.",
        "Join now for guaranteed returns on your Watchlist.",
        "Our winning signals are risk-free.",
        "This coin is Shariah approved.",
        "استثمر معنا، ربح مضمون",
        "Open your Watch Plan today.",
    ],
)
def test_a_seeded_boundary_defect_is_found_and_classified(seeded: str) -> None:
    """Validation case 2: seed a forbidden phrase, the harness must find it."""

    hits = [hit for hit in scan_for_claims(seeded) if hit.is_violation]
    assert hits, f"the harness missed a seeded claim: {seeded!r}"

    finding = _finding(
        finding_id="SEED-001",
        title="Seeded forbidden phrase rendered to a customer",
        failure_class=FailureClass.COPY,
        summary=f"A template renders {hits[0].phrase!r}.",
        evidence=f"line {hits[0].line}: {hits[0].context}",
        dedupe_key=f"copy:{hits[0].phrase}",
    )
    assert finding.failure_class is FailureClass.COPY
    assert finding.reproduction.strip()


def test_a_refusal_that_names_a_banned_phrase_is_not_reported() -> None:
    """The other half of validation case 2: the harness must not cry wolf.

    A product that says "we never promise guaranteed returns" contains the phrase and is
    behaving correctly. Reporting it would train everyone to ignore this check.
    """

    denials = (
        "Hilal Markets never promises guaranteed returns.",
        "No one can offer a risk-free investment, and we do not claim to.",
        "We cannot tell you an asset is 100% halal.",
    )
    for text in denials:
        violations = [hit for hit in scan_for_claims(text) if hit.is_violation]
        assert not violations, f"reported a denial as a claim: {text!r} -> {violations}"


def test_a_baseline_failure_is_reported_as_baseline_not_new() -> None:
    """Validation case 4: point the harness at a known failure; it must say BASELINE."""

    baseline = BaselineSet.from_captures(
        {"tests/browser/test_dashboard_e2e.py::test_already_broken"},
        {"tests/browser/test_dashboard_e2e.py::test_already_broken"},
        sha="211aecc5",
    )
    assert baseline.is_stable

    finding = classify(
        _finding(
            finding_id="DUP-001",
            title="test_already_broken fails",
            summary="tests/browser/test_dashboard_e2e.py::test_already_broken fails.",
            dedupe_key="browser:test_already_broken",
        ),
        baseline,
    )
    assert finding.status is FindingStatus.BASELINE
    assert finding.baseline_match == "tests/browser/test_dashboard_e2e.py::test_already_broken"


def test_a_genuinely_new_failure_is_not_excused_by_the_baseline() -> None:
    baseline = BaselineSet.from_captures(
        {"tests/browser/test_dashboard_e2e.py::test_already_broken"},
        {"tests/browser/test_dashboard_e2e.py::test_already_broken"},
        sha="211aecc5",
    )
    finding = classify(_finding(finding_id="NEW-001"), baseline)
    assert finding.status is FindingStatus.NEW


def test_a_baseline_that_disagreed_with_itself_is_not_treated_as_stable() -> None:
    """A test that failed once is flaky, which is neither a baseline nor a finding."""

    baseline = BaselineSet.from_captures({"a::b"}, set(), sha="211aecc5")
    assert baseline.is_stable is False
    assert baseline.flaky == frozenset({"a::b"})
    assert baseline.stable == frozenset()


# ---------------------------------------------------------------------------------
# Validation case 5: the open product decisions.
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "summary",
    [
        "/dashboard/activity and /dashboard/lifecycles both answer for one page",
        "The canonical URL for the lifecycles page is not agreed",
        "The opportunities tab is named three different things",
    ],
)
def test_the_url_alias_question_is_blocked_not_reported_as_a_defect(summary: str) -> None:
    """Validation case 5."""

    baseline = BaselineSet.from_captures(set(), set(), sha="211aecc5")
    finding = classify(_finding(summary=summary, dedupe_key=f"url:{summary[:20]}"), baseline)
    assert finding.status is FindingStatus.BLOCKED_ON_PRODUCT_DECISION
    assert finding.blocked_on and "lifecycles_url_name" in finding.blocked_on


def test_the_landing_layout_question_is_blocked_not_reported_as_a_defect() -> None:
    baseline = BaselineSet.from_captures(set(), set(), sha="211aecc5")
    finding = classify(
        _finding(
            summary="The landing layout reference differs from the shipped page by 374 px",
            dedupe_key="landing:layout",
        ),
        baseline,
    )
    assert finding.status is FindingStatus.BLOCKED_ON_PRODUCT_DECISION
    assert finding.blocked_on and "landing_layout_reference" in finding.blocked_on


def test_a_product_decision_wins_over_a_baseline_match() -> None:
    """An unanswered question is not the same as a defect being lived with."""

    baseline = BaselineSet.from_captures(
        {"test_lifecycles_page"}, {"test_lifecycles_page"}, sha="211aecc5"
    )
    finding = classify(
        _finding(
            summary="test_lifecycles_page fails on /dashboard/lifecycles",
            dedupe_key="browser:test_lifecycles_page",
        ),
        baseline,
    )
    assert finding.status is FindingStatus.BLOCKED_ON_PRODUCT_DECISION


# ---------------------------------------------------------------------------------
# Validation case 6: the three boundaries that must fail.
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "https://hilalmarkets.com",
        "https://app.hilalmarkets.com",
        "https://www.example.com",
        "http://prod-internal:8000",
        "http://10.0.0.5:8000",
    ],
)
def test_a_production_target_is_refused(address: str) -> None:
    """Validation case 6a. There is no flag that turns this off."""

    profile = classify_target(address, app_env="test", fault_control_available=True)
    assert profile.kind is TargetKind.PRODUCTION
    assert profile.is_production


def test_a_loopback_address_in_front_of_production_is_still_refused() -> None:
    """A tunnel puts the real product behind 127.0.0.1. The server's own word wins."""

    profile = classify_target(
        "http://127.0.0.1:8000", app_env="production", fault_control_available=False
    )
    assert profile.kind is TargetKind.PRODUCTION
    assert any("tunnel" in item for item in profile.evidence)


def test_the_isolated_test_target_is_the_only_one_that_takes_faults() -> None:
    isolated = classify_target(
        "http://127.0.0.1:8124", app_env="test", fault_control_available=True
    )
    assert isolated.kind is TargetKind.ISOLATED_TEST
    assert isolated.supports_fault_injection
    assert require_fault_injection(isolated, "timeout_once") == "timeout_once"

    local = classify_target(
        "http://127.0.0.1:8000", app_env="development", fault_control_available=False
    )
    assert local.kind is TargetKind.LOCAL
    assert not local.supports_fault_injection
    with pytest.raises(FaultInjectionUnsupported):
        require_fault_injection(local, "timeout_once")


def test_a_test_target_with_evaluator_off_refuses_faults_without_calling_it_a_defect() -> None:
    """The specific noise this exists to stop: a correct refusal read as a bug."""

    profile = classify_target(
        "http://127.0.0.1:8124", app_env="test", fault_control_available=False
    )
    assert profile.kind is TargetKind.ISOLATED_TEST
    assert not profile.supports_fault_injection
    with pytest.raises(FaultInjectionUnsupported) as raised:
        require_fault_injection(profile, "timeout_once")
    assert "must not be recorded as a finding" in str(raised.value)


def test_the_harness_knows_exactly_the_faults_the_product_accepts() -> None:
    """The shared contract that replaces an import the boundary check refuses."""

    assert frozenset(EVALUATOR_FAULTS) == SUPPORTED_FAULTS


def test_promoting_a_regression_candidate_is_refused() -> None:
    """Validation case 6c. Promotion is a person's decision, recorded as theirs."""

    candidate = RegressionCandidate(
        candidate_id="CAND-001",
        title="A question must not change the timeframe",
        suggested_path="tests/unit/test_invariant_turn_fragments.py",
        asserts="patches_for_turn produces no monitored patch for a question-only turn.",
        sketch="assert not [p for p in patches_for_turn(q, state) if p.field in MONITORED]",
    )
    assert candidate.promoted is False
    with pytest.raises(PromotionRefused):
        candidate.promote()


def test_a_finding_without_a_reproduction_command_is_refused() -> None:
    """Validation case 6b's sibling: the harness cannot record an unusable finding."""

    with pytest.raises(IncompleteFinding) as raised:
        _finding(reproduction="")
    assert "reproduction" in str(raised.value)

    with pytest.raises(IncompleteFinding):
        _finding(falsifying_evidence="")


def test_the_harness_may_not_read_anything_but_its_committed_fixtures() -> None:
    """Validation case 6's other half: no path to customer data, by three checks."""

    for forbidden in (
        "ai_market_monitor.db",
        "backups/production.dump",
        "../../etc/passwd",
        "postgres://user:pw@host/db",
        "tests/fixtures/../../.env",
    ):
        with pytest.raises(ConversationSourceRefused):
            resolve_corpus(forbidden, ROOT)


# ---------------------------------------------------------------------------------
# Validation case 7: redaction.
# ---------------------------------------------------------------------------------


def test_no_synthetic_secret_in_a_fixture_reaches_a_report_or_an_evidence_file() -> None:
    """Validation case 7.

    The probe fixture deliberately contains an invented seed phrase, an invented API key
    and an invented bot token. None of them may appear anywhere downstream.
    """

    raw = (ROOT / "tests" / "fixtures" / "oi_adversarial_qa_secret_probe.jsonl").read_text(
        encoding="utf-8"
    )
    planted = ("sk-oi4qaFAKEkeyNOTreal", "123456789:AAFAKEtoken", "FAKEbearerTOKEN")
    for needle in planted:
        assert needle in raw, "the probe fixture no longer contains what it is probing for"

    from hm_oi.conversation_source import load_conversations

    loaded = load_conversations("tests/fixtures/oi_adversarial_qa_secret_probe.jsonl", ROOT)
    rendered = "\n".join(case.text for case in loaded)
    for needle in planted:
        assert needle not in rendered, f"{needle} survived the corpus reader"
    assert "REDACTED" in rendered


def test_the_evidence_store_refuses_a_record_carrying_a_secret(tmp_path: Path) -> None:
    """Refuses rather than cleans. A tidy [REDACTED] would hide that something upstream leaks."""

    store = EvidenceStore(root=tmp_path / "evidence")
    clean = EvidenceRecord(
        record_id="rec-001",
        attack_id="copy.static_claims",
        environment_label="isolated_test (APP_ENV=test)",
        interaction=Interaction(
            surface="setup_chat", action="sent one turn", sent="Watch RSI below 30 on 15m"
        ),
        failure_stage=FailureStage.NONE,
        failure_class=FailureClass.COPY,
        before=StateSnapshot(approved=False, monitor_active=False),
        after=StateSnapshot(approved=False, monitor_active=False),
        reproduction=".venv/Scripts/python -m pytest tests/oi -q",
        product_held=True,
    )
    written = store.store(clean)
    assert written.exists()

    leaking = EvidenceRecord(
        record_id="rec-002",
        attack_id="copy.static_claims",
        environment_label="isolated_test (APP_ENV=test)",
        interaction=Interaction(
            surface="setup_chat",
            action="sent one turn",
            sent="here is my key",
            # A private-key header is refused outright rather than replaced, because a
            # record that needed redacting means something upstream is carrying one.
            response_excerpt="-----BEGIN RSA PRIVATE KEY-----",
        ),
        failure_stage=FailureStage.NONE,
        failure_class=FailureClass.COPY,
        before=StateSnapshot(approved=False, monitor_active=False),
        after=StateSnapshot(approved=False, monitor_active=False),
        reproduction=".venv/Scripts/python -m pytest tests/oi -q",
        product_held=True,
    )
    with pytest.raises(EvidenceLeak):
        store.store(leaking)
    assert not store.path_for("rec-002").exists(), "a refused record was written anyway"


def test_the_evidence_store_refuses_a_real_looking_identifier(tmp_path: Path) -> None:
    """Synthetic corpora contain no real people. Anything that looks like one is a leak."""

    store = EvidenceStore(root=tmp_path / "evidence")
    record = EvidenceRecord(
        record_id="rec-003",
        attack_id="authz.admin_from_customer_session",
        environment_label="isolated_test (APP_ENV=test)",
        interaction=Interaction(
            surface="admin",
            action="probed an admin route",
            sent="GET /api/v1/admin/health",
            response_excerpt="owner: someone@realcompany.co.uk",
        ),
        failure_stage=FailureStage.REFUSED_AT_ENTRY,
        failure_class=FailureClass.AUTHORIZATION,
        before=StateSnapshot(approved=False, monitor_active=False),
        after=StateSnapshot(approved=False, monitor_active=False),
        reproduction=".venv/Scripts/python -m pytest tests/oi -q",
        product_held=True,
    )
    with pytest.raises(EvidenceLeak):
        store.store(record)


def test_a_finding_carrying_a_secret_cannot_be_constructed() -> None:
    with pytest.raises(IncompleteFinding):
        _finding(evidence="the key was sk-oi4qaFAKEkeyNOTrealAAAAAAAAAAAA")


# ---------------------------------------------------------------------------------
# Ranking, dedupe and the catalogue.
# ---------------------------------------------------------------------------------


def test_findings_are_deduplicated_by_key_keeping_the_worst() -> None:
    merged = dedupe(
        [
            _finding(
                finding_id="A", severity=Severity.LOW, dedupe_key="same", artifacts=("a.png",)
            ),
            _finding(
                finding_id="B", severity=Severity.CRITICAL, dedupe_key="same", artifacts=("b.png",)
            ),
            _finding(finding_id="C", severity=Severity.MEDIUM, dedupe_key="other"),
        ]
    )
    assert len(merged) == 2
    worst = next(item for item in merged if item.dedupe_key == "same")
    assert worst.severity is Severity.CRITICAL
    assert set(worst.artifacts) == {"a.png", "b.png"}


def test_findings_are_ranked_worst_first_and_stably() -> None:
    ordered = rank(
        [
            _finding(finding_id="C", severity=Severity.LOW, dedupe_key="c"),
            _finding(finding_id="A", severity=Severity.CRITICAL, dedupe_key="a"),
            _finding(finding_id="B", severity=Severity.MEDIUM, dedupe_key="b"),
        ]
    )
    assert [item.finding_id for item in ordered] == ["A", "B", "C"]
    assert rank(list(ordered)) == ordered


def test_the_spend_cap_refuses_the_call_that_would_cross_it() -> None:
    """Validation case 8. A ceiling you discover you have crossed is not a ceiling."""

    cap = SpendCap(ceiling_usd=0.10)
    cap.reserve(0.04, what="one turn")
    cap.reserve(0.05, what="another turn")
    with pytest.raises(BudgetExceeded):
        cap.reserve(0.05, what="a third turn")
    assert cap.spent_usd <= cap.ceiling_usd
    # The refused call is not booked: a ceiling that counted attempts it never made
    # would drift away from what was actually spent.
    assert cap.calls == 2


def test_a_skipped_attack_is_reported_with_its_reason_not_left_out() -> None:
    """An attack quietly absent from a report reads as one that passed."""

    isolated = classify_target(
        "http://127.0.0.1:8124", app_env="test", fault_control_available=True
    )
    local = classify_target(
        "http://127.0.0.1:8000", app_env="development", fault_control_available=False
    )

    _, skipped_on_isolated = attacks_runnable_against(isolated, allow_paid=False)
    runnable_local, skipped_on_local = attacks_runnable_against(local, allow_paid=False)

    # Every attack is accounted for, on both targets.
    assert len(runnable_local) + len(skipped_on_local) == len(BOUNDARY_ATTACKS)

    fault_skips = {
        item.attack_id
        for item in skipped_on_local
        if item.status == AttackStatus.SKIPPED_UNSUPPORTED
    }
    assert fault_skips == {item.attack_id for item in attacks_requiring_faults()}
    # The isolated target takes the faults, so it skips none of them for that reason.
    assert not [
        item
        for item in skipped_on_isolated
        if item.status == AttackStatus.SKIPPED_UNSUPPORTED
    ]
    for item in (*skipped_on_local, *skipped_on_isolated):
        assert item.detail.strip(), f"{item.attack_id} was skipped with no reason"


def test_a_run_may_not_even_be_planned_against_production() -> None:
    production = classify_target(
        "https://hilalmarkets.com", app_env="production", fault_control_available=False
    )
    with pytest.raises(TargetRefused):
        attacks_runnable_against(production, allow_paid=False)


def test_a_report_separates_new_baseline_and_blocked() -> None:
    baseline = BaselineSet.from_captures({"a::b"}, {"a::b"}, sha="211aecc5")
    target = classify_target(
        "http://127.0.0.1:8124", app_env="test", fault_control_available=True
    )
    findings = [
        classify(_finding(finding_id="N-1", dedupe_key="n1"), baseline),
        classify(
            _finding(finding_id="B-1", summary="a::b fails", dedupe_key="b1"), baseline
        ),
        classify(
            _finding(
                finding_id="P-1",
                summary="the canonical URL for /dashboard/lifecycles is not agreed",
                dedupe_key="p1",
            ),
            baseline,
        ),
    ]
    report = build_report(
        run_id="test-run",
        head_sha="211aecc5",
        target=target,
        limits=RunLimits(),
        baseline=baseline,
        findings=findings,
    )
    payload = report.to_dict()
    assert payload["counts"] == {
        "new": 1,
        "baseline": 1,
        "blocked_on_product_decision": 1,
    }
    assert payload["baseline"]["is_stable"] is True
    assert payload["target"]["kind"] == "isolated_test"


def test_every_attack_in_the_catalogue_is_complete_enough_to_act_on() -> None:
    assert BOUNDARY_ATTACKS
    identifiers = [attack.attack_id for attack in BOUNDARY_ATTACKS]
    assert len(set(identifiers)) == len(identifiers)
    for attack in BOUNDARY_ATTACKS:
        assert attack.refusal_contract.strip(), attack.attack_id
        assert attack.violation_looks_like.strip(), attack.attack_id
        assert attack.reproduction.strip(), attack.attack_id
        if attack.requires_fault_injection:
            assert attack.method is AttackMethod.HTTP_PROBE, attack.attack_id


def test_every_failure_class_has_somewhere_to_land() -> None:
    """A class with no attack is a class nobody remembers to look at.

    This was not true when the catalogue was first written: five of the twelve had no
    entry, and they were the five where the corpus was quietly finding the worst
    problems. A reader comparing the catalogue against the brief's list would have
    concluded those five were untested.
    """

    covered = {attack.failure_class for attack in BOUNDARY_ATTACKS}
    missing = set(FailureClass) - covered
    assert not missing, "failure classes with no attack: " + ", ".join(
        sorted(str(item) for item in missing)
    )


# ---------------------------------------------------------------------------------
# Product-boundary assertions the attack catalogue names.
# ---------------------------------------------------------------------------------


def test_refusals_cannot_carry_a_substitute() -> None:
    """A refusal with somewhere to put an alternative eventually carries one."""

    fields = set(UnsupportedCapability.__dataclass_fields__)
    for banned in ("suggested_alternative", "alternative", "fallback", "nearest_match", "instead"):
        assert banned not in fields


@pytest.mark.parametrize(
    "key",
    [
        "backtesting",
        "whatsapp_alerts",
        "portfolio_tracking",
        "stocks_and_forex",
        "trade_execution",
        "brokerage_custody",
        "buy_sell_recommendations",
        "financial_advice",
        "leverage_and_margin",
        "guaranteed_returns",
        "ai_religious_ruling",
    ],
)
def test_unsupported_capabilities_are_refused_by_name(key: str) -> None:
    refusal = refuse(key)
    assert refusal.key == key
    assert refusal.title.strip()
    assert refusal.reason.strip()
    assert key.split("_")[0][:4] in refusal.customer_message().casefold() or refusal.title


def test_scanner_and_monitor_stay_apart() -> None:
    scanner = EVALUATION_MODES[EvaluationMode.SCANNER]
    monitor = EVALUATION_MODES[EvaluationMode.MONITOR]
    assert scanner.requires_approval is False
    assert monitor.requires_approval is True
    for mode in (scanner, monitor):
        assert mode.trigger.strip()
        assert mode.cadence.strip()
        assert mode.cost_note.strip()
        assert mode.does_not.strip()
    assert scanner.label != monitor.label


def test_launch_stage_hides_what_it_says() -> None:
    """A narrower stage never exposes more than a wider one."""

    order = (
        LaunchStage.INTERNAL,
        LaunchStage.PRIVATE_BETA_INVITE,
        LaunchStage.PUBLIC_WAITLIST,
        LaunchStage.PUBLIC_LAUNCH,
    )
    for narrower, wider in zip(order, order[1:], strict=False):
        left = STAGE_EXPOSURE[narrower]
        right = STAGE_EXPOSURE[wider]
        assert left.hidden_pages >= right.hidden_pages
        assert not (left.exposes_checkout and not right.exposes_checkout)
        assert not (left.advertises_pricing and not right.advertises_pricing)

    clamped = resolve_launch_stage(LaunchStage.PUBLIC_LAUNCH, waitlist_ceiling=True)
    assert clamped.effective is LaunchStage.PUBLIC_WAITLIST
    assert clamped.clamped_by_environment


def test_customer_copy_carries_no_forbidden_claim() -> None:
    """The attacker's own wider vocabulary, run over every customer-facing source."""

    offences: list[str] = []
    for source in customer_copy_sources(ROOT):
        candidates = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in candidates:
            if not path.is_file() or path.suffix not in {".html", ".py", ".js"}:
                continue
            for hit in scan_for_claims(path.read_text(encoding="utf-8")):
                if hit.is_violation:
                    offences.append(
                        f"{path.relative_to(ROOT).as_posix()}:{hit.line}: "
                        f"{hit.rule}: {hit.phrase!r}"
                    )
    assert not offences, "\n".join(offences)


def test_deprecated_product_terms_are_gone() -> None:
    """The release gate forbids "Watch Plan"; the shipped term is "Watchlist"."""

    pattern = re.compile(r"\bwatch\s+plans?\b", re.IGNORECASE)
    offences: list[str] = []
    for source in customer_copy_sources(ROOT):
        candidates = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in candidates:
            if not path.is_file() or path.suffix not in {".html", ".py", ".js"}:
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if pattern.search(line):
                    offences.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    assert not offences, "\n".join(offences)
