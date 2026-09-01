# Candidate Shariah authorities — what the open web actually publishes

Researched 30 August 2026. This is **research, not assessments.** Nothing here has been
imported, and nothing here carries a Shariah status inside Hilal Markets. A source
becomes a methodology only after rights clearance, a verified snapshot, and the
platform's own review — see `docs/SHARIA_METHODOLOGY_IMPORT_PACK.md`.

## The finding, in one line

The sources that publish **hundreds** of coins do not name a single scholar. The sources
that name scholars publish **15 to 50** coins, and we already hold almost all of them.

## What we hold today

184 unique coins, from three methodologies. Measured from
`data/methodology_union_matrix.csv`:

| Authority | Coins on its list | **New** coins it added |
|---|---:|---:|
| Fasset Shariah Reports | 183 | 183 |
| Shariah Review Bureau | 31 | 1 — only SUI |
| SC Malaysia SAC | 15 | 0 |

152 coins sit on one list only; 19 on two; 13 on all three.

## Group A — real authorities and boards

Named scholars, a stated framework, or a regulator behind them. These are the sources
that fit the product's governance rule.

| Source | Who stands behind it | Coins published | New for us | Rights |
|---|---|---:|---:|---|
| [HalalSignalz](https://www.halalsignalz.com/crypto-passlist) | Crypto Shariah Screening Framework by **Mufti Faraz Adam** (2021); re-screened monthly, page dated 4 June 2026 | 20 | **~1** (CSPR) | Commercial site, no permission |
| [Sharlife](https://sharlife.my/crypto-shariah) | Malaysian platform; page names no scholar and carries no date | ~41 compliant | **~6** (HYPE, WBTC, CRO, XAUt, AAVE, PI) | Commercial site, no permission |
| [Islamic Finance Guru](https://www.islamicfinanceguru.com/resources/halal-crypto-list) | Screened with muftis on the IFG forum | ~50 | handful | List is gated behind a download |
| [Amanie Advisors](https://amanieadvisors.com/shariah-white-paper-on-ethereum/) | Major Shariah advisory firm, AAOIFI-based | Certifies **products**, not lists — ComTech Gold (CGO), Ether staking, Binance Sharia Earn (BNB/ETH/SOL) | ~1 (CGO) | Per-certificate |
| SC Malaysia SAC | The regulator we already carry | 15 | 0 | Already held. Its list page was returning "under maintenance" on 30 Aug 2026 |

**Group A total, if we licensed every one: roughly 10 to 20 new coins.**

Note a real disagreement worth keeping: Sharlife shows **AAVE as compliant**, while
Fasset lists AAVE on its **non-compliant guard**. The system is built for exactly this —
each authority keeps its own result and no combined score is ever invented.

## Group B — automated screeners

These reach the volume asked for. **None of them names a scholar or cites a standard.**

| Source | Screened | Rated halal | Named scholars | Standard cited |
|---|---:|---:|---|---|
| [Saraf Screening](https://sarafscreening.com/) | 1,503 | 895 | **None** | **None** |
| [CryptoUmmah](https://cryptoummah.com/halal-crypto-list) | 2,589 | 294 | **None** | "27-point methodology", not published |

Either one alone would add 200+ coins. Both fail the test the product sets for itself:
a Shariah status must be evidence-backed and traceable to a body that can be named and
questioned. Importing an anonymous score as a governed halal status is the one thing
`CLAUDE.md` forbids outright.

## What this means for the decision

Three honest routes, in the order I would rank them:

1. **License a platform-scale source that has a real board.** The volume problem and the
   trust problem are only solved together by a source that has both. Fasset was that
   for us once. A Shariah-certified exchange with a named board — Rain, CoinMENA — or a
   screener that publishes its scholars is the shape to look for.
2. **Stand up our own board.** A named scholar or panel reviewing our own dossiers turns
   Hilal Markets into a Group A source itself, with no licence and no rights block. It
   is the slowest route and the only one that scales without asking anyone's permission.
3. **Carry a Group B screener as clearly-labelled information, never as a status.** A
   separate, visibly non-authoritative surface. This is a product and religious decision
   for the owner, not an engineering one, and it must never enter the passport as a
   Shariah status.

## Also worth knowing

Of our 184 coins, only **120** have a live Binance USDT spot pair and **105** on Bybit. A
coin with no market cannot be monitored, so coverage growth is capped twice: by what an
authority has ruled on, and by what an exchange lists.

Sources: [HalalSignalz](https://www.halalsignalz.com/crypto-passlist) ·
[Sharlife](https://sharlife.my/crypto-shariah) ·
[Islamic Finance Guru](https://www.islamicfinanceguru.com/resources/halal-crypto-list) ·
[Amanie Advisors](https://amanieadvisors.com/shariah-white-paper-on-ethereum/) ·
[Saraf Screening](https://sarafscreening.com/) ·
[CryptoUmmah](https://cryptoummah.com/halal-crypto-list)
