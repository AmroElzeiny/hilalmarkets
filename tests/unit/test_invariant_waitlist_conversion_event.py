"""The waitlist conversion has one owner, one spelling, and one moment it may fire.

The reported need was "track a GA4 conversion when somebody joins the waitlist". The
class of defect behind that request is the one this repository keeps hitting: a decision
copied into a second place. A conversion event is especially easy to copy - into the
click handler of a call-to-action, into a second form added later, into a template - and
each copy reports a signup that did not happen.

So these tests do not check that one line exists. They pin the rule:

* the event name is written once, in `analytics.ts`;
* it is emitted from exactly one function, the one that means "the server confirmed a
  new signup";
* that function is reached from every place that submits the waitlist;
* it goes through the consent gate, never through a direct `dataLayer.push`;
* it is scoped to one submission, so a repeat cannot report a second signup;
* and the built bundle a visitor actually downloads carries all of the above.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "Hilal-Markets-Website" / "src"
ANALYTICS = FRONTEND / "analytics.ts"
APP = FRONTEND / "App.tsx"
BUILT_BUNDLE = ROOT / "src/ai_market_monitor/static/landing/assets/landing.js"

EVENT_NAME = "waitlist_join"
# Every source file the bundle is built from, so a rule proved here is proved for the
# whole site rather than for the two files that were open at the time.
FRONTEND_SOURCES = sorted({*FRONTEND.rglob("*.ts"), *FRONTEND.rglob("*.tsx")})


def _analytics() -> str:
    return ANALYTICS.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """The text of one function, up to the next top-level declaration."""

    match = re.search(rf"^(?:export )?function {name}\(", source, flags=re.MULTILINE)
    assert match, f"{name} is no longer a top-level function in analytics.ts"
    remainder = source[match.start() :]
    end = re.search(r"\n(?:export )?(?:function|const|type) ", remainder[1:])
    return remainder[: end.start() + 1] if end else remainder


def test_the_conversion_event_name_is_written_exactly_once_in_the_whole_site():
    """One spelling. A second copy is how a tag and a page drift by one character."""

    analytics = _analytics()
    assert f"export const WAITLIST_JOIN_EVENT = '{EVENT_NAME}'" in analytics
    assert analytics.count(f"'{EVENT_NAME}'") == 1
    assert analytics.count(f'"{EVENT_NAME}"') == 0

    for path in FRONTEND_SOURCES:
        if path == ANALYTICS:
            continue
        assert EVENT_NAME not in path.read_text(encoding="utf-8"), path.name


def test_only_a_server_confirmed_new_signup_emits_the_conversion():
    """Not a click, not a view, not an attempt, not an error - a confirmed signup.

    Checked against every other waitlist tracking function, not only the error one, so a
    function added later cannot quietly join the list of things that report a signup.
    """

    analytics = _analytics()
    success = _function_body(analytics, "trackWaitlistSuccess")
    assert "WAITLIST_JOIN_EVENT" in success

    other_waitlist_functions = [
        "trackWaitlistFormView",
        "trackWaitlistFormStart",
        "trackWaitlistSubmitAttempt",
        "trackWaitlistError",
        "trackCtaClick",
        "trackSectionView",
    ]
    for name in other_waitlist_functions:
        assert "WAITLIST_JOIN_EVENT" not in _function_body(analytics, name), name

    # The name appears exactly twice: where it is declared, and where it is sent.
    # Anything else is a second sender.
    assert analytics.count("WAITLIST_JOIN_EVENT") == 2
    assert analytics.count("emitGoogle(WAITLIST_JOIN_EVENT") == 1


def test_the_conversion_passes_through_the_consent_gate_and_carries_no_form_data():
    """`emitGoogle` is the gate. A direct push would bypass consent and the parameter filter."""

    analytics = _analytics()
    success = _function_body(analytics, "trackWaitlistSuccess")
    assert "emitGoogle(WAITLIST_JOIN_EVENT, {})" in success
    assert "dataLayer" not in success
    assert "gtag" not in success

    # The gate itself: no Analytics consent, no event, for every Google event alike.
    gate = _function_body(analytics, "emitGoogle")
    assert "if (!runtimeConfig().enabled || !consent.analytics) return false" in gate

    # And no component reaches around the module.
    for path in FRONTEND_SOURCES:
        if path == ANALYTICS:
            continue
        source = path.read_text(encoding="utf-8")
        assert "dataLayer" not in source, path.name
        assert "window.gtag" not in source, path.name


def test_one_submission_can_report_at_most_one_conversion():
    """A re-render, a retried callback or a repeated call reports nothing further.

    The scope is the submission's own idempotency key - the same identifier that stops
    the server writing a second row - so the analytics count and the stored record can
    never disagree about how many people joined.
    """

    analytics = _analytics()
    success = _function_body(analytics, "trackWaitlistSuccess")
    assert "emitOnce(`waitlist-join:${eventScope}`" in success
    assert "const eventScope = submissionId.trim()" in success

    once = _function_body(analytics, "emitOnce")
    # Remembered only when it was actually sent: an event dropped for want of consent
    # must stay sendable, not be recorded as already delivered.
    assert "if (onceEvents.has(key)) return false" in once
    assert "if (!send()) return false" in once

    app = APP.read_text(encoding="utf-8")
    assert "trackWaitlistSuccess('landing_final', idempotencyKey.current)" in app
    # The key is replaced only after the submission is finished, so the whole of one
    # attempt shares one scope.
    assert app.index("trackWaitlistSuccess(") < app.index("resetIdempotency()\n    } catch")


def test_every_place_that_submits_the_waitlist_reports_the_confirmed_signup():
    """Requirement: every entry point, not only the landing form that exists today.

    There is one signup flow - `submitWaitlist`. Any file that calls it is a waitlist
    entry point, and each one has to report success through the single owner. A second
    form added later fails this test until it does.
    """

    callers = [
        path
        for path in FRONTEND_SOURCES
        if path.name != "publicForms.ts"
        and "submitWaitlist(" in path.read_text(encoding="utf-8")
    ]
    assert callers, "No waitlist entry point found; the flow was renamed or removed."
    for path in callers:
        source = path.read_text(encoding="utf-8")
        assert "trackWaitlistSuccess(" in source, path.name
        # Reported on the confirmed-new branch, never beside the request itself.
        assert "if (result.created) {" in source, path.name
        assert source.index("if (result.created) {") < source.index(
            "trackWaitlistSuccess("
        ), path.name


def test_the_bundle_a_visitor_downloads_carries_the_conversion_and_one_gtm_install():
    """Source proves intent; the built file is what ships.

    `Hilal-Markets-Website/src` is compiled into `static/landing/assets/landing.js` by
    hand. Without this test the event could sit in the source for weeks while every
    visitor ran a bundle that never emitted it.
    """

    bundle = BUILT_BUNDLE.read_text(encoding="utf-8")
    # The minifier rewrites string quotes, so the name is matched, not the quoting.
    assert any(f"{q}{EVENT_NAME}{q}" in bundle for q in ('"', "'", "`"))
    assert bundle.count(EVENT_NAME) == 1

    # One analytics installation, and it is the existing container.
    assert bundle.count("googletagmanager.com/gtm.js?id=") == 1
    assert "googletagmanager.com/gtag/js" not in bundle
    # No GA4 measurement ID is compiled into the page - not the property this event is
    # reported to, and not any other. GA4 is configured inside the GTM container.
    assert "G-2Q0KT6RGE8" not in bundle
    assert not re.search(r"\bG-[A-Z0-9]{6,}\b", bundle)
    for path in FRONTEND_SOURCES:
        assert "G-2Q0KT6RGE8" not in path.read_text(encoding="utf-8"), path.name
