# 🕌 Sharia Governance and Evidence

> **Workspace status:** Updated 17 July 2026. Product name: **HilalMarkets**.

## Governing principle

HilalMarkets provides **methodology-specific screened-market information**, not a universal religious ruling. Technology may collect, normalize, summarize, calculate, compare, and monitor factual evidence. Final status authority belongs to approved human governance.

## Status taxonomy

| Status | Meaning |
|---|---|
| Eligible | Meets the disclosed methodology using the reviewed evidence and effective version |
| Eligible with qualifications | Meets the methodology but important use or evidence qualifications must be read |
| Under review | A material question, change, or safety hold requires human review |
| Disputed | Approved methodologies or reviewers differ in a way that cannot be collapsed |
| Excluded | Does not meet the selected methodology |
| Insufficient information | Required evidence is missing, stale, contradictory, or not reliable enough |

Missing evidence never becomes eligibility.

## Source-to-publication flow

1. Approved source adapter imports a versioned snapshot and content hash.
2. Canonical identity is verified using name, network/native-token identity, contracts or official project references, provider IDs, and exact exchange mapping.
3. Factual evidence is collected from registered official sources.
4. AI may organize factual evidence into a strict schema, but receives no decision or publication field.
5. Completeness and contradiction gates determine whether the case needs research, identity resolution, or review.
6. A human reviewer records criterion decisions, reasoning, qualifications, gaps, evidence references, role, timestamp, version, and integrity hash.
7. Approval and publication are separate actions.
8. Publication creates immutable assessment and Passport versions and refreshes the screened universe.
9. Material source changes create a new scoped review and can trigger a safety hold.
10. A new publication supersedes rather than overwrites the old record.

## SC Malaysia reference workflow

The implemented source workflow can import explicit asset-level rows from the Securities Commission Malaysia digital-assets page. The customer-facing label is scoped to that source—for example, an SC Malaysia SAC reference—rather than being presented as universal approval.

Important boundaries:

- parsed rows are not automatically public;
- exact source wording, SAC meeting, and date are retained;
- ticker-only identity matching fails;
- non-covered or rejected workflow states are not converted into “haram”;
- official-source reference and HilalMarkets factual research are separated;
- approval of a token does not approve every related use, yield product, wrapper, or leveraged instrument.

## Two-layer Evidence Passport

### Layer A — authoritative external reference
- exact source wording;
- authority, meeting, decision date;
- source and retrieval date;
- source-specific scope and limits.

### Layer B — HilalMarkets factual dossier
- identity and current market mapping;
- project activity and token role;
- staking, lending, yield, treasury, governance, tokenomics, and derivative exposure;
- sources reviewed;
- gaps, contradictions, and last verification;
- explicit label that AI-organized research is not a religious decision.

## Review roles

The software supports governance roles such as System Admin, Researcher, Reviewer, and Publisher. One owner can currently hold multiple roles, while optional four-eyes enforcement can require a different publisher from the reviewer.

Before a public launch, document:

- adviser or committee identity and authority;
- reviewer qualifications;
- assignment and escalation;
- service-level target;
- evidence freshness requirements;
- correction and appeal process;
- separation of research, review, and publication;
- when four-eyes approval becomes mandatory.

## Fail-closed rules

- No active methodology → no executable screened market.
- No effective assessment → asset excluded from default execution.
- Identity conflict → no research/publication.
- Unknown or delisted exchange mapping → market unavailable, without rewriting religious status.
- Material source change → new review; historical evidence remains intact.
- AI failure → recorded failure, no status effect.
- Publication failure → decision remains recorded but not publicly effective.

## Production governance checklist

- [ ] Qualified governing authority appointed
- [ ] Production methodology approved and versioned
- [ ] Evidence-source catalog approved and legally reviewed
- [ ] Pilot assets reviewed individually
- [ ] Review SLA and assignment process operational
- [ ] Corrections, appeals, and incident handling documented
- [ ] Status-change notification language approved
- [ ] Public disclaimers reviewed by counsel
- [ ] Methodology comparison rules approved
- [ ] Four-eyes threshold decided
