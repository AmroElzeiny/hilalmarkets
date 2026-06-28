# Private Beta Readiness Checklist

## Completed In This Pass

- [x] Repo hygiene ignore rules expanded.
- [x] `.env.example` uses placeholders only.
- [x] Research-monitor proof fields added.
- [x] Entry/RR not required for research proof or quick-scan tests.
- [x] Default lifecycle labels no longer show `Entry zone`.
- [x] Provider-required concepts hidden from normal capability payload.
- [x] Provider-required mandatory conditions still block activation validation.
- [x] Fixture market-data provider added for local/CI.
- [x] Production/staging rejects fixture mode.
- [x] Worker smoke script added and run.
- [x] Provider-required concepts documented by name.
- [x] External test-system instructions added.
- [x] Full backend pytest passed locally.
- [x] Browser tests passed locally.
- [x] Focused engine/services/interpreter tests passed locally.

## Must Pass Before Inviting Private Beta Users

- [ ] Repair local Git metadata or verify a clean clone.
- [ ] Run secret scanner on the actual Git branch.
- [ ] Run database migrations on staging.
- [ ] API starts in staging.
- [ ] Worker starts in staging.
- [ ] Scheduler starts in staging.
- [ ] Redis/Postgres connectivity verified.
- [ ] Fixture mode works locally.
- [ ] Real provider mode configured or unsupported providers disabled.
- [ ] Telegram test delivery verified or disabled.
- [ ] Discord test delivery verified or disabled.
- [ ] Payment sandbox webhook verified or billing disabled.
- [ ] Quick Scan works through backend path.
- [ ] Research monitor publishes without entry/RR.
- [ ] Proof receipt shows required condition completion.
- [x] Browser tests pass locally.
- [x] Full pytest suite passes locally.
- [x] Worker smoke test passes locally.
- [ ] Landing/onboarding copy avoids financial-performance guarantees.
- [ ] Admin/support diagnostics visible.
- [ ] Error logs visible without secrets.

## Current Readiness Estimate

Private beta readiness: **strong local readiness, staging pending**.

The core safety changes are in place and local backend/browser/smoke tests pass. Staging cannot be called ready until real Telegram/Discord/payment test systems, clean Git metadata, secret scan, and deployment-environment checks pass.
