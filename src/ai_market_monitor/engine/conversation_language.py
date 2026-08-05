"""The language this conversation is in, decided by the server and enforced by it.

Before this module the rule lived in one sentence of the composer prompt — "write in
the user's own language" — and nowhere else. Everything the server itself says was
hardcoded English: every refusal message, every clarification question, every
deterministic summary, every safe error, every fallback. So a trader writing Arabic got
Arabic only while the model happened to comply, and the moment a turn fell back to a
deterministic reply the conversation switched to English mid-sentence.

A model instruction is not an enforcement point. This module is: it decides the active
language from the trader's own words, carries it through planning, clarification,
composition and error handling, and provides the localized wording for everything the
server writes itself. :func:`response_matches_language` is the check that runs before a
reply is rendered.

The rule is consistency, not restriction. English in, English out; Arabic in, Arabic
out; French in, French out. A language nobody on the team can review is still answered
in that language when the model produced it — the server only guarantees that *its own*
sentences follow the conversation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "ConversationLanguage",
    "LanguageDecision",
    "detect_language",
    "localized",
    "resolve_conversation_language",
    "response_matches_language",
]


class ConversationLanguage(StrEnum):
    """The languages the server can write in itself."""

    ENGLISH = "en"
    ARABIC = "ar"
    FRENCH = "fr"
    SPANISH = "es"

    @classmethod
    def parse(cls, value: str | None) -> ConversationLanguage | None:
        if not value:
            return None
        head = str(value).strip().casefold().replace("_", "-").split("-", 1)[0]
        try:
            return cls(head)
        except ValueError:
            return None


#: Arabic script. One character is enough: nobody writes Arabic letters by accident.
_ARABIC_SCRIPT = re.compile(r"[؀-ۿݐ-ݿ]")

#: Arabizi spells Arabic sounds Latin has no letter for with digits: ``3`` for ع,
#: ``7`` for ح, ``2`` for ء. A digit *inside or opening* a Latin word is that
#: substitution and almost nothing else, which makes it a far better signal than any
#: word list: ``3ayez``, ``7aga``, ``a2al``, ``sa3a``.
_ARABIZI_STRONG = re.compile(r"(?<![\w])[23579][a-z]{2,}|[a-z]+[23579][a-z]+", re.IGNORECASE)

#: A digit *ending* a Latin word is weaker — ``web3`` and ``gpt5`` do it too — so it
#: counts only alongside a strong signal, never on its own.
_ARABIZI_WEAK = re.compile(r"(?<![\w])[a-z]{2,}[23579](?![\w])", re.IGNORECASE)

#: Arabizi words with no digit in them. Worth a point each, never a verdict alone.
_ARABIZI_MARKERS: Final[tuple[str, ...]] = (
    "3ala",
    "el a2al",
    "el aktar",
    "a2al",
    "aktar",
    "akbar",
    "men",
    "aw",
    "3ayez",
    "3awez",
    "3ayz",
    "momken",
    "mumkin",
    "3andi",
    "eh",
    "leh",
    "kam",
    "da",
    "di",
    "msh",
    "mesh",
    "bas",
    "kda",
    "keda",
    "3ashan",
    "3shan",
    "sa3a",
    "de2i2a",
    "yom",
)

#: Ordinary words that only appear in one of these languages. Scored, not matched once,
#: so a single borrowed word ("alert", "scanner") cannot flip the whole conversation.
_WORD_MARKERS: Final[dict[ConversationLanguage, tuple[str, ...]]] = {
    ConversationLanguage.ENGLISH: (
        "the", "and", "when", "what", "coin", "coins", "alert", "please", "should",
        "increase", "increases", "rise", "rises", "watch", "show", "me", "now",
        "over", "period", "hours", "hour", "with", "that", "this", "for", "from",
        "want", "create", "make", "give", "which", "how", "does", "don't", "up",
    ),
    ConversationLanguage.FRENCH: (
        "le", "la", "les", "des", "une", "un", "et", "quand", "quoi", "quelle",
        "quel", "pièce", "pièces", "alerte", "hausse", "monte", "montent",
        "surveille", "surveiller", "montre", "maintenant", "heures", "heure",
        "sur", "pour", "avec", "que", "qui", "je", "veux", "créer", "crée",
        "moi", "s'il", "vous", "plaît", "combien", "pourquoi", "comment",
    ),
    ConversationLanguage.SPANISH: (
        "el", "la", "los", "las", "una", "unos", "y", "cuando", "qué", "cuál",
        "moneda", "monedas", "alerta", "sube", "suben", "subida", "vigila",
        "vigilar", "muestra", "ahora", "horas", "hora", "sobre", "para", "con",
        "que", "quien", "quiero", "crear", "crea", "por", "favor", "cómo",
        "cuánto", "porque", "dame",
    ),
}

#: Words shared by French and Spanish. Counting them for both would let either win at
#: random on a short message, so they are worth nothing to either.
_AMBIGUOUS: Final[frozenset[str]] = frozenset(
    {"la", "el", "un", "una", "que", "no", "me", "de", "en", "es", "a", "y", "o"}
)

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class LanguageDecision:
    """The active language, and how the server arrived at it."""

    language: ConversationLanguage
    #: ``user_turn`` | ``session`` | ``default``
    source: str
    #: True when this turn changed the conversation's language.
    changed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "conversation_language": self.language.value,
            "conversation_language_source": self.source,
            "conversation_language_changed": self.changed,
        }


def detect_language(text: str) -> ConversationLanguage | None:
    """The language of one message, or ``None`` when the words decide nothing.

    ``None`` is a real answer. "ok", "??" and "BTCUSDT" carry no language, and guessing
    one from them is how a conversation flips language on an acknowledgement.
    """

    if not text or not text.strip():
        return None
    if _ARABIC_SCRIPT.search(text):
        return ConversationLanguage.ARABIC
    words = [word.casefold() for word in _WORD_RE.findall(text)]
    if not words:
        return None
    unique = set(words)
    strong = len(_ARABIZI_STRONG.findall(text))
    if strong:
        # A digit standing in for an Arabic sound is the gate. Once one is present,
        # trailing-digit words and plain Arabizi words corroborate it.
        score = (
            strong
            + len(_ARABIZI_WEAK.findall(text))
            + sum(marker in unique for marker in _ARABIZI_MARKERS)
        )
        if score >= 2:
            return ConversationLanguage.ARABIC
    scores: dict[ConversationLanguage, int] = {}
    for language, markers in _WORD_MARKERS.items():
        scores[language] = sum(
            1 for word in words if word in markers and word not in _AMBIGUOUS
        )
    best = max(scores, key=lambda item: scores[item])
    if scores[best] == 0:
        return None
    # A tie is not evidence. Two languages scoring the same on a short message means
    # the message is mostly shared vocabulary, and switching on that is a coin flip.
    if sum(1 for value in scores.values() if value == scores[best]) > 1:
        return None
    return best


def resolve_conversation_language(
    message: str,
    *,
    session_language: str | None = None,
    default: ConversationLanguage = ConversationLanguage.ENGLISH,
) -> LanguageDecision:
    """Decide the language this turn must be answered in.

    The latest meaningful user turn wins, because that is where a trader changes
    language. When this turn carries no language signal the session's language stands —
    that is what keeps ``??`` or ``ok`` from resetting an Arabic conversation to English.
    """

    stored = ConversationLanguage.parse(session_language)
    detected = detect_language(message)
    if detected is not None:
        return LanguageDecision(
            language=detected,
            source="user_turn",
            changed=stored is not None and stored is not detected,
        )
    if stored is not None:
        return LanguageDecision(language=stored, source="session")
    return LanguageDecision(language=default, source="default")


# --------------------------------------------------------------------------------
# Everything the server says in its own words
# --------------------------------------------------------------------------------

#: One entry per sentence the server writes itself. A key missing a translation falls
#: back to English rather than raising: a conversation in the wrong language is a
#: defect, but a turn that fails to render at all is worse.
_CATALOGUE: Final[dict[str, dict[ConversationLanguage, str]]] = {
    # -- clarification questions -------------------------------------------------
    "ask.symbol_scope": {
        ConversationLanguage.ENGLISH: (
            "Sure. Should I watch all screened coins or one specific coin?"
        ),
        ConversationLanguage.ARABIC: (
            "تمام. أراقب كل العملات المفحوصة ولا عملة واحدة بعينها؟"
        ),
        ConversationLanguage.FRENCH: (
            "D'accord. Dois-je surveiller toutes les cryptos filtrées ou une seule ?"
        ),
        ConversationLanguage.SPANISH: (
            "De acuerdo. ¿Vigilo todas las monedas filtradas o una moneda concreta?"
        ),
    },
    "ask.measurement_window": {
        ConversationLanguage.ENGLISH: (
            "Over what period should the {threshold} move be measured: "
            "1 hour, 4 hours, 24 hours, or since the daily open?"
        ),
        ConversationLanguage.ARABIC: (
            "على أي مدة أقيس حركة {threshold}: ساعة، 4 ساعات، 24 ساعة، "
            "ولا من افتتاح اليوم؟"
        ),
        ConversationLanguage.FRENCH: (
            "Sur quelle période dois-je mesurer la variation de {threshold} : "
            "1 heure, 4 heures, 24 heures, ou depuis l'ouverture du jour ?"
        ),
        ConversationLanguage.SPANISH: (
            "¿En qué periodo mido el movimiento de {threshold}: "
            "1 hora, 4 horas, 24 horas, o desde la apertura del día?"
        ),
    },
    "ask.scan_window": {
        ConversationLanguage.ENGLISH: (
            "Over what period should I measure the {threshold} rise: "
            "1 hour, 4 hours, 24 hours, or since today's open?"
        ),
        ConversationLanguage.ARABIC: (
            "على أي مدة أقيس الصعود {threshold}: ساعة، 4 ساعات، 24 ساعة، "
            "ولا من افتتاح النهارده؟"
        ),
        ConversationLanguage.FRENCH: (
            "Sur quelle période dois-je mesurer la hausse de {threshold} : "
            "1 heure, 4 heures, 24 heures, ou depuis l'ouverture du jour ?"
        ),
        ConversationLanguage.SPANISH: (
            "¿En qué periodo mido la subida de {threshold}: "
            "1 hora, 4 horas, 24 horas, o desde la apertura de hoy?"
        ),
    },
    "ask.movement_size": {
        ConversationLanguage.ENGLISH: "How big should the move be, in percent?",
        ConversationLanguage.ARABIC: "قد إيه تكون الحركة، بالنسبة المئوية؟",
        ConversationLanguage.FRENCH: "De quelle taille doit être la variation, en pourcentage ?",
        ConversationLanguage.SPANISH: "¿De qué tamaño debe ser el movimiento, en porcentaje?",
    },
    "ask.movement_kind": {
        ConversationLanguage.ENGLISH: "Should I watch for a rise, a fall, or both?",
        ConversationLanguage.ARABIC: "أراقب صعود، هبوط، ولا الاتنين؟",
        ConversationLanguage.FRENCH: "Dois-je surveiller une hausse, une baisse, ou les deux ?",
        ConversationLanguage.SPANISH: "¿Vigilo una subida, una bajada, o ambas?",
    },
    # -- scan results ------------------------------------------------------------
    "scan.no_matches": {
        ConversationLanguage.ENGLISH: (
            "No screened coins are up {threshold} or more over the last {window} "
            "(as of {timestamp})."
        ),
        ConversationLanguage.ARABIC: (
            "مفيش عملات مفحوصة صاعدة {threshold} أو أكتر خلال {window} "
            "(حتى {timestamp})."
        ),
        ConversationLanguage.FRENCH: (
            "Aucune crypto filtrée n'est en hausse de {threshold} ou plus sur {window} "
            "(au {timestamp})."
        ),
        ConversationLanguage.SPANISH: (
            "Ninguna moneda filtrada sube {threshold} o más en {window} "
            "(a fecha de {timestamp})."
        ),
    },
    "scan.matches": {
        ConversationLanguage.ENGLISH: (
            "Screened coins up {threshold} or more over the last {window} "
            "(as of {timestamp}):"
        ),
        ConversationLanguage.ARABIC: (
            "عملات مفحوصة صاعدة {threshold} أو أكتر خلال {window} (حتى {timestamp}):"
        ),
        ConversationLanguage.FRENCH: (
            "Cryptos filtrées en hausse de {threshold} ou plus sur {window} "
            "(au {timestamp}) :"
        ),
        ConversationLanguage.SPANISH: (
            "Monedas filtradas que suben {threshold} o más en {window} "
            "(a fecha de {timestamp}):"
        ),
    },
    "scan.unavailable": {
        ConversationLanguage.ENGLISH: (
            "I could not run that scan right now because live market data is "
            "unavailable. Nothing was changed."
        ),
        ConversationLanguage.ARABIC: (
            "مقدرتش أعمل الفحص دلوقتي لأن بيانات السوق مش متاحة. مفيش حاجة اتغيرت."
        ),
        ConversationLanguage.FRENCH: (
            "Je n'ai pas pu lancer ce scan car les données de marché sont "
            "indisponibles. Rien n'a été modifié."
        ),
        ConversationLanguage.SPANISH: (
            "No he podido ejecutar ese escaneo porque los datos de mercado no están "
            "disponibles. No se ha cambiado nada."
        ),
    },
    # -- confusion recovery ------------------------------------------------------
    "confusion.acknowledge": {
        ConversationLanguage.ENGLISH: "Sorry — that didn't answer your question.",
        ConversationLanguage.ARABIC: "آسف — الرد ده مجاوبش على سؤالك.",
        ConversationLanguage.FRENCH: "Désolé — cela ne répondait pas à votre question.",
        ConversationLanguage.SPANISH: "Perdona — eso no respondía a tu pregunta.",
    },
    "confusion.restate_scan": {
        ConversationLanguage.ENGLISH: (
            "You want to scan screened coins that are up at least {threshold}."
        ),
        ConversationLanguage.ARABIC: (
            "إنت عايز تفحص العملات المفحوصة اللي صاعدة {threshold} على الأقل."
        ),
        ConversationLanguage.FRENCH: (
            "Vous voulez scanner les cryptos filtrées en hausse d'au moins {threshold}."
        ),
        ConversationLanguage.SPANISH: (
            "Quieres escanear monedas filtradas que suban al menos {threshold}."
        ),
    },
    "confusion.restate_alert": {
        ConversationLanguage.ENGLISH: (
            "You want an alert when a coin moves by {threshold}."
        ),
        ConversationLanguage.ARABIC: "إنت عايز تنبيه لما عملة تتحرك {threshold}.",
        ConversationLanguage.FRENCH: (
            "Vous voulez une alerte lorsqu'une crypto varie de {threshold}."
        ),
        ConversationLanguage.SPANISH: (
            "Quieres una alerta cuando una moneda se mueva un {threshold}."
        ),
    },
    "confusion.restate_generic": {
        ConversationLanguage.ENGLISH: "Let me get back to what you asked.",
        ConversationLanguage.ARABIC: "خليني أرجع لطلبك.",
        ConversationLanguage.FRENCH: "Revenons à votre demande.",
        ConversationLanguage.SPANISH: "Volvamos a lo que pediste.",
    },
    # -- ordinary status ---------------------------------------------------------
    "status.nothing_set_up": {
        ConversationLanguage.ENGLISH: (
            "Nothing is set up yet. Tell me what market move you want followed."
        ),
        ConversationLanguage.ARABIC: "لسه مفيش حاجة متظبطة. قوللي عايز تتابع أي حركة في السوق.",
        ConversationLanguage.FRENCH: (
            "Rien n'est encore configuré. Dites-moi quel mouvement vous voulez suivre."
        ),
        ConversationLanguage.SPANISH: (
            "Todavía no hay nada configurado. Dime qué movimiento quieres seguir."
        ),
    },
    "status.preview_ready": {
        ConversationLanguage.ENGLISH: (
            "The inactive preview is ready. Use Review and approve when it matches."
        ),
        ConversationLanguage.ARABIC: (
            "المعاينة غير المفعّلة جاهزة. استخدم مراجعة وموافقة لما تكون مظبوطة."
        ),
        ConversationLanguage.FRENCH: (
            "L'aperçu inactif est prêt. Utilisez Vérifier et approuver quand il convient."
        ),
        ConversationLanguage.SPANISH: (
            "La vista previa inactiva está lista. Usa Revisar y aprobar cuando encaje."
        ),
    },
    "status.no_change": {
        ConversationLanguage.ENGLISH: "Nothing in the draft needed to change for that.",
        ConversationLanguage.ARABIC: "مفيش حاجة في المسودة محتاجة تتغير عشان ده.",
        ConversationLanguage.FRENCH: "Rien dans le brouillon n'avait besoin de changer.",
        ConversationLanguage.SPANISH: "No hacía falta cambiar nada en el borrador.",
    },
    "status.chat_cannot_approve": {
        ConversationLanguage.ENGLISH: (
            "I noted that you want to approve it, but chat cannot approve anything. "
            "Use Review and approve on the preview."
        ),
        ConversationLanguage.ARABIC: (
            "سجلت إنك عايز توافق، بس الشات مش بيوافق على حاجة. "
            "استخدم مراجعة وموافقة في المعاينة."
        ),
        ConversationLanguage.FRENCH: (
            "J'ai noté que vous voulez approuver, mais le chat ne peut rien approuver. "
            "Utilisez Vérifier et approuver sur l'aperçu."
        ),
        ConversationLanguage.SPANISH: (
            "He anotado que quieres aprobarlo, pero el chat no aprueba nada. "
            "Usa Revisar y aprobar en la vista previa."
        ),
    },
    # -- product knowledge -------------------------------------------------------
    #: The question this chat is asked more than any other. The answer is server-owned
    #: product fact, so it needs no model call — and answering it in the trader's own
    #: language needs it to exist in that language.
    "product.scanner_vs_monitor": {
        ConversationLanguage.ENGLISH: (
            "A Scanner checks the market once, right now. A Monitor keeps watching and "
            "tells you when your rules match. Neither one places a trade."
        ),
        ConversationLanguage.ARABIC: (
            "السكانر بيفحص السوق مرة واحدة دلوقتي. والمونيتور بيفضل يراقب ويبلغك لما "
            "قواعدك تتحقق. ولا واحد فيهم بينفذ صفقات."
        ),
        ConversationLanguage.FRENCH: (
            "Un Scanner examine le marché une fois, maintenant. Un Monitor continue de "
            "surveiller et vous prévient quand vos règles correspondent. Aucun des deux "
            "ne passe d'ordre."
        ),
        ConversationLanguage.SPANISH: (
            "Un Escáner revisa el mercado una vez, ahora. Un Monitor sigue vigilando y "
            "te avisa cuando tus reglas coinciden. Ninguno de los dos opera."
        ),
    },
    # -- refusals ----------------------------------------------------------------
    "refuse.generic": {
        ConversationLanguage.ENGLISH: (
            "I could not turn that into an exact change. Nothing was altered."
        ),
        ConversationLanguage.ARABIC: "مقدرتش أحوّل ده لتغيير دقيق. مفيش حاجة اتغيرت.",
        ConversationLanguage.FRENCH: (
            "Je n'ai pas pu en faire une modification exacte. Rien n'a été modifié."
        ),
        ConversationLanguage.SPANISH: (
            "No he podido convertir eso en un cambio exacto. No se ha cambiado nada."
        ),
    },
    "refuse.support_reference": {
        ConversationLanguage.ENGLISH: (
            "Something on my side would not accept that change, so I made none. "
            "This is not a problem with how you wrote it."
        ),
        ConversationLanguage.ARABIC: (
            "في حاجة عندي مرضيتش تقبل التغيير ده، فما عملتش أي تعديل. "
            "المشكلة مش في طريقة كتابتك."
        ),
        ConversationLanguage.FRENCH: (
            "Quelque chose de mon côté a refusé ce changement, je n'ai donc rien "
            "modifié. Ce n'est pas un problème de formulation."
        ),
        ConversationLanguage.SPANISH: (
            "Algo de mi lado no aceptó ese cambio, así que no cambié nada. "
            "No es un problema de cómo lo escribiste."
        ),
    },
    "refuse.unsupported": {
        ConversationLanguage.ENGLISH: (
            "HilalMarkets cannot follow that kind of market behaviour yet."
        ),
        ConversationLanguage.ARABIC: "هيلال ماركتس لسه مش بتقدر تتابع نوع الحركة دي.",
        ConversationLanguage.FRENCH: (
            "HilalMarkets ne peut pas encore suivre ce type de comportement de marché."
        ),
        ConversationLanguage.SPANISH: (
            "HilalMarkets todavía no puede seguir ese tipo de comportamiento de mercado."
        ),
    },
}

#: Script ranges that prove a reply is *not* in the expected language. Used only to
#: catch a reply written in the wrong script, which is the failure a user notices
#: instantly; word-level style is left to the model.
_SCRIPT_EXPECTATION: Final[dict[ConversationLanguage, bool]] = {
    ConversationLanguage.ARABIC: True,
    ConversationLanguage.ENGLISH: False,
    ConversationLanguage.FRENCH: False,
    ConversationLanguage.SPANISH: False,
}


def localized(
    key: str,
    language: ConversationLanguage,
    /,
    **values: object,
) -> str:
    """The server's own sentence for ``key``, in ``language``.

    An untranslated key falls back to English rather than raising. A conversation in
    the wrong language is a defect worth a test; a turn that cannot render a reply at
    all is a worse one.
    """

    entry: Mapping[ConversationLanguage, str] = _CATALOGUE.get(key, {})
    template = entry.get(language) or entry.get(ConversationLanguage.ENGLISH) or key
    if not values:
        return template
    try:
        return template.format(**values)
    except (KeyError, IndexError):  # pragma: no cover - a template/argument mismatch
        return template


def response_matches_language(text: str, language: ConversationLanguage) -> bool:
    """Whether a rendered reply is in the conversation's script.

    Deliberately narrow. It proves an Arabic conversation did not get a Latin-script
    reply and vice versa — the mismatch a trader sees immediately — and leaves finer
    judgements alone rather than rejecting valid wording it cannot assess.
    """

    body = (text or "").strip()
    if not body:
        return False
    has_arabic = bool(_ARABIC_SCRIPT.search(body))
    expects_arabic = _SCRIPT_EXPECTATION.get(language, False)
    if expects_arabic:
        return has_arabic
    # A Latin-script conversation may legitimately quote an Arabic name, but a reply
    # that is *mostly* Arabic script is a language switch, not a quotation.
    arabic_characters = len(_ARABIC_SCRIPT.findall(body))
    return arabic_characters * 2 <= len(body)


def translation_coverage() -> dict[str, list[str]]:
    """Keys missing a translation, per language. Used by the language matrix test."""

    missing: dict[str, list[str]] = {}
    for language in ConversationLanguage:
        absent = sorted(key for key, entry in _CATALOGUE.items() if language not in entry)
        if absent:
            missing[language.value] = absent
    return missing
