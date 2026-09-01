"""The one register of screening conditions, their evidence, and how each is found.

**What this module is.** Every condition the Hilal Markets Methodology may apply lives
here exactly once: what it forbids, the Qur'anic verse or hadith behind it, the words it
looks like in a project's own writing, and the coarse activity it belongs to. Nothing
else in the product is allowed to hold a second list of "things that refuse a coin".

**What this module is not.** It is not a fatwa and it does not decide anything. Writing a
condition down here has no effect at all. A condition refuses a coin only after the
product owner has approved it, and that approval lives in a separate file —
``sharia_condition_decisions.json`` — signed and dated, so the *proposal* and the
*decision* can never be confused with one another. That separation is the whole design:

    This module says   "here is a rule, and here is the evidence for it".
    The decisions file says   "the owner approved it on this date".

An unapproved condition is inert. It is still read, still measured, and still reported —
so the owner can see what approving it *would* do before approving it — but it can never
change a verdict.

**Approval is a reviewed act, not a runtime switch.** The decisions file is in git. A
condition changing state is a commit somebody signs off, which is what
``CLAUDE.md`` means by a Shariah status being governed rather than inferred.

**A condition nobody can check must say so.** Some conditions are true rules that no
amount of reading a website can settle — a debt ratio needs a balance sheet, a hidden
mint function needs the contract. Those carry :attr:`Detection.MANUAL` or
:attr:`Detection.NUMERIC`, and an approved one that cannot be checked is reported on
every verdict as an unchecked rule. It is never quietly treated as passed. Silence is
not evidence, and a screen that hides what it could not look at is worse than one that
refuses.

**Precision over recall, in the phrases.** A missed condition costs an unresolved
question a person can answer. A wrongly-found one costs a public refusal that nobody
notices until the project complains. Every phrase is a whole phrase for the reason given
in :mod:`sharia_evidence_vocabulary` — "raffle" anchored on one side only matched inside
"Raffles Avenue" and refused Tezos as a casino.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from typing import Any

#: The file that carries the owner's decisions. Kept beside this module so it ships with
#: the package, and kept *separate* from it so a proposal is never mistaken for a ruling.
DECISIONS_FILE = "sharia_condition_decisions.json"


class Activity(StrEnum):
    """What an asset's project actually does. Observable facts, not judgements.

    Every value is something a researcher can point at in the project's own
    documentation. Nothing here encodes a religious conclusion — the conclusion is a
    :class:`Condition`, which is stated separately, with its evidence, so it can be
    challenged without rewriting the facts.

    An activity is a **bucket**, deliberately coarser than a condition. A lottery, a
    casino, a sportsbook and a paid loot box are four conditions with four separate
    proofs, and all of them are :attr:`GAMBLING` when the page has to say in one line
    what the project does.
    """

    # --- Activities that a condition may refuse. ---

    #: The protocol's own business is lending money and charging for it.
    LENDING_BORROWING = "lending_borrowing"
    #: The token pays its holder a return for holding it, funded by lending or by
    #: a fixed promise rather than by work performed.
    INTEREST_BEARING_HOLDING = "interest_bearing_holding"
    #: Perpetuals, options, synthetic leverage, prediction markets.
    DERIVATIVES_OR_LEVERAGE = "derivatives_or_leverage"
    #: Casino, sportsbook, lottery, or a token whose revenue is betting.
    GAMBLING = "gambling"
    #: A token representing a share, bond or other security.
    TOKENIZED_SECURITY = "tokenized_security"
    #: A token with no protocol, product or service behind it.
    NO_UNDERLYING_UTILITY = "no_underlying_utility"
    #: Interest-based banking, conventional insurance, credit issuance.
    CONVENTIONAL_FINANCE = "conventional_finance"
    #: Alcohol, pork, tobacco, narcotics — the goods themselves.
    PROHIBITED_GOODS = "prohibited_goods"
    #: Pornography, prostitution, trafficking.
    ADULT_OR_IMMORAL_TRADE = "adult_or_immoral_trade"
    #: Fortune telling, astrology, sorcery, idols and rites of shirk.
    OCCULT_OR_IDOLATRY = "occult_or_idolatry"
    #: Arms sold for unlawful aggression.
    UNLAWFUL_WEAPONS = "unlawful_weapons"
    #: Entertainment a body of scholars refuses. Disputed, and marked as such.
    DISPUTED_ENTERTAINMENT = "disputed_entertainment"
    #: A scheme paying earlier entrants out of later entrants' money.
    PONZI_OR_PYRAMID = "ponzi_or_pyramid"
    #: Faked volume, bids with no intent to buy, coordinated pumps.
    MARKET_MANIPULATION = "market_manipulation"
    #: Hidden powers, false claims, undisclosed control over holders' money.
    DECEPTIVE_DISCLOSURE = "deceptive_disclosure"
    #: Selling what the seller does not own or has not taken possession of.
    SELLING_WHAT_IS_NOT_OWNED = "selling_what_is_not_owned"
    #: Trading debt for money at a price other than its face value.
    DEBT_TRADING = "debt_trading"
    #: Currency for currency without both sides changing hands at once.
    DEFERRED_CURRENCY_EXCHANGE = "deferred_currency_exchange"
    #: A claim on a metal or a currency that is not actually held against it.
    UNBACKED_COMMODITY_CLAIM = "unbacked_commodity_claim"
    #: Withholding a necessity to move its price, or capturing control of a protocol.
    HOARDING_OR_CONTROL = "hoarding_or_control"
    #: A service whose selling point is escaping lawful obligation.
    ILLICIT_FINANCE_SERVICE = "illicit_finance_service"
    #: A contract form the Sunnah forbids — two sales in one, a loan tied to a sale.
    CORRUPT_CONTRACT_FORM = "corrupt_contract_form"
    #: A partnership where one side's capital or profit is guaranteed.
    GUARANTEED_CAPITAL_OR_RETURN = "guaranteed_capital_or_return"
    #: Money earned from a forbidden source, mixed into otherwise clean income.
    MIXED_PROHIBITED_INCOME = "mixed_prohibited_income"
    #: Spending, lending or selling what belongs to a customer without their consent.
    MISUSE_OF_CUSTOMER_ASSETS = "misuse_of_customer_assets"
    #: Kept apart from :attr:`SPOT_EXCHANGE` on purpose. The objection to impermanent
    #: loss is contested, and every decentralised exchange has liquidity pools — so
    #: sharing the spot-exchange bucket would let one disputed condition refuse the
    #: whole category the moment it was approved.
    DISPUTED_LIQUIDITY_PROVISION = "disputed_liquidity_provision"

    # --- Below this line: activities that never refuse on their own. ---

    #: Runs its own settlement network.
    OWN_SETTLEMENT_NETWORK = "own_settlement_network"
    #: Validating, sequencing or securing a network, and being paid for that work.
    STAKING_OR_VALIDATION = "staking_or_validation"
    #: Swapping assets people already hold.
    SPOT_EXCHANGE = "spot_exchange"
    #: Compute, storage, bandwidth, data, oracles, identity, naming.
    INFRASTRUCTURE_SERVICE = "infrastructure_service"
    #: Games, collectibles, media, ticketing, metaverse.
    CONSUMER_APPLICATION = "consumer_application"
    #: Holds one unit of a currency or a metal and redeems it on demand.
    FULLY_BACKED_REDEEMABLE = "fully_backed_redeemable"
    #: Access, fee discounts or governance inside one platform.
    PLATFORM_ACCESS_OR_GOVERNANCE = "platform_access_or_governance"


class HolderReturn(StrEnum):
    """What holding the token pays its holder, and where that payment comes from.

    This exists because of a measured failure, not a theory. When a model was asked
    blind what each asset does, it called Dai, GHO, Usual USD, AUSD and Frax USD
    "fully backed and redeemable" — true of the peg, silent about the yield — and the
    screen passed all five. Every one is a token an authority refuses.

    A peg claim on its own can no longer reach a verdict. Say what the holder is paid.
    """

    #: Holding it pays the holder nothing. The peg is the whole product.
    NONE = "none"
    #: Paid for work performed — validating, sequencing, providing a service.
    FROM_WORK = "from_work"
    #: Paid out of lending, treasuries, or a fixed promise to the holder.
    FROM_LENDING_OR_PROMISE = "from_lending_or_promise"


class Family(StrEnum):
    """The chapter of fiqh a condition belongs to. Used for grouping, never for deciding."""

    RIBA = "riba"
    MAYSIR = "maysir"
    GHARAR = "gharar"
    PROHIBITED = "prohibited"
    DECEIT = "deceit"
    WRONGFUL_GAIN = "wrongful_gain"
    CONTRACT_FORM = "contract_form"
    EXCHANGE = "exchange"
    RATIO = "ratio"
    HARM = "harm"


#: What each family is called on an Arabic page.
FAMILY_TITLE_AR: Mapping[Family, str] = {
    Family.RIBA: "الربا",
    Family.MAYSIR: "الميسر والقمار",
    Family.GHARAR: "الغرر والجهالة",
    Family.PROHIBITED: "الأنشطة المحرمة لذاتها",
    Family.DECEIT: "الغش والتدليس",
    Family.WRONGFUL_GAIN: "أكل المال بالباطل",
    Family.CONTRACT_FORM: "صيغة العقد",
    Family.EXCHANGE: "الصرف والتقابض",
    Family.RATIO: "المعايير الكمية",
    Family.HARM: "الضرر والظلم",
}


class Status(StrEnum):
    """Where a condition stands with the product owner."""

    #: Written, evidenced, measured — and inert. It cannot change any verdict.
    PROPOSED = "proposed"
    #: The owner approved it. It refuses coins.
    APPROVED = "approved"
    #: The owner considered it and said no. Kept so nobody proposes it again blind.
    REJECTED = "rejected"


class Agreement(StrEnum):
    """How settled the underlying ruling is among scholars.

    This is here for honesty, not for logic — nothing in the code reads it to decide
    anything. It is shown to the owner beside each condition, because approving a
    condition every school agrees on and approving one a minority holds are not the
    same act, and a reviewer is owed the difference.
    """

    #: Agreed by consensus. Riba, maysir, khamr.
    UNANIMOUS = "unanimous"
    #: The position of the majority, with a known minority view.
    MAJORITY = "majority"
    #: Genuinely contested among contemporary bodies.
    DISPUTED = "disputed"


class Detection(StrEnum):
    """How this condition could ever be established for a real coin."""

    #: From the project's own written pages, by phrase. The screen can do this alone.
    TEXT = "text"
    #: Only a person can settle it — reading a contract, an audit, a filing.
    MANUAL = "manual"
    #: Needs a number the product does not collect yet, such as a debt ratio.
    NUMERIC = "numeric"


class EvidenceKind(StrEnum):
    QURAN = "quran"
    SUNNAH = "sunnah"
    IJMA = "ijma"
    QAIDA = "qaida"
    STANDARD = "standard"


#: What each kind of evidence is called in Arabic.
EVIDENCE_KIND_AR: Mapping[EvidenceKind, str] = {
    EvidenceKind.QURAN: "قرآن",
    EvidenceKind.SUNNAH: "سنة",
    EvidenceKind.IJMA: "إجماع",
    EvidenceKind.QAIDA: "قاعدة فقهية",
    EvidenceKind.STANDARD: "معيار معاصر",
}


@dataclass(frozen=True, slots=True)
class Evidence:
    """One proof, quoted, with where it is found."""

    kind: EvidenceKind
    reference: str
    text: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "reference": self.reference, "text": self.text}


@dataclass(frozen=True, slots=True)
class Condition:
    """One rule: what it forbids, why, what it looks like, and whether it is live.

    ``status`` is **not** stored here. It is read from the decisions file, so that this
    module can be edited freely — adding a rule, correcting a quotation, widening a
    phrase list — without any edit ever silently turning a rule on.
    """

    code: str
    family: Family
    #: The bucket this refusal is reported under. ``None`` for a condition that answers
    #: the holder-return question instead — see :attr:`return_kind`.
    activity: Activity | None
    title_ar: str
    #: What the rule forbids, in the simplest Arabic that still says it exactly.
    meaning_ar: str
    #: How it shows up in a crypto project specifically. This is what makes the rule
    #: reviewable: an owner can picture the coin it would refuse.
    looks_like_ar: str
    #: The sentence a reader of the English site sees when this condition refuses a coin.
    reason_en: str
    evidence: tuple[Evidence, ...]
    agreement: Agreement
    detection: Detection
    #: Whole phrases, lowercase, as a project would write them about itself. Empty for
    #: anything :attr:`Detection.MANUAL` or :attr:`Detection.NUMERIC`.
    phrases: tuple[str, ...] = ()
    #: Why this rule exists in a form somebody may want to argue with. Optional.
    note_ar: str = ""
    #: Set instead of :attr:`activity` when the condition answers *where a holder's
    #: return comes from* rather than *what the project does*.
    #:
    #: This distinction is not tidiness, it is the single most expensive lesson in this
    #: module's history. "It pays a return" and "the return is riba" are two questions,
    #: and :class:`HolderReturn` is the only owner of the second. When both a blocking
    #: activity and the return question were allowed to answer it, a blind run refused
    #: Chainlink, Polygon, Hedera, NEAR, stETH and rETH — every one of them paid for
    #: validation work. A riba condition therefore never becomes a blocking activity;
    #: it feeds the return vocabulary, and the resolution order there decides.
    return_kind: HolderReturn | None = None

    def __post_init__(self) -> None:
        if (self.activity is None) == (self.return_kind is None):
            raise ValueError(
                f"{self.code}: a condition names exactly one of activity or return_kind"
            )
        if self.activity is Activity.INTEREST_BEARING_HOLDING:
            raise ValueError(
                f"{self.code}: interest_bearing_holding must never be a blocking "
                "activity. Use return_kind, so HolderReturn stays the only owner of "
                "the question 'where does this return come from'."
            )

    @property
    def is_detectable_from_text(self) -> bool:
        return self.detection is Detection.TEXT and bool(self.phrases)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "family": self.family.value,
            "family_ar": FAMILY_TITLE_AR[self.family],
            "activity": self.activity.value if self.activity else None,
            "return_kind": self.return_kind.value if self.return_kind else None,
            "title_ar": self.title_ar,
            "meaning_ar": self.meaning_ar,
            "looks_like_ar": self.looks_like_ar,
            "reason_en": self.reason_en,
            "evidence": [item.as_dict() for item in self.evidence],
            "agreement": self.agreement.value,
            "detection": self.detection.value,
            "phrases": list(self.phrases),
            "note_ar": self.note_ar,
            "status": status_of(self.code).value,
        }


def _q(reference: str, text: str) -> Evidence:
    return Evidence(EvidenceKind.QURAN, reference, text)


def _s(reference: str, text: str) -> Evidence:
    return Evidence(EvidenceKind.SUNNAH, reference, text)


def _i(reference: str, text: str) -> Evidence:
    return Evidence(EvidenceKind.IJMA, reference, text)


def _k(reference: str, text: str) -> Evidence:
    return Evidence(EvidenceKind.QAIDA, reference, text)


def _m(reference: str, text: str) -> Evidence:
    return Evidence(EvidenceKind.STANDARD, reference, text)


# The verses and hadiths quoted more than once, written once.
_RIBA_VERSE = _q("البقرة: 275", "وَأَحَلَّ اللَّهُ الْبَيْعَ وَحَرَّمَ الرِّبَا")
_RIBA_WAR = _q(
    "البقرة: 278-279",
    "يَا أَيُّهَا الَّذِينَ آمَنُوا اتَّقُوا اللَّهَ وَذَرُوا مَا بَقِيَ مِنَ الرِّبَا إِن كُنتُم "
    "مُّؤْمِنِينَ * فَإِن لَّمْ تَفْعَلُوا فَأْذَنُوا بِحَرْبٍ مِّنَ اللَّهِ وَرَسُولِهِ",
)
_RIBA_CURSE = _s(
    "مسلم: 1598",
    "لعن رسول الله ﷺ آكل الربا، وموكله، وكاتبه، وشاهديه، وقال: هم سواء",
)
_QARD_JARRA = _k(
    "قاعدة مقررة، نقل الإجماع عليها ابن قدامة في المغني",
    "كل قرض جر نفعاً فهو ربا",
)
_MAYSIR_VERSE = _q(
    "المائدة: 90",
    "إِنَّمَا الْخَمْرُ وَالْمَيْسِرُ وَالْأَنصَابُ وَالْأَزْلَامُ "
    "رِجْسٌ مِّنْ عَمَلِ الشَّيْطَانِ فَاجْتَنِبُوهُ",
)
_MAYSIR_HARM = _q(
    "المائدة: 91",
    "إِنَّمَا يُرِيدُ الشَّيْطَانُ أَن يُوقِعَ بَيْنَكُمُ الْعَدَاوَةَ "
    "وَالْبَغْضَاءَ فِي الْخَمْرِ وَالْمَيْسِرِ",
)
_GHARAR_HADITH = _s("مسلم: 1513", "نهى رسول الله ﷺ عن بيع الحصاة، وعن بيع الغرر")
_NOT_YOURS = _s("أبو داود: 3503، والترمذي: 1232", "لا تبع ما ليس عندك")
_BATIL_VERSE = _q("البقرة: 188", "وَلَا تَأْكُلُوا أَمْوَالَكُم بَيْنَكُم بِالْبَاطِلِ")
_TIJARA_VERSE = _q(
    "النساء: 29",
    "لَا تَأْكُلُوا أَمْوَالَكُم بَيْنَكُم بِالْبَاطِلِ "
    "إِلَّا أَن تَكُونَ تِجَارَةً عَن تَرَاضٍ مِّنكُمْ",
)
_GHISH_HADITH = _s("مسلم: 102", "من غش فليس مني")
_PRICE_OF_HARAM = _s("أبو داود: 3488", "إن الله إذا حرم على قوم أكل شيء حرم عليهم ثمنه")
_COOPERATION_VERSE = _q(
    "المائدة: 2",
    "وَتَعَاوَنُوا عَلَى الْبِرِّ وَالتَّقْوَىٰ وَلَا تَعَاوَنُوا عَلَى الْإِثْمِ وَالْعُدْوَانِ",
)
_NO_HARM = _k("ابن ماجه: 2341، ومالك في الموطأ", "لا ضرر ولا ضرار")
_SARF_HADITH = _s(
    "مسلم: 1587",
    "الذهب بالذهب، والفضة بالفضة... مثلاً بمثل، سواءً بسواء، يداً بيد، فإذا اختلفت هذه "
    "الأصناف فبيعوا كيف شئتم إذا كان يداً بيد",
)
_AAOIFI_21 = _m(
    "المعيار الشرعي رقم 21 — الأوراق المالية، هيئة المحاسبة والمراجعة للمؤسسات المالية "
    "الإسلامية (أيوفي)",
    "لا يجوز تملك أسهم الشركات التي يكون نشاطها الأصلي محرماً، وتُشترط حدود كمية للدخل "
    "المحرم والديون الربوية",
)


#: Every condition the methodology may ever apply, each written once.
#:
#: **Order matters only for reading.** Nothing in the code depends on it; the register is
#: grouped by family so the Arabic document generated from it reads as a document.
#:
#: The four conditions carrying today's live phrase lists — ``RB-01``, ``MY-01``,
#: ``GH-01`` and ``WG-02`` — hold **exactly** the vocabulary that was measured at 14/18
#: on 30 August 2026. They are copied across unchanged and deliberately not rewritten:
#: every new condition below them is additive, so approving none of them leaves the
#: measured behaviour identical, and approving one changes exactly one thing.
CONDITIONS: tuple[Condition, ...] = (
    # ---------------------------------------------------------------- الربا
    Condition(
        code="RB-01",
        family=Family.RIBA,
        activity=Activity.LENDING_BORROWING,
        title_ar="الإقراض بفائدة",
        meaning_ar="أن يكون عمل المشروع نفسه هو إقراض المال وأخذ زيادة عليه.",
        looks_like_ar=(
            "منصة تقول عن نفسها إنها بروتوكول إقراض، أو تعرض عليك أن تودع عملة وتأخذ "
            "عليها نسبة، أو أن تقترض مقابل ضمان."
        ),
        reason_en="The project's own business is lending money and charging for it.",
        evidence=(_RIBA_VERSE, _RIBA_WAR, _RIBA_CURSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "lending protocol", "lending platform", "lending market", "lending pool",
            "lending and borrowing", "borrowing and lending", "borrow and lend",
            "lend and borrow", "supply and borrow", "money market protocol",
            "decentralized money market", "collateralized loan", "collateralised loan",
            "over-collateralized borrowing", "borrow against your",
            "earn interest on your deposits", "interest rate model",
            "deposit assets and earn interest", "loan origination", "credit protocol",
            "peer-to-peer lending", "flash loan",
        ),
        note_ar=(
            "هذا الشرط مطبق فعلاً منذ 30 أغسطس 2026، وهو الذي يرفض AAVE و COMP و GHO."
        ),
    ),
    Condition(
        code="RB-02",
        family=Family.RIBA,
        activity=None,
        return_kind=HolderReturn.FROM_LENDING_OR_PROMISE,
        title_ar="عائد ثابت مضمون على مجرد الحفظ",
        meaning_ar=(
            "أن يوعد صاحب العملة بنسبة ربح ثابتة معلومة لمجرد أنه يحتفظ بها، من غير عمل "
            "ولا تحمل خسارة."
        ),
        looks_like_ar=(
            "موقع يقول: احتفظ بالعملة واحصل على 5% سنوياً مضمونة. الضمان هنا هو المشكلة، "
            "لا الربح."
        ),
        reason_en=(
            "Holding the token pays a fixed, guaranteed rate that is promised rather "
            "than earned."
        ),
        evidence=(_RIBA_VERSE, _QARD_JARRA, _AAOIFI_21),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "guaranteed apy", "guaranteed yield", "guaranteed return",
            "guaranteed interest", "fixed apy", "fixed interest rate",
            "risk-free yield", "risk free return", "guaranteed daily returns",
            "assured returns",
        ),
    ),
    Condition(
        code="RB-03",
        family=Family.RIBA,
        activity=None,
        return_kind=HolderReturn.FROM_LENDING_OR_PROMISE,
        title_ar="توكن يكبر رصيده تلقائياً من فائدة",
        meaning_ar=(
            "عملة يزيد عددها في محفظتك من نفسها، ومصدر الزيادة إقراض أو فائدة، لا عمل."
        ),
        looks_like_ar="عملة توصف بأنها rebasing أو interest-bearing، وتقول إن الرصيد ينمو كل يوم.",
        reason_en="The token's balance grows on its own out of interest, not out of work.",
        evidence=(_RIBA_VERSE, _RIBA_CURSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "accrues interest", "accrue interest", "interest-bearing token",
            "interest bearing token", "rebases daily", "rebasing token",
            "distributes interest to holders", "your balance grows automatically",
        ),
    ),
    Condition(
        code="RB-04",
        family=Family.RIBA,
        activity=None,
        return_kind=HolderReturn.FROM_LENDING_OR_PROMISE,
        title_ar="عائد من سندات وأذون خزانة",
        meaning_ar="أن يكون ربح حامل العملة آتياً من سندات أو أذون خزانة، وهي قروض بفائدة.",
        looks_like_ar=(
            "عملة مستقرة تقول إن احتياطها في أذون خزانة أمريكية وإن عائدها يوزع على الحاملين."
        ),
        reason_en=(
            "The holder's return comes from treasury bills, which are interest-bearing loans."
        ),
        evidence=(_RIBA_VERSE, _RIBA_CURSE, _AAOIFI_21),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "backed by treasury bills", "backed by t-bills", "treasury bill yield",
            "money market fund", "interest earned on the reserves",
            "yield from the reserve", "lending revenue is distributed to holders",
            "tokenized money market", "short-term treasuries",
        ),
    ),
    Condition(
        code="RB-05",
        family=Family.RIBA,
        activity=None,
        return_kind=HolderReturn.FROM_LENDING_OR_PROMISE,
        title_ar="وحدة ادخار بفائدة",
        meaning_ar="خدمة داخل المشروع اسمها الادخار، حقيقتها إقراض بفائدة معلومة.",
        looks_like_ar="بروتوكول فيه savings rate أو DSR يعطي نسبة على الإيداع.",
        reason_en="The project runs a savings product that pays a set interest rate on deposits.",
        evidence=(_RIBA_VERSE, _QARD_JARRA),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("savings rate", "savings module", "savings account rate", "deposit rate module"),
    ),
    Condition(
        code="RB-06",
        family=Family.RIBA,
        activity=Activity.CONVENTIONAL_FINANCE,
        title_ar="تمويل بالهامش",
        meaning_ar="إقراض المتداول مالاً بفائدة ليضاعف حجم صفقته.",
        looks_like_ar="منصة تعرض margin أو تقول اقترض لتتداول بحجم أكبر.",
        reason_en="The project lends traders money at interest so they can trade bigger.",
        evidence=(_RIBA_VERSE, _RIBA_CURSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "margin lending", "borrow to trade", "margin account",
            "interest on borrowed funds", "funding rate paid to lenders",
        ),
    ),
    Condition(
        code="RB-07",
        family=Family.RIBA,
        activity=Activity.CONVENTIONAL_FINANCE,
        title_ar="غرامة تأخير مالية",
        meaning_ar="زيادة مبلغ الدين على المدين لمجرد تأخره في السداد.",
        looks_like_ar="شروط تقول: عند التأخر تضاف نسبة على المبلغ المستحق.",
        reason_en="A late payer is charged extra money purely for being late.",
        evidence=(_RIBA_VERSE, _RIBA_CURSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "late payment interest", "penalty interest", "default interest", "overdue interest",
        ),
    ),
    Condition(
        code="RB-08",
        family=Family.RIBA,
        activity=Activity.LENDING_BORROWING,
        title_ar="قرض يجر نفعاً",
        meaning_ar="قرض يشترط فيه المقرض منفعة لنفسه، ولو لم تسم فائدة.",
        looks_like_ar="اقرض المشروع وستحصل على امتيازات أو حصة أو رسوم مخفضة مقابل القرض.",
        reason_en="A loan that brings the lender a benefit beyond the money lent.",
        evidence=(_QARD_JARRA, _RIBA_VERSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("lend and receive rewards", "loan bonus", "lenders receive a share of fees"),
    ),
    Condition(
        code="RB-09",
        family=Family.RIBA,
        activity=Activity.CONVENTIONAL_FINANCE,
        title_ar="بطاقة ائتمان بفوائد",
        meaning_ar="إصدار بطاقة ائتمان تحتسب فائدة على الرصيد غير المسدد.",
        looks_like_ar="مشروع يصدر بطاقة ويذكر معدل فائدة سنوي عليها.",
        reason_en="The project issues a credit card that charges interest on unpaid balances.",
        evidence=(_RIBA_VERSE, _RIBA_CURSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("credit card apr", "revolving credit", "interest on outstanding balance"),
    ),
    Condition(
        code="RB-10",
        family=Family.RIBA,
        activity=Activity.CONVENTIONAL_FINANCE,
        title_ar="تأمين تجاري تقليدي",
        meaning_ar=(
            "بيع وثيقة تأمين بمقابل، فيها جهالة كبيرة في العوض، واستثمار أقساطها بالربا."
        ),
        looks_like_ar="مشروع يبيع بوليصة تأمين تقليدية، لا تكافل تعاوني.",
        reason_en="The project sells conventional insurance policies.",
        evidence=(_GHARAR_HADITH, _MAYSIR_VERSE, _AAOIFI_21),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=(
            "conventional insurance", "insurance premium", "underwriting policies",
            "insurance underwriting",
        ),
        note_ar="التكافل التعاوني خارج هذا الشرط، والفرق بينهما أن التكافل تبرع لا معاوضة.",
    ),
    Condition(
        code="RB-11",
        family=Family.RIBA,
        activity=Activity.CONVENTIONAL_FINANCE,
        title_ar="بنك ربوي تقليدي",
        meaning_ar="أن يكون المشروع بنكاً أو ذراعاً لبنك يقوم عمله على الإقراض بفائدة.",
        looks_like_ar="عملة يصدرها بنك تقليدي وتوصف بأنها جزء من خدماته المصرفية.",
        reason_en="The project is a conventional bank or its arm.",
        evidence=(_RIBA_VERSE, _RIBA_CURSE, _AAOIFI_21),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("commercial bank", "retail banking", "banking license", "chartered bank"),
    ),
    Condition(
        code="RB-12",
        family=Family.RIBA,
        activity=Activity.TOKENIZED_SECURITY,
        title_ar="إصدار سندات بفائدة",
        meaning_ar="طرح دين على الناس بفائدة، سواء سمي سنداً أو ورقة دين رقمية.",
        looks_like_ar="مشروع يطرح bond أو note ويذكر كوبون أو عائداً سنوياً.",
        reason_en="The project issues interest-bearing bonds or notes.",
        evidence=(_RIBA_VERSE, _AAOIFI_21),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("bond issuance", "coupon payment", "corporate bond", "debt security offering"),
    ),
    Condition(
        code="RB-13",
        family=Family.RIBA,
        activity=Activity.DERIVATIVES_OR_LEVERAGE,
        title_ar="مبادلة أسعار الفائدة",
        meaning_ar="عقد يتبادل فيه الطرفان التزامات فائدة ثابتة ومتغيرة.",
        looks_like_ar="بروتوكول يقول إنه يتيح interest rate swap أو تثبيت العائد المتغير.",
        reason_en="The project sells interest-rate swaps.",
        evidence=(_RIBA_VERSE, _GHARAR_HADITH),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("interest rate swap", "rate swap market", "fixed for floating"),
    ),
    Condition(
        code="RB-14",
        family=Family.RIBA,
        activity=Activity.LENDING_BORROWING,
        title_ar="ربا الفضل في مبادلة الجنس الواحد",
        meaning_ar="مبادلة مال بجنسه مع زيادة في أحد العوضين.",
        looks_like_ar="مبادلة عملة بنفس العملة بكمية مختلفة داخل آلية المشروع.",
        reason_en="The protocol swaps a thing for the same thing in unequal amounts.",
        evidence=(_SARF_HADITH, _RIBA_VERSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.MANUAL,
        note_ar="لا يمكن إثباته من صفحات الموقع، ويحتاج قراءة آلية العقد نفسها.",
    ),
    Condition(
        code="RB-15",
        family=Family.RIBA,
        activity=Activity.CONVENTIONAL_FINANCE,
        title_ar="اشترِ الآن وادفع لاحقاً بزيادة",
        meaning_ar="تقسيط بزيادة على الثمن مقابل الأجل، مع كون العقد قرضاً لا بيعاً.",
        looks_like_ar="خدمة تقول: قسّط مشترياتك، ثم تذكر رسوم تمويل أو نسبة سنوية.",
        reason_en="The project finances purchases and charges for the delay itself.",
        evidence=(_RIBA_VERSE, _QARD_JARRA),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("buy now pay later", "installment financing fee", "financing charge"),
        note_ar=(
            "البيع بالتقسيط بثمن أعلى معلوم عند العقد جائز عند الجمهور، والممنوع هو القرض بزيادة."
        ),
    ),
    # -------------------------------------------------------------- الميسر
    Condition(
        code="MY-01",
        family=Family.MAYSIR,
        activity=Activity.GAMBLING,
        title_ar="القمار والمراهنة",
        meaning_ar="أن يدفع الإنسان مالاً على احتمال، فإما غنم بلا عمل وإما غرم بلا عوض.",
        looks_like_ar="كازينو، مراهنات، يانصيب، أو أي لعبة تدفع فيها لتربح بالحظ.",
        reason_en="The project's own business is betting or a casino.",
        evidence=(
            _MAYSIR_VERSE, _MAYSIR_HARM,
            _q(
                "البقرة: 219",
                "يَسْأَلُونَكَ عَنِ الْخَمْرِ وَالْمَيْسِرِ قُلْ فِيهِمَا إِثْمٌ كَبِيرٌ",
            ),
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "casino", "sportsbook", "sports betting", "betting platform",
            "betting protocol", "place a bet", "place bets", "lottery", "raffle",
            "jackpot", "wagering", "wager on", "gambling platform", "dice game",
        ),
        note_ar="هذا الشرط مطبق فعلاً منذ 30 أغسطس 2026.",
    ),
    Condition(
        code="MY-02",
        family=Family.MAYSIR,
        activity=Activity.GAMBLING,
        title_ar="أسواق التنبؤ",
        meaning_ar="بيع وشراء عقود على وقوع حدث مستقبلي، والربح فيها من خسارة الطرف الآخر.",
        looks_like_ar="منصة تقول: راهن على نتيجة الانتخابات أو على سعر عملة في تاريخ معين.",
        reason_en="The project runs a market for betting on future events.",
        evidence=(_MAYSIR_VERSE, _GHARAR_HADITH),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=(
            "predict the outcome and win", "event contracts", "bet on the outcome",
        ),
        note_ar=(
            "بعض المعاصرين يفرق بين التنبؤ للتحوط والتنبؤ للمقامرة، والتطبيق هنا على "
            "الثاني. ولفظ prediction market نفسه مملوك للشرط GH-01 المطبق فعلاً، ولا "
            "يكرر هنا: اللفظ الواحد لا يكون تحت شرطين، وإلا لم يعرف أحد أي شرط رفض العملة."
        ),
    ),
    Condition(
        code="MY-03",
        family=Family.MAYSIR,
        activity=Activity.GAMBLING,
        title_ar="صناديق عشوائية مدفوعة",
        meaning_ar="أن تدفع ثمناً معلوماً لتحصل على شيء مجهول قيمته تتفاوت بالحظ.",
        looks_like_ar="لعبة تبيع loot box أو mystery box، تدفع فيها لتفتح مفاجأة.",
        reason_en="The project sells paid boxes whose contents are decided by chance.",
        evidence=(_MAYSIR_VERSE, _GHARAR_HADITH),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("loot box", "loot boxes", "mystery box", "mystery boxes", "gacha", "blind box"),
    ),
    Condition(
        code="MY-04",
        family=Family.MAYSIR,
        activity=Activity.GAMBLING,
        title_ar="عجلة الحظ والدوران بمقابل",
        meaning_ar="دفع مال مقابل دورة عشوائية قد تعطي جائزة وقد لا تعطي شيئاً.",
        looks_like_ar="زر spin to win يطلب رسوماً قبل الدوران.",
        reason_en="The project charges for a random spin that may or may not pay out.",
        evidence=(_MAYSIR_VERSE,),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("spin to win", "wheel of fortune", "prize wheel", "spin the wheel"),
    ),
    Condition(
        code="MY-05",
        family=Family.MAYSIR,
        activity=Activity.GAMBLING,
        title_ar="بطولة برسم دخول وجائزتها من الرسوم",
        meaning_ar=(
            "مسابقة يدفع فيها كل المشتركين، ويأخذ الفائز ما دفعوه. هذا هو صورة القمار "
            "الصريحة."
        ),
        looks_like_ar="لعبة تقول: ادفع للدخول، والفائز يأخذ مجموع رسوم الدخول.",
        reason_en="Entrants pay a fee and the winner takes the pooled fees.",
        evidence=(_MAYSIR_VERSE, _BATIL_VERSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "entry fee prize pool", "winner takes the pot", "buy-in tournament",
            "stake to enter and win",
        ),
        note_ar="لو كانت الجائزة من طرف ثالث لا من المشتركين، خرجت عن القمار عند الجمهور.",
    ),
    Condition(
        code="MY-06",
        family=Family.MAYSIR,
        activity=Activity.GAMBLING,
        title_ar="ألعاب النرد والروليت",
        meaning_ar="ألعاب قائمة على الحظ المحض ورد النهي عنها بعينها.",
        looks_like_ar="لعبة روليت أو نرد داخل المشروع بمقابل مالي.",
        reason_en="The project runs dice or roulette games for money.",
        evidence=(
            _MAYSIR_VERSE,
            _s("مسلم: 2260", "من لعب بالنردشير فكأنما صبغ يده في لحم خنزير ودمه"),
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("roulette", "dice roll game", "crash game", "plinko"),
    ),
    Condition(
        code="MY-07",
        family=Family.MAYSIR,
        activity=Activity.GAMBLING,
        title_ar="اللعب مقابل الربح إذا كان الدخول بمقابل",
        meaning_ar="أن يشترط على اللاعب شراء أصل ليدخل، ثم يكون عائده معلقاً على الحظ.",
        looks_like_ar="لعبة play-to-earn تفرض شراء NFT للدخول وعائدها عشوائي.",
        reason_en="Players must pay to enter and what they earn is decided by chance.",
        evidence=(_MAYSIR_VERSE, _GHARAR_HADITH),
        agreement=Agreement.DISPUTED,
        detection=Detection.TEXT,
        phrases=("pay to play and earn", "purchase required to earn", "random rewards for players"),
        note_ar=(
            "اللعب مقابل الربح ليس محرماً لذاته؛ الممنوع اجتماع الدفع للدخول مع العشوائية في "
            "العائد."
        ),
    ),
    # -------------------------------------------------------------- الغرر
    Condition(
        code="GH-01",
        family=Family.GHARAR,
        activity=Activity.DERIVATIVES_OR_LEVERAGE,
        title_ar="المشتقات والرافعة المالية",
        meaning_ar=(
            "عقود لا يقبض فيها شيء ولا يملك، وإنما تسوى فروق أسعار، وفيها غرر ورهان على "
            "السعر."
        ),
        looks_like_ar="منصة تقول عن نفسها perp dex أو تعرض عقوداً دائمة أو خيارات أو رافعة.",
        reason_en="The project's own business is leverage, perpetuals or other derivatives.",
        evidence=(_GHARAR_HADITH, _NOT_YOURS, _MAYSIR_VERSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "perpetual futures", "perpetual contract", "perpetual swap", "perp dex",
            "futures exchange", "futures trading", "margin trading", "leveraged trading",
            "leveraged token", "leverage up to", "options protocol", "options trading",
            "options vault", "synthetic asset", "synthetic exposure",
            "prediction market", "binary option", "contract for difference",
            "short and long positions", "derivatives exchange", "derivatives protocol",
        ),
        note_ar="هذا الشرط مطبق فعلاً منذ 30 أغسطس 2026، وهو الذي رفض Synthetix و Hyperliquid.",
    ),
    Condition(
        code="GH-02",
        family=Family.GHARAR,
        activity=Activity.SELLING_WHAT_IS_NOT_OWNED,
        title_ar="البيع على المكشوف",
        meaning_ar="بيع ما لا يملكه البائع أصلاً، على أمل شرائه أرخص فيما بعد.",
        looks_like_ar="منصة تقول: افتح صفقة بيع على عملة لا تملكها.",
        reason_en="The project lets people sell what they do not own.",
        evidence=(_NOT_YOURS, _GHARAR_HADITH),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("short selling", "sell short", "borrow and sell", "naked short"),
    ),
    Condition(
        code="GH-03",
        family=Family.GHARAR,
        activity=Activity.SELLING_WHAT_IS_NOT_OWNED,
        title_ar="البيع قبل القبض",
        meaning_ar="بيع ما اشتراه الإنسان قبل أن يحوزه ويستقر في ضمانه.",
        looks_like_ar="سوق يبيع فيه الناس أصولاً لم تسلم لهم بعد، أو حقوق شراء مستقبلية.",
        reason_en="Things are resold before the seller has actually taken possession.",
        evidence=(
            _s("البخاري: 2136، ومسلم: 1525", "من ابتاع طعاماً فلا يبعه حتى يستوفيه"),
            _s("أبو داود: 3503، من حديث حكيم بن حزام", "فإذا اشتريت بيعاً فلا تبعه حتى تقبضه"),
        ),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("presale resale", "trade before delivery", "sell your allocation before"),
    ),
    Condition(
        code="GH-04",
        family=Family.GHARAR,
        activity=Activity.DEBT_TRADING,
        title_ar="بيع الدين بالدين",
        meaning_ar="مبادلة دين مؤجل بدين مؤجل آخر، وهو الكالئ بالكالئ.",
        looks_like_ar="بروتوكول يبادل التزامات دين آجلة بعضها ببعض.",
        reason_en="The project exchanges one deferred debt for another.",
        evidence=(
            _i("نقل الإجماع ابن المنذر والإمام أحمد", "أجمعوا على أن بيع الدين بالدين لا يجوز"),
            _GHARAR_HADITH,
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("debt for debt", "swap one obligation for another", "roll over the debt"),
    ),
    Condition(
        code="GH-05",
        family=Family.GHARAR,
        activity=Activity.DEBT_TRADING,
        title_ar="بيع الدين بأقل منه",
        meaning_ar="بيع دين في ذمة الغير بمبلغ أقل نقداً، فتكون الزيادة في مقابل الأجل.",
        looks_like_ar="منصة تشتري فواتير أو مستحقات بخصم وتبيعها للمستثمرين.",
        reason_en="The project buys debts at a discount and sells them on.",
        evidence=(_RIBA_VERSE, _i("جمهور الفقهاء", "لا يجوز بيع الدين لغير من هو عليه بأقل منه")),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=(
            "invoice factoring", "receivables at a discount", "discounted notes",
            "sell your receivables",
        ),
    ),
    Condition(
        code="GH-06",
        family=Family.GHARAR,
        activity=Activity.UNBACKED_COMMODITY_CLAIM,
        title_ar="احتياطي غير معلن ولا مدقق",
        meaning_ar=(
            "أن تدعي العملة أنها مغطاة، ولا تبين بماذا ولا أين، ولا يوجد تدقيق مستقل."
        ),
        looks_like_ar="عملة مستقرة تقول fully backed ولا تنشر أي تقرير عن الاحتياطي.",
        reason_en=(
            "The project claims backing it never shows, so the buyer cannot know what he owns."
        ),
        evidence=(_GHARAR_HADITH, _GHISH_HADITH),
        agreement=Agreement.MAJORITY,
        detection=Detection.MANUAL,
        note_ar="لا يثبت بالكلمات، لأن غياب التقرير لا يظهر في نص الصفحة. يحتاج مراجعة إنسان.",
    ),
    Condition(
        code="GH-07",
        family=Family.GHARAR,
        activity=Activity.UNBACKED_COMMODITY_CLAIM,
        title_ar="ذهب أو فضة رقمية بلا تغطية محددة",
        meaning_ar=(
            "بيع ذهب أو فضة رقمياً من غير أن يكون هناك ذهب معين محدد مملوك يقابل كل وحدة."
        ),
        looks_like_ar="توكن ذهب يقول إنه مدعوم بالذهب دون تخصيص ولا حفظ ولا حق استرداد.",
        reason_en="A gold or silver token with no specific allocated metal behind it.",
        evidence=(_SARF_HADITH, _GHARAR_HADITH),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("unallocated gold", "pooled gold account", "gold exposure without delivery"),
    ),
    Condition(
        code="GH-08",
        family=Family.GHARAR,
        activity=Activity.DERIVATIVES_OR_LEVERAGE,
        title_ar="أصول اصطناعية تحاكي أصولاً محرمة",
        meaning_ar="عقد يعطيك ربح أصل لا تملكه، وقد يكون الأصل نفسه محرماً كسهم بنك ربوي.",
        looks_like_ar="بروتوكول يصدر نسخة اصطناعية من سهم أو مؤشر أو سلعة.",
        reason_en="The project mints synthetic copies of assets nobody actually holds.",
        evidence=(_GHARAR_HADITH, _NOT_YOURS),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("synthetic stock", "mirrored asset", "tracks the price without owning"),
    ),
    Condition(
        code="GH-09",
        family=Family.GHARAR,
        activity=Activity.TOKENIZED_SECURITY,
        title_ar="ترميز أوراق مالية",
        meaning_ar=(
            "أن تمثل العملة سهماً أو سنداً. هذا لا يفحص هنا، لأن حكمه يتبع الشركة نفسها."
        ),
        looks_like_ar="توكن يقول إنه يمثل سهماً في شركة أو حصة في صندوق.",
        reason_en="The token stands for a share or a bond, which this product does not screen.",
        evidence=(_AAOIFI_21, _GHARAR_HADITH),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "tokenized stock", "tokenized stocks", "tokenised stock", "tokenised stocks",
            "tokenized equity", "tokenized equities", "tokenised equity",
            "tokenised equities", "tokenized security", "tokenized securities",
            "tokenised security", "tokenised securities", "security token offering",
            "tokenized treasury", "tokenized treasuries", "tokenized bond",
            "tokenized bonds", "represents a share in the company", "equity token",
            "share certificate",
        ),
        note_ar=(
            "هذا الشرط مطبق فعلاً منذ 30 أغسطس 2026. وهو رفض بمعنى «خارج نطاق الفحص»، لا بمعنى "
            "التحريم."
        ),
    ),
    # ------------------------------------------------ المحرمات لذاتها
    Condition(
        code="PR-01",
        family=Family.PROHIBITED,
        activity=Activity.PROHIBITED_GOODS,
        title_ar="الخمر وصناعتها وبيعها",
        meaning_ar="الاتجار في المسكرات بأي صورة، إنتاجاً أو بيعاً أو نقلاً أو تسويقاً.",
        looks_like_ar="مشروع يبيع الخمور أو يخدم مصانعها أو يبني سوقاً لها.",
        reason_en="The project's business is alcohol.",
        evidence=(
            _MAYSIR_VERSE,
            _s(
                "أبو داود: 3674، وابن ماجه: 3380",
                "لعن الله الخمر، وشاربها، وساقيها، وبائعها، ومبتاعها، وعاصرها، ومعتصرها، "
                "وحاملها، والمحمولة إليه، وآكل ثمنها",
            ),
            _PRICE_OF_HARAM,
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "alcohol brand", "wine merchant", "liquor store", "brewery", "distillery",
            "beer company",
        ),
    ),
    Condition(
        code="PR-02",
        family=Family.PROHIBITED,
        activity=Activity.PROHIBITED_GOODS,
        title_ar="لحم الخنزير والميتة",
        meaning_ar="الاتجار في لحم الخنزير أو الميتة أو الدم.",
        looks_like_ar="سلسلة إمداد غذائي على البلوك تشين تتاجر في هذه الأصناف.",
        reason_en="The project's business is pork or carrion.",
        evidence=(
            _q("المائدة: 3", "حُرِّمَتْ عَلَيْكُمُ الْمَيْتَةُ وَالدَّمُ وَلَحْمُ الْخِنزِيرِ"),
            _PRICE_OF_HARAM,
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("pork products", "pork supply chain", "bacon brand", "non-halal meat"),
    ),
    Condition(
        code="PR-03",
        family=Family.PROHIBITED,
        activity=Activity.PROHIBITED_GOODS,
        title_ar="المخدرات",
        meaning_ar="الاتجار في المواد المخدرة، وهي داخلة في معنى المسكر.",
        looks_like_ar="سوق رقمي يبيع مواد مخدرة أو يخدم تجارتها.",
        reason_en="The project's business is narcotics.",
        evidence=(
            _MAYSIR_VERSE,
            _s("مسلم: 2003", "كل مسكر خمر، وكل مسكر حرام"),
            _NO_HARM,
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("recreational cannabis", "narcotics marketplace", "drug marketplace"),
    ),
    Condition(
        code="PR-04",
        family=Family.PROHIBITED,
        activity=Activity.PROHIBITED_GOODS,
        title_ar="التبغ",
        meaning_ar="الاتجار في التبغ ومنتجاته.",
        looks_like_ar="مشروع يخدم شركات السجائر أو يبيع منتجات التبغ.",
        reason_en="The project's business is tobacco.",
        evidence=(_NO_HARM, _AAOIFI_21),
        agreement=Agreement.DISPUTED,
        detection=Detection.TEXT,
        phrases=("tobacco company", "cigarette brand", "vaping products"),
        note_ar="بعض العلماء يراه مكروهاً لا محرماً، لكن معايير التصفية المعاصرة تستبعده.",
    ),
    Condition(
        code="PR-05",
        family=Family.PROHIBITED,
        activity=Activity.ADULT_OR_IMMORAL_TRADE,
        title_ar="المحتوى الإباحي",
        meaning_ar="إنتاج المحتوى الإباحي أو توزيعه أو تسهيل الدفع له.",
        looks_like_ar="منصة محتوى للبالغين، أو عملة وسيلة دفع لمواقع إباحية.",
        reason_en="The project's business is adult content.",
        evidence=(
            _q("النور: 30", "قُل لِّلْمُؤْمِنِينَ يَغُضُّوا مِنْ أَبْصَارِهِمْ"),
            _q("الإسراء: 32", "وَلَا تَقْرَبُوا الزِّنَا إِنَّهُ كَانَ فَاحِشَةً وَسَاءَ سَبِيلًا"),
            _COOPERATION_VERSE,
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "adult content platform", "adult entertainment", "pornography", "camming platform",
            "nsfw marketplace",
        ),
    ),
    Condition(
        code="PR-06",
        family=Family.PROHIBITED,
        activity=Activity.ADULT_OR_IMMORAL_TRADE,
        title_ar="الدعارة والاتجار بالبشر",
        meaning_ar="تسهيل البغاء أو بيع البشر بأي وسيلة.",
        looks_like_ar="خدمة تدعي الخصوصية وتسوق نفسها لهذا الغرض.",
        reason_en="The project facilitates prostitution or trafficking.",
        evidence=(
            _q("النور: 33", "وَلَا تُكْرِهُوا فَتَيَاتِكُمْ عَلَى الْبِغَاءِ"),
            _s("البخاري: 2227", "ثلاثة أنا خصمهم يوم القيامة... ورجل باع حراً فأكل ثمنه"),
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("escort service", "human trafficking",),
    ),
    Condition(
        code="PR-07",
        family=Family.PROHIBITED,
        activity=Activity.OCCULT_OR_IDOLATRY,
        title_ar="السحر والتنجيم والكهانة",
        meaning_ar="بيع خدمات الغيب المزعومة، من تنجيم وقراءة طالع وسحر.",
        looks_like_ar="تطبيق يبيع قراءة الأبراج أو التنبؤ بالمستقبل مقابل عملته.",
        reason_en="The project sells fortune telling, astrology or sorcery.",
        evidence=(
            _MAYSIR_VERSE,
            _s("أبو داود: 3904", "من أتى كاهناً فصدقه بما يقول، فقد كفر بما أنزل على محمد ﷺ"),
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "astrology readings", "fortune telling", "tarot reading", "psychic reading",
            "horoscope service",
        ),
    ),
    Condition(
        code="PR-08",
        family=Family.PROHIBITED,
        activity=Activity.OCCULT_OR_IDOLATRY,
        title_ar="الأصنام وشعائر الشرك",
        meaning_ar="صناعة ما يعبد من دون الله أو الاتجار فيه أو خدمة طقوسه.",
        looks_like_ar="مشروع NFT يبيع تماثيل للعبادة أو يخدم معابد وطقوساً شركية.",
        reason_en="The project's business is idols or the rites of other-than-God worship.",
        evidence=(
            _MAYSIR_VERSE,
            _q(
                "الحج: 30",
                "فَاجْتَنِبُوا الرِّجْسَ مِنَ الْأَوْثَانِ وَاجْتَنِبُوا قَوْلَ الزُّورِ"
            ),
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("idol worship", "occult rituals", "temple offerings platform"),
    ),
    Condition(
        code="PR-09",
        family=Family.PROHIBITED,
        activity=Activity.UNLAWFUL_WEAPONS,
        title_ar="سلاح للعدوان",
        meaning_ar="بيع السلاح لمن يستخدمه في عدوان محرم أو في فتنة بين المسلمين.",
        looks_like_ar="سوق سلاح غير مرخص، أو منصة تمول تسليح جهة معتدية.",
        reason_en="The project's business is arming unlawful aggression.",
        evidence=(
            _COOPERATION_VERSE,
            _s("مسلم: 2564", "كل المسلم على المسلم حرام: دمه، وماله، وعرضه"),
        ),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("weapons marketplace", "arms dealing", "unlicensed firearms"),
        note_ar="السلاح ليس محرماً لذاته، والمنع متعلق بالغرض. لذلك يحتاج مراجعة إنسان في الغالب.",
    ),
    Condition(
        code="PR-10",
        family=Family.PROHIBITED,
        activity=Activity.DISPUTED_ENTERTAINMENT,
        title_ar="صناعة الترفيه المحرم",
        meaning_ar="أن يكون النشاط الأصلي إنتاج الموسيقى أو الأفلام أو الملاهي.",
        looks_like_ar="منصة بث موسيقي أو استوديو أفلام يصدر عملته.",
        reason_en="The project's core business is music, film or nightlife.",
        evidence=(
            _q(
                "لقمان: 6",
                "وَمِنَ النَّاسِ مَن يَشْتَرِي لَهْوَ الْحَدِيثِ لِيُضِلَّ عَن سَبِيلِ اللَّهِ"
            ),
            _AAOIFI_21,
        ),
        agreement=Agreement.DISPUTED,
        detection=Detection.TEXT,
        phrases=("music label", "record label", "nightclub", "film studio"),
        note_ar=(
            "خلاف معتبر. معايير أيوفي والمؤشرات الإسلامية تستبعد السينما والموسيقى، وبعض "
            "المعاصرين يفرق بين المحتوى النافع والماجن. أوصي بمناقشته مع شيخ قبل اعتماده."
        ),
    ),
    # -------------------------------------------------------------- الغش
    Condition(
        code="DC-01",
        family=Family.DECEIT,
        activity=Activity.PONZI_OR_PYRAMID,
        title_ar="مخطط بونزي",
        meaning_ar="دفع أرباح القدامى من أموال الجدد، لا من ربح حقيقي.",
        looks_like_ar="مشروع يعد بعائد ثابت مرتفع ولا يبين من أين يأتي المال.",
        reason_en="Earlier entrants are paid out of later entrants' money.",
        evidence=(_BATIL_VERSE, _GHISH_HADITH, _TIJARA_VERSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("returns paid from new deposits", "guaranteed daily profit"),
        note_ar=(
            "لفظ ponzi وحده أُخرج من القائمة عمداً. لا يوجد مشروع يصف نفسه بأنه احتيال، "
            "فالكلمة لا تظهر إلا في سياق الرد على التهمة. وقد أثبت القياس الأعمى ذلك: "
            "في 31 أغسطس 2026 التقطها هذا الشرط على إيثيريوم، من منشور في منتدى يرد على "
            "الاتهام ويقول إنه مغالطة. لأن الشرط كان مقترحاً لا مطبقاً، لم يرفض شيئاً، "
            "وظهر في التقرير فقط — وهذا هو سبب وجود مرحلة الاقتراح أصلاً."
        ),
    ),
    Condition(
        code="DC-02",
        family=Family.DECEIT,
        activity=Activity.PONZI_OR_PYRAMID,
        title_ar="التسويق الهرمي",
        meaning_ar="أن يكون الكسب الأساسي من ضم أعضاء جدد لا من بيع منفعة حقيقية.",
        looks_like_ar="مشروع يقول: ادع صديقاً واكسب من كل مستوى تحتك.",
        reason_en="The main way to earn is recruiting others, not selling anything real.",
        evidence=(_BATIL_VERSE, _GHISH_HADITH),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "multi-level marketing", "downline commission", "recruit members to earn",
            "matrix compensation",
        ),
        note_ar="برامج الإحالة البسيطة بمكافأة واحدة ليست هرمية، والفرق هو تعدد المستويات.",
    ),
    Condition(
        code="DC-03",
        family=Family.DECEIT,
        activity=Activity.MARKET_MANIPULATION,
        title_ar="التداول الوهمي",
        meaning_ar="بيع وشراء صوري لإظهار سيولة أو نشاط لا وجود له.",
        looks_like_ar="منصة تفاخر بحجم تداول مصنوع، أو تكافئ على صفقات وهمية.",
        reason_en="The project fakes trading volume.",
        evidence=(_GHISH_HADITH, _BATIL_VERSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.MANUAL,
        note_ar="لا يعترف مشروع بهذا في صفحاته، فلا سبيل لإثباته إلا بتحليل بيانات السوق.",
    ),
    Condition(
        code="DC-04",
        family=Family.DECEIT,
        activity=Activity.MARKET_MANIPULATION,
        title_ar="النجش ورفع السعر بلا نية شراء",
        meaning_ar="أن يزايد على السلعة من لا يريد شراءها ليغري غيره.",
        looks_like_ar="مجموعات منظمة داخل المشروع لرفع السعر ثم البيع.",
        reason_en="Prices are pushed up by people with no intention of buying.",
        evidence=(
            _s("البخاري: 2142، ومسلم: 1516", "نهى النبي ﷺ عن النجش"),
            _GHISH_HADITH,
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("pump group", "coordinated pump", "pump and dump"),
    ),
    Condition(
        code="DC-05",
        family=Family.DECEIT,
        activity=Activity.DECEPTIVE_DISCLOSURE,
        title_ar="صلاحيات خفية في العقد",
        meaning_ar=(
            "أن يحتفظ المصدر بقدرة على سك عملات جديدة أو تجميد أموال الناس دون إفصاح."
        ),
        looks_like_ar="عقد فيه دالة سك مفتوحة أو قائمة حظر، والموقع لا يذكرها.",
        reason_en="The issuer keeps hidden power over holders' money.",
        evidence=(_GHISH_HADITH, _GHARAR_HADITH, _BATIL_VERSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.MANUAL,
        note_ar="يحتاج قراءة العقد الذكي نفسه، وهذا خارج ما يستطيعه قارئ الصفحات اليوم.",
    ),
    Condition(
        code="DC-06",
        family=Family.DECEIT,
        activity=Activity.DECEPTIVE_DISCLOSURE,
        title_ar="ادعاء شراكات أو تدقيق غير حقيقي",
        meaning_ar="نسبة المشروع لنفسه اعتمادات أو شركاء لا وجود لهم.",
        looks_like_ar="موقع يعرض شعارات شركات كبرى دون أي علاقة معها.",
        reason_en="The project claims partners or audits it does not have.",
        evidence=(_GHISH_HADITH, _q("الحج: 30", "وَاجْتَنِبُوا قَوْلَ الزُّورِ")),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.MANUAL,
    ),
    Condition(
        code="DC-07",
        family=Family.DECEIT,
        activity=Activity.DECEPTIVE_DISCLOSURE,
        title_ar="باب سرقة في العقد",
        meaning_ar="آلية تمكن الفريق من سحب سيولة المشروع أو منع الناس من البيع.",
        looks_like_ar="عقد honeypot تشتري فيه ولا تستطيع الخروج.",
        reason_en="The contract lets the team drain the money or trap holders.",
        evidence=(_BATIL_VERSE, _GHISH_HADITH, _NO_HARM),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.MANUAL,
    ),
    # ------------------------------------------------ أكل المال بالباطل
    Condition(
        code="WG-01",
        family=Family.WRONGFUL_GAIN,
        activity=Activity.NO_UNDERLYING_UTILITY,
        title_ar="لا منفعة ولا منتج خلف العملة",
        meaning_ar="عملة ليس وراءها عمل ولا خدمة ولا أصل، وإنما قيمتها من إقبال الناس فقط.",
        looks_like_ar="عملة ميم لا يوجد لها منتج ولا فريق يعمل على شيء.",
        reason_en="There is no protocol, product or service behind the token.",
        evidence=(_BATIL_VERSE, _TIJARA_VERSE),
        agreement=Agreement.DISPUTED,
        detection=Detection.MANUAL,
        note_ar=(
            "مطبق فعلاً، لكنه لا يثبت من الكلمات أبداً، لأن أحداً لا يكتب «ليس لنا منتج». "
            "ولاحظ أن Fasset تصنف DOGE و SHIB و PEPE متوافقة، فاعتماد إثباته يدوياً يعني "
            "الخروج عن حدها إلى حد أضيق. هذا قرارك أنت."
        ),
    ),
    Condition(
        code="WG-02",
        family=Family.WRONGFUL_GAIN,
        activity=Activity.NO_UNDERLYING_UTILITY,
        title_ar="قيمة قائمة على المشتري التالي فقط",
        meaning_ar="أن يصرح المشروع بأن الربح يأتي من دخول مشترين بعدك، لا من قيمة يصنعها.",
        looks_like_ar="موقع يقول صراحة: كلما دخل أناس أكثر ارتفع سعرك.",
        reason_en="The project says outright that gains come from later buyers arriving.",
        evidence=(_BATIL_VERSE, _MAYSIR_VERSE),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("price goes up as more people buy in", "early buyers profit from later buyers"),
    ),
    Condition(
        code="WG-03",
        family=Family.WRONGFUL_GAIN,
        activity=Activity.HOARDING_OR_CONTROL,
        title_ar="الاحتكار",
        meaning_ar="حبس ما يحتاجه الناس ليرتفع سعره ثم بيعه عليهم.",
        looks_like_ar="مشروع يصرح بأنه يحبس المعروض ليرفع السعر.",
        reason_en="The project withholds supply to push the price up.",
        evidence=(
            _s("مسلم: 1605", "لا يحتكر إلا خاطئ"),
            _NO_HARM,
        ),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("supply squeeze", "restrict supply to raise the price"),
    ),
    Condition(
        code="WG-04",
        family=Family.WRONGFUL_GAIN,
        activity=Activity.HOARDING_OR_CONTROL,
        title_ar="تركز الملكية بما يمكّن من التلاعب",
        meaning_ar="أن تكون أغلبية العملات في يد قلة تستطيع تحريك السعر والحوكمة كما تشاء.",
        looks_like_ar="فريق يملك أغلب المعروض ويتحكم في التصويت.",
        reason_en="A small group holds enough of the supply to move the price and the votes.",
        evidence=(_NO_HARM, _BATIL_VERSE),
        agreement=Agreement.MAJORITY,
        detection=Detection.NUMERIC,
        note_ar="يحتاج بيانات توزيع الملكية، والمنتج لا يجمعها اليوم.",
    ),
    Condition(
        code="WG-05",
        family=Family.WRONGFUL_GAIN,
        activity=Activity.ILLICIT_FINANCE_SERVICE,
        title_ar="خدمة لغسل الأموال أو التهرب",
        meaning_ar=(
            "أن يسوق المشروع نفسه بأنه وسيلة لإخفاء مصدر المال أو التهرب من حق واجب."
        ),
        looks_like_ar="خدمة تقول: أخفِ أموالك عن الجهات، أو تجاوز إجراءات التحقق.",
        reason_en="The project sells itself as a way to hide money or escape lawful duty.",
        evidence=(_BATIL_VERSE, _COOPERATION_VERSE),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("evade sanctions", "bypass kyc", "launder", "untraceable payments to avoid"),
        note_ar=(
            "الخصوصية في ذاتها ليست محرمة، والشرط منصب على من يسوق نفسه للتحايل. "
            "لذلك الألفاظ هنا ضيقة عمداً."
        ),
    ),
    # ------------------------------------------------------ صيغة العقد
    Condition(
        code="CF-01",
        family=Family.CONTRACT_FORM,
        activity=Activity.CORRUPT_CONTRACT_FORM,
        title_ar="بيعتان في بيعة",
        meaning_ar="أن يجمع العقد بين صفقتين على وجه لا يتحدد معه الثمن أو المبيع.",
        looks_like_ar="عرض يربط شراء أصل بالتزام بيعه لاحقاً بسعر محدد.",
        reason_en="Two sales are bundled into one so the price is never settled.",
        evidence=(
            _s("الترمذي: 1231", "نهى رسول الله ﷺ عن بيعتين في بيعة"),
            _GHARAR_HADITH,
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.MANUAL,
    ),
    Condition(
        code="CF-02",
        family=Family.CONTRACT_FORM,
        activity=Activity.CORRUPT_CONTRACT_FORM,
        title_ar="بيع وسلف",
        meaning_ar="اشتراط قرض داخل عقد بيع، فيصير القرض ثمناً خفياً.",
        looks_like_ar="اشترِ العملة بشرط أن تقرض المشروع مبلغاً.",
        reason_en="A sale is tied to a loan, so the loan becomes a hidden price.",
        evidence=(
            _s(
                "أبو داود: 3504، والترمذي: 1234",
                "لا يحل سلف وبيع، ولا شرطان في بيع، ولا ربح ما لم تضمن، ولا بيع ما ليس عندك"
            ),
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.MANUAL,
    ),
    Condition(
        code="CF-03",
        family=Family.CONTRACT_FORM,
        activity=Activity.GUARANTEED_CAPITAL_OR_RETURN,
        title_ar="ضمان رأس المال في المشاركة",
        meaning_ar=(
            "أن يضمن الشريك أو المضارب رأس المال أو ربحاً معلوماً، فينقلب العقد إلى قرض بفائدة."
        ),
        looks_like_ar="صندوق يقول: استثمر معنا ورأس مالك مضمون مهما حدث.",
        reason_en=(
            "A partnership guarantees the capital or a set profit, which turns it into a loan."
        ),
        evidence=(
            _s("أبو داود: 3508، والترمذي: 1285", "الخراج بالضمان"),
            _RIBA_VERSE,
            _AAOIFI_21,
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("capital protected", "principal guaranteed", "your capital is safe guaranteed"),
    ),
    Condition(
        code="CF-04",
        family=Family.CONTRACT_FORM,
        activity=Activity.GUARANTEED_CAPITAL_OR_RETURN,
        title_ar="ربح ما لم يضمن",
        meaning_ar="أن يأخذ الإنسان ربح شيء لم يدخل في ضمانه ولم يتحمل تبعته.",
        looks_like_ar="عائد يأخذه طرف لا يتحمل أي خسارة إن وقعت.",
        reason_en="Someone takes the profit of a thing whose risk he never carried.",
        evidence=(
            _s("أبو داود: 3504", "ولا ربح ما لم تضمن"),
            _s("أبو داود: 3508", "الخراج بالضمان"),
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.MANUAL,
    ),
    # ------------------------------------------------ الصرف والتقابض
    Condition(
        code="EX-01",
        family=Family.EXCHANGE,
        activity=Activity.DEFERRED_CURRENCY_EXCHANGE,
        title_ar="صرف عملة بعملة بغير تقابض",
        meaning_ar="مبادلة نقد بنقد مع تأخير تسليم أحد العوضين.",
        looks_like_ar="خدمة تحويل تقول: ادفع اليوم وتستلم العملة الأخرى بعد أيام بسعر اليوم.",
        reason_en="Currency is exchanged for currency without both sides changing hands at once.",
        evidence=(_SARF_HADITH, _RIBA_VERSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "forward exchange rate", "settle the currency leg later",
            "deferred settlement of the exchange",
        ),
    ),
    Condition(
        code="EX-02",
        family=Family.EXCHANGE,
        activity=Activity.DEFERRED_CURRENCY_EXCHANGE,
        title_ar="عقد صرف آجل",
        meaning_ar="بيع عملة بعملة إلى أجل، وهو ربا النسيئة.",
        looks_like_ar="منصة تعرض FX forward أو تثبيت سعر صرف لتاريخ مستقبلي.",
        reason_en="The project sells forward foreign-exchange contracts.",
        evidence=(_SARF_HADITH, _RIBA_VERSE, _GHARAR_HADITH),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=("fx forward", "currency forward contract", "lock in tomorrow's rate"),
    ),
    # ------------------------------------------------ المعايير الكمية
    Condition(
        code="RT-01",
        family=Family.RATIO,
        activity=Activity.MIXED_PROHIBITED_INCOME,
        title_ar="الدخل المحرم يتجاوز 5% من الإيراد",
        meaning_ar=(
            "أن يكون نشاط المشروع الأصلي مباحاً، لكن جزءاً من دخله يأتي من مصدر محرم "
            "يتجاوز الحد المعفو عنه."
        ),
        looks_like_ar="شبكة مباحة تشغل إلى جانبها وحدة إقراض تدر جزءاً معتبراً من الإيراد.",
        reason_en="More than 5% of the project's income comes from a forbidden source.",
        evidence=(_AAOIFI_21, _PRICE_OF_HARAM),
        agreement=Agreement.MAJORITY,
        detection=Detection.NUMERIC,
        note_ar="يحتاج قوائم إيراد لا يوفرها أي مزود بيانات عملات اليوم.",
    ),
    Condition(
        code="RT-02",
        family=Family.RATIO,
        activity=Activity.MIXED_PROHIBITED_INCOME,
        title_ar="الديون الربوية تتجاوز 30% من القيمة السوقية",
        meaning_ar="أن تكون الشركة المصدرة مثقلة بقروض بفائدة فوق الحد المعتمد.",
        looks_like_ar="شركة مصدرة لعملة، ميزانيتها قائمة على اقتراض ربوي كبير.",
        reason_en="Interest-bearing debt is more than 30% of the project's market value.",
        evidence=(_AAOIFI_21, _RIBA_VERSE),
        agreement=Agreement.MAJORITY,
        detection=Detection.NUMERIC,
    ),
    Condition(
        code="RT-03",
        family=Family.RATIO,
        activity=Activity.MIXED_PROHIBITED_INCOME,
        title_ar="الاستثمارات الربوية تتجاوز 30% من القيمة السوقية",
        meaning_ar="أن يكون جزء كبير من أصول المشروع في ودائع أو أوراق بفائدة.",
        looks_like_ar="خزينة مشروع أغلبها في أدوات دين بفائدة.",
        reason_en="Interest-bearing investments are more than 30% of the project's market value.",
        evidence=(_AAOIFI_21, _RIBA_VERSE),
        agreement=Agreement.MAJORITY,
        detection=Detection.NUMERIC,
    ),
    # ------------------------------------------------------ الضرر والظلم
    Condition(
        code="HM-01",
        family=Family.HARM,
        activity=Activity.ILLICIT_FINANCE_SERVICE,
        title_ar="الإعانة على محرم",
        meaning_ar=(
            "أن تكون الخدمة نفسها مباحة، لكن المشروع يوجهها صراحة لخدمة نشاط محرم."
        ),
        looks_like_ar="بنية تحتية تسوق نفسها لمنصات القمار أو المواقع الإباحية بالاسم.",
        reason_en="The project openly markets itself to serve a forbidden business.",
        evidence=(_COOPERATION_VERSE, _PRICE_OF_HARAM),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=(
            "built for casinos", "payments for adult sites", "infrastructure for betting operators",
        ),
    ),
    Condition(
        code="HM-02",
        family=Family.HARM,
        activity=Activity.MARKET_MANIPULATION,
        title_ar="الاستفادة من ترتيب المعاملات على حساب المستخدم",
        meaning_ar=(
            "أن يعرف أحد أن هناك أمر شراء قادماً، فيسبقه بالشراء ليبيع عليه أغلى. "
            "هذا معنى تلقي الركبان: استغلال جهل الطرف الآخر بالسعر."
        ),
        looks_like_ar="بروتوكول يفاخر بأنه يلتقط أرباح الترتيب أو يبيع أولوية التنفيذ.",
        reason_en=(
            "The project profits by jumping ahead of users' own orders and selling "
            "back to them at a worse price."
        ),
        evidence=(
            _s("البخاري: 2165، ومسلم: 1517", "لا تلقوا الركبان للبيع"),
            _GHISH_HADITH,
            _NO_HARM,
        ),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("sandwich attack", "frontrunning profit", "extract mev", "mev extraction"),
        note_ar=(
            "ليس كل ترتيب للمعاملات محرماً؛ الممنوع هو التربح من الإضرار بأمر المستخدم. "
            "لذلك الألفاظ هنا تخص الاستخراج الصريح لا مجرد ذكر MEV."
        ),
    ),
    Condition(
        code="HM-03",
        family=Family.HARM,
        activity=Activity.MISUSE_OF_CUSTOMER_ASSETS,
        title_ar="التصرف في أصول العملاء بغير إذنهم",
        meaning_ar="أن تستعمل المنصة أموال المودعين في الإقراض أو التداول دون رضاهم.",
        looks_like_ar="شروط خدمة تسمح للمنصة بإعادة رهن أصول العملاء.",
        reason_en="The project uses customers' deposited assets without their consent.",
        evidence=(
            _s(
                "أحمد: 23605، من حديث أبي حميد الساعدي، وصححه الألباني",
                "لا يحل مال امرئ إلا بطيب نفس منه",
            ),
            _BATIL_VERSE,
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.TEXT,
        phrases=(
            "rehypothecation", "we may lend your deposited assets", "reuse customer collateral",
        ),
    ),
    Condition(
        code="HM-04",
        family=Family.HARM,
        activity=Activity.MISUSE_OF_CUSTOMER_ASSETS,
        title_ar="بيع بيانات المستخدمين بغير إذن",
        meaning_ar="أن يبيع المشروع معلومات الناس الخاصة لطرف ثالث دون رضاهم.",
        looks_like_ar="سياسة خصوصية تقول إن بيانات المحفظة تباع للمعلنين.",
        reason_en="The project sells users' private data without their consent.",
        evidence=(
            _q("الحجرات: 12", "وَلَا تَجَسَّسُوا وَلَا يَغْتَب بَّعْضُكُم بَعْضًا"),
            _BATIL_VERSE,
        ),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("sell your data to advertisers", "monetize user data",),
    ),
    Condition(
        code="WG-06",
        family=Family.WRONGFUL_GAIN,
        activity=Activity.HOARDING_OR_CONTROL,
        title_ar="شراء الأصوات في الحوكمة",
        meaning_ar="دفع المال لمن يصوت لصالحك، وهي رشوة تفسد القرار وتضر بقية الملاك.",
        looks_like_ar="سوق مفتوح لبيع قوة التصويت وشرائها قبل كل قرار.",
        reason_en="Votes in the project's governance are openly bought and sold.",
        evidence=(
            _s("أبو داود: 3580، والترمذي: 1337", "لعن رسول الله ﷺ الراشي والمرتشي"),
            _BATIL_VERSE,
        ),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("vote buying", "bribe market for votes", "sell your voting power"),
    ),
    Condition(
        code="GH-10",
        family=Family.GHARAR,
        activity=Activity.SELLING_WHAT_IS_NOT_OWNED,
        title_ar="بيع المعدوم",
        meaning_ar="بيع شيء غير موجود أصلاً وقت العقد ولا يملكه البائع ولا يقدر على تسليمه.",
        looks_like_ar="طرح عملة مقابل منتج لم يبدأ العمل فيه بعد، ولا يوجد ما يضمنه.",
        reason_en="The project sells a thing that does not exist yet and may never exist.",
        evidence=(
            _NOT_YOURS,
            _s("البخاري: 2143، ومسلم: 1514", "نهى رسول الله ﷺ عن بيع حبل الحبلة"),
            _GHARAR_HADITH,
        ),
        agreement=Agreement.MAJORITY,
        detection=Detection.MANUAL,
        note_ar="السلم والاستصناع مستثنيان بضوابطهما، فلا يدخلان في هذا الشرط.",
    ),
    Condition(
        code="GH-11",
        family=Family.GHARAR,
        activity=Activity.LENDING_BORROWING,
        title_ar="إقراض أصول العملاء لمن يبيع على المكشوف",
        meaning_ar="أن تقرض المنصة عملات العملاء لمن يبيعها وهو لا يملكها، بمقابل.",
        looks_like_ar="خدمة تقول: أقرض عملاتك للمتداولين واحصل على عائد.",
        reason_en="The project lends out holdings so others can short-sell them, for a fee.",
        evidence=(_NOT_YOURS, _QARD_JARRA, _RIBA_VERSE),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("securities lending", "lend your coins to short sellers", "coin lending program"),
    ),
    Condition(
        code="GH-12",
        family=Family.GHARAR,
        activity=Activity.UNBACKED_COMMODITY_CLAIM,
        title_ar="عملة مستقرة خوارزمية بلا تغطية",
        meaning_ar=(
            "عملة تدعي ثباتاً في السعر، وليس خلفها أصل حقيقي، وإنما معادلة تعتمد على "
            "استمرار إقبال الناس. إن توقف الإقبال ذهبت القيمة كلها."
        ),
        looks_like_ar="عملة تقول إنها مستقرة بآلية خوارزمية أو بعملة أخرى تصدرها هي نفسها.",
        reason_en="The coin promises a stable price with no real asset behind it.",
        evidence=(_GHARAR_HADITH, _BATIL_VERSE),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=(
            "algorithmic stablecoin", "seigniorage model", "maintains the peg algorithmically",
        ),
        note_ar="كتب هذا الشرط بعد انهيار عملات من هذا النوع أذهب أموال الناس كلها.",
    ),
    Condition(
        code="GH-13",
        family=Family.GHARAR,
        activity=Activity.DISPUTED_LIQUIDITY_PROVISION,
        title_ar="تزويد السيولة مع الخسارة غير الدائمة",
        meaning_ar=(
            "أن تضع أصلين في مجمع، فتخرج بنسب مختلفة عما وضعت، من غير أن تكون بعت "
            "بيعاً معلوماً."
        ),
        looks_like_ar="أي مجمع سيولة في منصة تبادل لا مركزية.",
        reason_en="Liquidity providers can be returned a different mix than they put in.",
        evidence=(_GHARAR_HADITH,),
        agreement=Agreement.DISPUTED,
        detection=Detection.TEXT,
        phrases=("impermanent loss",),
        note_ar=(
            "تحذير مهم: هذا الشرط لو اعتمد فسيرفض تقريباً كل منصات التبادل اللامركزية، "
            "ومنها ما تعده جهات كثيرة متوافقاً. وضع في خانة مستقلة عمداً حتى لا يرفض "
            "«التبادل الفوري» كله بالخطأ. لا أنصح باعتماده إلا بعد فتوى خاصة."
        ),
    ),
    Condition(
        code="MY-08",
        family=Family.MAYSIR,
        activity=Activity.CONVENTIONAL_FINANCE,
        title_ar="بيع تغطية المخاطر بمقابل",
        meaning_ar=(
            "أن تدفع قسطاً لتأخذ تعويضاً إن وقع حدث، وهو معاوضة على أمر محتمل. "
            "يختلف عن التكافل لأن التكافل تبرع لا بيع."
        ),
        looks_like_ar="بروتوكول يبيع تغطية ضد اختراق العقود أو انهيار الربط بالدولار.",
        reason_en="The project sells cover against a risk in exchange for a premium.",
        evidence=(_GHARAR_HADITH, _MAYSIR_VERSE, _AAOIFI_21),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=(
            "buy cover for your", "coverage against smart contract failure", "depeg protection",
        ),
    ),
    Condition(
        code="MY-09",
        family=Family.MAYSIR,
        activity=Activity.GAMBLING,
        title_ar="المراهنة على فشل مشروع أو انهيار سعر",
        meaning_ar="عقود ربحها معلق على وقوع ضرر بالآخرين، لا على عمل ولا على أصل.",
        looks_like_ar="سوق يتيح الرهان على اختراق بروتوكول أو انهيار عملة.",
        reason_en="The project runs markets for betting that something will fail.",
        evidence=(_MAYSIR_VERSE, _NO_HARM),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("bet on the collapse", "wager on a hack", "short the protocol and win"),
    ),
    Condition(
        code="DC-08",
        family=Family.DECEIT,
        activity=Activity.DECEPTIVE_DISCLOSURE,
        title_ar="رسوم خفية غير معلنة",
        meaning_ar="اقتطاع مبالغ من المستخدم لم تبين له قبل العقد.",
        looks_like_ar="عقد يأخذ نسبة عند كل تحويل دون ذكرها في الموقع.",
        reason_en="The project takes fees it never disclosed.",
        evidence=(_GHISH_HADITH, _BATIL_VERSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.MANUAL,
    ),
    Condition(
        code="DC-09",
        family=Family.DECEIT,
        activity=Activity.DECEPTIVE_DISCLOSURE,
        title_ar="تضخيم أرقام المشروع",
        meaning_ar=(
            "إظهار المشروع أكبر مما هو، بأرقام مستخدمين أو سيولة مصنوعة. وهو من التصرية "
            "المنهي عنها: تحسين ظاهر السلعة لإخفاء حقيقتها."
        ),
        looks_like_ar="موقع يعرض أرقام نمو لا تطابق ما على الشبكة.",
        reason_en="The project inflates its own numbers to look bigger than it is.",
        evidence=(
            _GHISH_HADITH,
            _s(
                "البخاري: 2148، ومسلم: 1524",
                "لا تصروا الإبل والغنم، فمن ابتاعها بعد فهو بخير النظرين"
            ),
        ),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.MANUAL,
    ),
    Condition(
        code="PR-11",
        family=Family.PROHIBITED,
        activity=Activity.ADULT_OR_IMMORAL_TRADE,
        title_ar="أصول رقمية تحمل صوراً أو رموزاً محرمة",
        meaning_ar="أن يكون المبيع نفسه صورة فاحشة أو رمزاً لمحرم.",
        looks_like_ar="مجموعة NFT قائمة على صور عارية أو رموز شركية.",
        reason_en="What the project actually sells is obscene or idolatrous imagery.",
        evidence=(_q("الإسراء: 32", "وَلَا تَقْرَبُوا الزِّنَا"), _PRICE_OF_HARAM),
        agreement=Agreement.MAJORITY,
        detection=Detection.TEXT,
        phrases=("nude nft", "adult nft collection",),
    ),
    Condition(
        code="PR-12",
        family=Family.PROHIBITED,
        activity=Activity.DISPUTED_ENTERTAINMENT,
        title_ar="الفنادق والمنتجعات القائمة على المحرم",
        meaning_ar="أن يكون الدخل الأصلي من خدمات تشمل الخمر والقمار.",
        looks_like_ar="مجموعة فنادق تصدر عملة، ودخلها الأساسي من الملاهي والبار.",
        reason_en="The project's core income is hotel services built on alcohol and gambling.",
        evidence=(_COOPERATION_VERSE, _AAOIFI_21),
        agreement=Agreement.DISPUTED,
        detection=Detection.TEXT,
        phrases=("resort and casino", "hotel and gaming group"),
        note_ar=(
            "الفنادق ليست محرمة لذاتها، والاستبعاد في المؤشرات الإسلامية بسبب الخدمات المصاحبة."
        ),
    ),
    Condition(
        code="CF-05",
        family=Family.CONTRACT_FORM,
        activity=Activity.CORRUPT_CONTRACT_FORM,
        title_ar="شرطان في بيع",
        meaning_ar="اشتراط شرطين زائدين على مقتضى العقد يجران نفعاً لأحد الطرفين.",
        looks_like_ar="بيع مشروط بالاحتفاظ مدة وبإعادة البيع للمصدر بسعر يحدده هو.",
        reason_en="A sale is loaded with extra conditions that only serve one side.",
        evidence=(
            _s("أبو داود: 3504، والترمذي: 1234", "لا يحل سلف وبيع، ولا شرطان في بيع"),
        ),
        agreement=Agreement.MAJORITY,
        detection=Detection.MANUAL,
    ),
    Condition(
        code="EX-03",
        family=Family.EXCHANGE,
        activity=Activity.DEFERRED_CURRENCY_EXCHANGE,
        title_ar="التفاضل في صرف الجنس الواحد",
        meaning_ar="مبادلة ذهب بذهب أو عملة بنفس العملة مع زيادة في أحد الطرفين.",
        looks_like_ar="خدمة تبادل توكن ذهب بتوكن ذهب آخر بكميات غير متساوية.",
        reason_en="The project swaps a thing for the same thing in unequal amounts.",
        evidence=(_SARF_HADITH, _RIBA_VERSE),
        agreement=Agreement.UNANIMOUS,
        detection=Detection.MANUAL,
    ),
    Condition(
        code="RT-04",
        family=Family.RATIO,
        activity=Activity.MIXED_PROHIBITED_INCOME,
        title_ar="وجوب تطهير الدخل المحرم اليسير",
        meaning_ar=(
            "إذا كان الدخل المحرم دون الحد المعفو عنه، وجب التخلص من نسبته بالصدقة، "
            "ولا يطيب للمالك."
        ),
        looks_like_ar="مشروع نشاطه مباح وفيه وحدة صغيرة تدر دخلاً ربوياً.",
        reason_en="A small share of forbidden income must be given away, not kept.",
        evidence=(_AAOIFI_21, _PRICE_OF_HARAM),
        agreement=Agreement.MAJORITY,
        detection=Detection.NUMERIC,
        note_ar="هذا شرط إرشادي للمستخدم، لا يرفض العملة، وإنما يوجب عليه التطهير.",
    ),
)


@dataclass(frozen=True, slots=True)
class Decision:
    """One approval or refusal by the product owner."""

    code: str
    status: Status
    decided_by: str = ""
    decided_on: str = ""
    note: str = ""


@lru_cache(maxsize=1)
def _decisions() -> Mapping[str, Decision]:
    """The owner's decisions, read from the file that carries them.

    A code with no entry is :attr:`Status.PROPOSED`. That default is the safe one in both
    directions: a new condition somebody adds cannot start refusing coins, and a decision
    somebody deletes cannot leave a rule running with nothing authorising it.

    **Cached for the life of the process, deliberately.** Some readers ask at import time
    — :mod:`sharia_evidence_vocabulary` builds its phrase lists once — and some ask on
    every call, as :func:`sharia_automated_screen.screen` does. The cache is what makes
    those agree: every reader in one process sees the same snapshot, so the words being
    searched for and the rules being applied can never come from two different versions
    of this file. Changing an approval therefore takes a restart, which is correct for a
    decision that arrives as a deployment.
    """

    with (
        resources.files("ai_market_monitor.services")
        .joinpath(DECISIONS_FILE)
        .open(encoding="utf-8") as handle
    ):
        payload = json.load(handle)

    known = {condition.code for condition in CONDITIONS}
    decisions: dict[str, Decision] = {}
    for entry in payload.get("decisions", []):
        code = str(entry.get("code", "")).strip()
        if code not in known:
            raise ValueError(
                f"{DECISIONS_FILE} approves {code!r}, which is not a condition in this "
                "register. A decision about a rule nobody wrote cannot be applied."
            )
        decisions[code] = Decision(
            code=code,
            status=Status(str(entry.get("status"))),
            decided_by=str(entry.get("decided_by", "")),
            decided_on=str(entry.get("decided_on", "")),
            note=str(entry.get("note", "")),
        )
    return decisions


def status_of(code: str) -> Status:
    """Where one condition stands. Anything undecided is proposed, and so inert."""

    decision = _decisions().get(code)
    return decision.status if decision else Status.PROPOSED


def decision_for(code: str) -> Decision | None:
    return _decisions().get(code)


def conditions_by_status(status: Status) -> tuple[Condition, ...]:
    return tuple(item for item in CONDITIONS if status_of(item.code) is status)


def approved_conditions() -> tuple[Condition, ...]:
    """Every condition the owner has approved. These, and only these, refuse a coin."""

    return conditions_by_status(Status.APPROVED)


def condition(code: str) -> Condition:
    for item in CONDITIONS:
        if item.code == code:
            return item
    raise KeyError(code)


def blocking_activities() -> Mapping[Activity, str]:
    """The activities that refuse a coin, and the plain words for why.

    Derived from the approved conditions, never written by hand. This is the mapping the
    rule in :mod:`sharia_automated_screen` applies, so approving a condition here is the
    single act that changes what the product refuses.

    Several conditions can share one activity — a lottery and a casino are both
    :attr:`Activity.GAMBLING`. The reason shown is the first approved condition's, which
    is why every condition's ``reason_en`` has to stand on its own as a sentence.
    """

    reasons: dict[Activity, str] = {}
    for item in approved_conditions():
        if item.activity is None:
            continue  # answers the holder-return question instead; see Condition
        reasons.setdefault(item.activity, item.reason_en)
    return reasons


def approved_return_conditions(kind: HolderReturn) -> tuple[Condition, ...]:
    """Approved conditions that say a holder's return comes from ``kind``.

    These feed the return vocabulary rather than the blocking-activity map, so that
    :class:`HolderReturn` stays the only owner of "is this return riba".
    """

    return tuple(item for item in approved_conditions() if item.return_kind is kind)


def return_phrases(kind: HolderReturn) -> tuple[str, ...]:
    """Every phrase, from every approved condition, that evidences this return kind."""

    seen: dict[str, None] = {}
    for item in approved_return_conditions(kind):
        for phrase in item.phrases:
            seen.setdefault(phrase, None)
    return tuple(seen)


def out_of_reach_conditions() -> tuple[Condition, ...]:
    """Approved rules the automated screen does not attempt.

    Reading a project's own pages cannot settle these, and no amount of reading more
    pages would: whether a swap is riba al-fadl needs the mechanism rather than the
    marketing, and a debt ratio needs a balance sheet no coin data provider publishes.

    **They are skipped silently, per coin.** For one turn they were reported on every
    verdict as "unchecked", and that was dropped on 31 August 2026 for a plain reason:
    nobody could act on it. Neither the product owner nor a Shariah provider can settle
    riba al-fadl from a queue entry, so the queue was work that could never be cleared;
    and a flag identical on every coin tells a reader nothing.

    What replaces it is a single statement of the screen's reach — in the methodology
    note, and in the disclosure that travels with every result. This function is what
    those say it from, so the sentence and the code cannot drift.

    Skipped is never *passed*. A rule here contributes nothing in either direction.
    """

    return tuple(
        item for item in approved_conditions() if not item.is_detectable_from_text
    )


def applied_conditions() -> tuple[Condition, ...]:
    """The approved rules the screen can and does apply by reading a project's pages."""

    return tuple(item for item in approved_conditions() if item.is_detectable_from_text)


def text_detectable_conditions() -> tuple[Condition, ...]:
    """Every condition with phrases, approved or not.

    Deliberately **not** filtered by status. The vocabulary reads all of them, so the
    owner can be shown what approving a proposed rule would have caught, on real coins,
    before approving it. Only :func:`blocking_activities` decides what actually refuses.
    """

    return tuple(item for item in CONDITIONS if item.is_detectable_from_text)


def register_summary() -> dict[str, Any]:
    """Counts for a report or a page header."""

    by_status = {status.value: len(conditions_by_status(status)) for status in Status}
    by_family = {
        family.value: len([c for c in CONDITIONS if c.family is family]) for family in Family
    }
    return {
        "total": len(CONDITIONS),
        "by_status": by_status,
        "by_family": by_family,
        "text_detectable": len(text_detectable_conditions()),
        "applied": len(applied_conditions()),
        "out_of_reach": len(out_of_reach_conditions()),
    }


__all__ = [
    "CONDITIONS",
    "DECISIONS_FILE",
    "EVIDENCE_KIND_AR",
    "FAMILY_TITLE_AR",
    "Activity",
    "Agreement",
    "Condition",
    "Decision",
    "Detection",
    "Evidence",
    "EvidenceKind",
    "Family",
    "HolderReturn",
    "Status",
    "approved_conditions",
    "approved_return_conditions",
    "blocking_activities",
    "return_phrases",
    "condition",
    "conditions_by_status",
    "decision_for",
    "register_summary",
    "status_of",
    "text_detectable_conditions",
    "applied_conditions",
    "out_of_reach_conditions",
]
