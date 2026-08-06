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

from ai_market_monitor.schemas.timeframes import COMMON_TIMEFRAMES

__all__ = [
    "ConversationLanguage",
    "LanguageDecision",
    "detect_language",
    "language_of",
    "localized",
    "localized_options",
    "option_label_map",
    "governed_scan_error",
    "resolve_conversation_language",
    "scan_error_is_resolvable",
    "response_matches_language",
    "scanner_labels",
    "scope_labels",
    "translation_coverage",
]


class ConversationLanguage(StrEnum):
    """The languages the server can write in itself."""

    ENGLISH = "en"
    ARABIC = "ar"
    FRENCH = "fr"
    SPANISH = "es"
    RUSSIAN = "ru"

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

#: Cyrillic script. Same reasoning as Arabic: its own alphabet, never typed by accident.
_CYRILLIC_SCRIPT = re.compile(r"[Ѐ-ӿ]")

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
    if _CYRILLIC_SCRIPT.search(text):
        return ConversationLanguage.RUSSIAN
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
        ConversationLanguage.RUSSIAN: (
            "Хорошо. Следить за всеми проверенными монетами или за одной конкретной?"
        ),
    },
    #: Candle periods the compiler can actually build. This offered "since the daily
    #: open" as a fourth choice, which is not a candle period — a trader who picked it
    #: was picking something no rule could be made from.
    "ask.measurement_window": {
        ConversationLanguage.ENGLISH: (
            "Over what period should the {threshold} move be measured: "
            "1 hour, 4 hours, or 1 day?"
        ),
        ConversationLanguage.ARABIC: (
            "على أي مدة أقيس حركة {threshold}: ساعة، 4 ساعات، ولا يوم؟"
        ),
        ConversationLanguage.FRENCH: (
            "Sur quelle période dois-je mesurer la variation de {threshold} : "
            "1 heure, 4 heures, ou 1 jour ?"
        ),
        ConversationLanguage.SPANISH: (
            "¿En qué periodo mido el movimiento de {threshold}: "
            "1 hora, 4 horas, o 1 día?"
        ),
        ConversationLanguage.RUSSIAN: (
            "За какой период измерять движение {threshold}: "
            "1 час, 4 часа или 1 день?"
        ),
    },
    "ask.movement_size": {
        ConversationLanguage.ENGLISH: "How big should the move be, in percent?",
        ConversationLanguage.ARABIC: "قد إيه تكون الحركة، بالنسبة المئوية؟",
        ConversationLanguage.FRENCH: "De quelle taille doit être la variation, en pourcentage ?",
        ConversationLanguage.SPANISH: "¿De qué tamaño debe ser el movimiento, en porcentaje?",
        ConversationLanguage.RUSSIAN: "Каким должно быть движение в процентах?",
    },
    "ask.movement_kind": {
        ConversationLanguage.ENGLISH: "Should I watch for a rise, a fall, or both?",
        ConversationLanguage.ARABIC: "أراقب صعود، هبوط، ولا الاتنين؟",
        ConversationLanguage.FRENCH: "Dois-je surveiller une hausse, une baisse, ou les deux ?",
        ConversationLanguage.SPANISH: "¿Vigilo una subida, una bajada, o ambas?",
        ConversationLanguage.RUSSIAN: "Следить за ростом, падением или за обоими?",
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
        ConversationLanguage.RUSSIAN: (
            "Нет проверенных монет с ростом {threshold} или больше за {window} "
            "(на {timestamp})."
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
        ConversationLanguage.RUSSIAN: (
            "Проверенные монеты с ростом {threshold} или больше за {window} "
            "(на {timestamp}):"
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
        ConversationLanguage.RUSSIAN: (
            "Я не смог выполнить это сканирование, потому что рыночные данные "
            "недоступны. Ничего не изменилось."
        ),
    },
    # -- confusion recovery ------------------------------------------------------
    "confusion.acknowledge": {
        ConversationLanguage.ENGLISH: "Sorry — that didn't answer your question.",
        ConversationLanguage.ARABIC: "آسف — الرد ده مجاوبش على سؤالك.",
        ConversationLanguage.FRENCH: "Désolé — cela ne répondait pas à votre question.",
        ConversationLanguage.SPANISH: "Perdona — eso no respondía a tu pregunta.",
        ConversationLanguage.RUSSIAN: "Извините — это не ответило на ваш вопрос.",
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
        ConversationLanguage.RUSSIAN: (
            "Вы хотите просканировать проверенные монеты с ростом не менее {threshold}."
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
        ConversationLanguage.RUSSIAN: (
            "Вы хотите сигнал, когда монета изменится на {threshold}."
        ),
    },
    "confusion.restate_generic": {
        ConversationLanguage.ENGLISH: "Let me get back to what you asked.",
        ConversationLanguage.ARABIC: "خليني أرجع لطلبك.",
        ConversationLanguage.FRENCH: "Revenons à votre demande.",
        ConversationLanguage.SPANISH: "Volvamos a lo que pediste.",
        ConversationLanguage.RUSSIAN: "Вернёмся к вашему вопросу.",
    },
    "confusion.unclear_retry": {
        ConversationLanguage.ENGLISH: "Sorry — my last reply was unclear. {question}",
        ConversationLanguage.ARABIC: "آسف — ردي السابق لم يكن واضحًا. {question}",
        ConversationLanguage.FRENCH: (
            "Désolé — ma réponse précédente n'était pas claire. {question}"
        ),
        ConversationLanguage.SPANISH: (
            "Perdón, mi respuesta anterior no fue clara. {question}"
        ),
        ConversationLanguage.RUSSIAN: (
            "Извините, предыдущий ответ был неясным. {question}"
        ),
    },
    "confusion.scan_retry": {
        ConversationLanguage.ENGLISH: (
            "Sorry — I didn't answer the scan clearly. You want screened coins "
            "{direction} at least {threshold}. {question}"
        ),
        ConversationLanguage.ARABIC: (
            "آسف — لم أوضح إجابة الفحص. أنت تريد العملات المفحوصة التي تحركت "
            "{direction} بنسبة {threshold} على الأقل. {question}"
        ),
        ConversationLanguage.FRENCH: (
            "Désolé — ma réponse au scan n'était pas claire. Vous cherchez les cryptos "
            "filtrées {direction} d'au moins {threshold}. {question}"
        ),
        ConversationLanguage.SPANISH: (
            "Perdón, no respondí claramente al escaneo. Buscas monedas filtradas que "
            "{direction} al menos {threshold}. {question}"
        ),
        ConversationLanguage.RUSSIAN: (
            "Извините, ответ на запрос сканирования был неясным. Нужны проверенные "
            "монеты, которые {direction} минимум на {threshold}. {question}"
        ),
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
        ConversationLanguage.RUSSIAN: (
            "Пока ничего не настроено. Скажите, какое движение рынка нужно отслеживать."
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
        ConversationLanguage.RUSSIAN: (
            "Неактивный предпросмотр готов. Нажмите «Проверить и одобрить», когда всё верно."
        ),
    },
    "status.reapprove": {
        ConversationLanguage.ENGLISH: (
            "That edit made a new version, so it needs approving again."
        ),
        ConversationLanguage.ARABIC: (
            "التعديل ده عمل نسخة جديدة، فمحتاجة موافقة تاني."
        ),
        ConversationLanguage.FRENCH: (
            "Cette modification a créé une nouvelle version : il faut la réapprouver."
        ),
        ConversationLanguage.SPANISH: (
            "Ese cambio creó una versión nueva, así que hay que aprobarla otra vez."
        ),
        ConversationLanguage.RUSSIAN: (
            "Изменение создало новую версию, которую нужно одобрить снова."
        ),
    },
    "status.no_change": {
        ConversationLanguage.ENGLISH: "Nothing in the draft needed to change for that.",
        ConversationLanguage.ARABIC: "مفيش حاجة في المسودة محتاجة تتغير عشان ده.",
        ConversationLanguage.FRENCH: "Rien dans le brouillon n'avait besoin de changer.",
        ConversationLanguage.SPANISH: "No hacía falta cambiar nada en el borrador.",
        ConversationLanguage.RUSSIAN: "Для этого в черновике ничего менять не пришлось.",
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
        ConversationLanguage.RUSSIAN: (
            "Я записал, что вы хотите одобрить, но чат ничего не одобряет. "
            "Используйте «Проверить и одобрить» в предпросмотре."
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
        ConversationLanguage.RUSSIAN: (
            "Scanner проверяет рынок один раз, прямо сейчас. Monitor продолжает следить "
            "и сообщает, когда ваши правила совпадают. Ни один из них не торгует."
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
        ConversationLanguage.RUSSIAN: (
            "Я не смог превратить это в точное изменение. Ничего не изменилось."
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
        ConversationLanguage.RUSSIAN: (
            "Что-то на моей стороне не приняло это изменение, поэтому я ничего не менял. "
            "Дело не в том, как вы написали."
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
        ConversationLanguage.RUSSIAN: (
            "HilalMarkets пока не может отслеживать такое поведение рынка."
        ),
    },
    "refuse.unsupported_reason": {
        ConversationLanguage.ENGLISH: "I can't build that exact rule yet: {reason}",
        ConversationLanguage.ARABIC: "لا أقدر أبني القاعدة دي بدقة حاليًا: {reason}",
        ConversationLanguage.FRENCH: (
            "Je ne peux pas encore construire exactement cette règle : {reason}"
        ),
        ConversationLanguage.SPANISH: (
            "Todavía no puedo construir esa regla exactamente: {reason}"
        ),
        ConversationLanguage.RUSSIAN: "Пока невозможно точно построить это правило: {reason}",
    },
    # -- clarification wording carried over from the planner-side catalogue --------
    "ask.intro": {
        ConversationLanguage.ENGLISH: "Got it. I only need one choice to continue.",
        ConversationLanguage.ARABIC: "تمام. محتاج اختيار واحد بس عشان أكمل.",
        ConversationLanguage.FRENCH: "D'accord. Il me faut un seul choix pour continuer.",
        ConversationLanguage.SPANISH: "Entendido. Solo necesito una elección para continuar.",
        ConversationLanguage.RUSSIAN: "Понял. Нужен только один выбор, чтобы продолжить.",
    },
    "ask.candle_period": {
        ConversationLanguage.ENGLISH: "Which candle period should I use: 1h, 4h, or 1d?",
        ConversationLanguage.ARABIC: "أستخدم أي فترة شمعة: ساعة، 4 ساعات، ولا يوم؟",
        ConversationLanguage.FRENCH: (
            "Quelle période de bougie dois-je utiliser : 1 h, 4 h ou 1 j ?"
        ),
        ConversationLanguage.SPANISH: "¿Qué periodo de vela uso: 1 h, 4 h o 1 día?",
        ConversationLanguage.RUSSIAN: (
            "Какой период свечи использовать: 1 час, 4 часа или 1 день?"
        ),
    },
    "ask.move_reference": {
        ConversationLanguage.ENGLISH: (
            "Measure the move from that candle's open or the previous candle's close?"
        ),
        ConversationLanguage.ARABIC: "أقيس الحركة من افتتاح الشمعة ولا من إغلاق الشمعة السابقة؟",
        ConversationLanguage.FRENCH: (
            "Mesurer le mouvement depuis l'ouverture de la bougie ou la clôture de la "
            "bougie précédente ?"
        ),
        ConversationLanguage.SPANISH: (
            "¿Mido el movimiento desde la apertura de la vela o desde el cierre de la "
            "vela anterior?"
        ),
        ConversationLanguage.RUSSIAN: (
            "Измерять движение от открытия свечи или от закрытия предыдущей свечи?"
        ),
    },
    "ask.comparator": {
        ConversationLanguage.ENGLISH: (
            "Should the alert trigger when the move reaches the value, or only after "
            "it passes it?"
        ),
        ConversationLanguage.ARABIC: "التنبيه يشتغل عند الوصول للقيمة ولا بعد تجاوزها فقط؟",
        ConversationLanguage.FRENCH: (
            "L'alerte doit-elle se déclencher dès que la valeur est atteinte ou "
            "seulement après son dépassement ?"
        ),
        ConversationLanguage.SPANISH: (
            "¿La alerta se activa al alcanzar el valor o solo después de superarlo?"
        ),
        ConversationLanguage.RUSSIAN: (
            "Сигнал должен сработать при достижении значения или только после его "
            "превышения?"
        ),
    },
    "ask.threshold": {
        ConversationLanguage.ENGLISH: "What percentage or numeric level should trigger it?",
        ConversationLanguage.ARABIC: "إيه النسبة أو المستوى الرقمي المطلوب للتنبيه؟",
        ConversationLanguage.FRENCH: (
            "Quel pourcentage ou niveau numérique doit déclencher l'alerte ?"
        ),
        ConversationLanguage.SPANISH: "¿Qué porcentaje o nivel numérico debe activar la alerta?",
        ConversationLanguage.RUSSIAN: (
            "Какой процент или числовой уровень должен запускать сигнал?"
        ),
    },
    "ask.rule_mechanic": {
        ConversationLanguage.ENGLISH: (
            "What exact market move or indicator should this rule measure?"
        ),
        ConversationLanguage.ARABIC: "إيه حركة السوق أو المؤشر المطلوب قياسه بالضبط؟",
        ConversationLanguage.FRENCH: (
            "Quel mouvement de marché ou indicateur cette règle doit-elle mesurer ?"
        ),
        ConversationLanguage.SPANISH: (
            "¿Qué movimiento de mercado o indicador debe medir esta regla?"
        ),
        ConversationLanguage.RUSSIAN: (
            "Какое движение рынка или индикатор должна измерять эта логика?"
        ),
    },
    "ask.confirm_candidate": {
        ConversationLanguage.ENGLISH: "Did you mean {candidate}?",
        ConversationLanguage.ARABIC: "تقصد {candidate}؟",
        ConversationLanguage.FRENCH: "Vouliez-vous dire {candidate} ?",
        ConversationLanguage.SPANISH: "¿Querías decir {candidate}?",
        ConversationLanguage.RUSSIAN: "Вы имели в виду {candidate}?",
    },
    # These two are followed by the open question itself, which the turn result appends
    # from the live contract. Repeating the question inside the sentence printed it
    # twice — one question asked once is the whole point of this flow.
    "ask.repeat_options": {
        ConversationLanguage.ENGLISH: "I did not catch that. You can pick: {options}.",
        ConversationLanguage.ARABIC: "ما فهمتش الإجابة. تقدر تختار: {options}.",
        ConversationLanguage.FRENCH: "Je n'ai pas compris. Vous pouvez choisir : {options}.",
        ConversationLanguage.SPANISH: "No lo entendí. Puedes elegir: {options}.",
        ConversationLanguage.RUSSIAN: "Я не понял. Можно выбрать: {options}.",
    },
    "ask.unsupported_value": {
        ConversationLanguage.ENGLISH: (
            "I cannot use that value here. You can pick: {options}."
        ),
        ConversationLanguage.ARABIC: "مش قادر أستخدم القيمة دي هنا. تقدر تختار: {options}.",
        ConversationLanguage.FRENCH: (
            "Je ne peux pas utiliser cette valeur ici. Vous pouvez choisir : {options}."
        ),
        ConversationLanguage.SPANISH: (
            "No puedo usar ese valor aquí. Puedes elegir: {options}."
        ),
        ConversationLanguage.RUSSIAN: (
            "Я не могу использовать это значение здесь. Можно выбрать: {options}."
        ),
    },
    "status.question_cancelled": {
        ConversationLanguage.ENGLISH: "Alright, I stopped that question. Nothing was saved.",
        ConversationLanguage.ARABIC: "تمام، وقفت السؤال ده. مفيش حاجة اتحفظت.",
        ConversationLanguage.FRENCH: "D'accord, j'arrête cette question. Rien n'a été enregistré.",
        ConversationLanguage.SPANISH: "De acuerdo, dejo esa pregunta. No se guardó nada.",
        ConversationLanguage.RUSSIAN: "Хорошо, я закрыл этот вопрос. Ничего не сохранено.",
    },
    "ask.retry_invalid": {
        ConversationLanguage.ENGLISH: "I couldn't map that answer safely. {question}",
        ConversationLanguage.ARABIC: "لم أقدر أربط الإجابة بأمان. {question}",
        ConversationLanguage.FRENCH: (
            "Je n'ai pas pu associer cette réponse en toute sécurité. {question}"
        ),
        ConversationLanguage.SPANISH: (
            "No pude asociar esa respuesta de forma segura. {question}"
        ),
        ConversationLanguage.RUSSIAN: "Я не смог безопасно сопоставить этот ответ. {question}",
    },
    "ask.scan_window_24h": {
        ConversationLanguage.ENGLISH: (
            "Should I use the provider's rolling 24-hour percentage change?"
        ),
        ConversationLanguage.ARABIC: (
            "أستخدم نسبة التغير المتحركة خلال آخر 24 ساعة من مزود البيانات؟"
        ),
        ConversationLanguage.FRENCH: (
            "Dois-je utiliser la variation glissante sur 24 heures du fournisseur ?"
        ),
        ConversationLanguage.SPANISH: (
            "¿Uso el cambio porcentual móvil de 24 horas del proveedor?"
        ),
        ConversationLanguage.RUSSIAN: (
            "Использовать скользящее 24-часовое процентное изменение провайдера?"
        ),
    },
    # -- turn outcome ------------------------------------------------------------
    "status.question_answered": {
        ConversationLanguage.ENGLISH: "That answered the open question.",
        ConversationLanguage.ARABIC: "كده تمت الإجابة عن السؤال المفتوح.",
        ConversationLanguage.FRENCH: "La question ouverte a été traitée.",
        ConversationLanguage.SPANISH: "La pregunta pendiente quedó respondida.",
        ConversationLanguage.RUSSIAN: "Открытый вопрос закрыт.",
    },
    "status.draft_version": {
        ConversationLanguage.ENGLISH: "The inactive draft is now version {version}.",
        ConversationLanguage.ARABIC: "المسودة غير المفعلة أصبحت الإصدار {version}.",
        ConversationLanguage.FRENCH: (
            "Le brouillon inactif est maintenant à la version {version}."
        ),
        ConversationLanguage.SPANISH: "El borrador inactivo ahora es la versión {version}.",
        ConversationLanguage.RUSSIAN: "Неактивный черновик теперь версии {version}.",
    },
    #: Names the version as well as the count. Approval is bound to a version, so a
    #: reply that states what the draft holds without saying which version it is leaves
    #: the trader unable to tell whether their approval still covers it.
    "status.draft_count": {
        ConversationLanguage.ENGLISH: (
            "The draft currently has {count} rule{suffix} at version {version}. "
            "Tell me what to change."
        ),
        ConversationLanguage.ARABIC: (
            "المسودة تحتوي حاليًا على {count} قاعدة في الإصدار {version}. "
            "اكتب التعديل المطلوب."
        ),
        ConversationLanguage.FRENCH: (
            "Le brouillon contient actuellement {count} règle{suffix} en version "
            "{version}. Indiquez la modification souhaitée."
        ),
        ConversationLanguage.SPANISH: (
            "El borrador tiene actualmente {count} regla{suffix} en la versión "
            "{version}. Indica qué quieres cambiar."
        ),
        ConversationLanguage.RUSSIAN: (
            "Сейчас в черновике {count} правил, версия {version}. Укажите, что изменить."
        ),
    },
    "status.no_action": {
        ConversationLanguage.ENGLISH: "Nothing changed on this turn.",
        ConversationLanguage.ARABIC: "لم يتغير شيء في هذه الرسالة.",
        ConversationLanguage.FRENCH: "Aucun changement pendant ce tour.",
        ConversationLanguage.SPANISH: "No cambió nada en este turno.",
        ConversationLanguage.RUSSIAN: "В этом сообщении ничего не изменилось.",
    },
    "status.rule_completed": {
        ConversationLanguage.ENGLISH: (
            "Done — the inactive rule is complete and ready for review."
        ),
        ConversationLanguage.ARABIC: "تم — القاعدة غير المفعلة اكتملت وأصبحت جاهزة للمراجعة.",
        ConversationLanguage.FRENCH: (
            "Terminé — la règle inactive est complète et prête à être vérifiée."
        ),
        ConversationLanguage.SPANISH: (
            "Listo: la regla inactiva está completa y lista para revisión."
        ),
        ConversationLanguage.RUSSIAN: (
            "Готово — неактивное правило завершено и готово к проверке."
        ),
    },
    "status.question_fallback": {
        ConversationLanguage.ENGLISH: (
            "I couldn't answer that safely from the current draft. Tell me the result "
            "you want to scan for, and I'll ask only for the missing detail."
        ),
        ConversationLanguage.ARABIC: (
            "لم أقدر أجاوب بأمان من المسودة الحالية. اكتب نتيجة الفحص المطلوبة "
            "وسأسأل عن التفصيلة الناقصة فقط."
        ),
        ConversationLanguage.FRENCH: (
            "Je ne peux pas répondre en toute sécurité à partir du brouillon actuel. "
            "Décrivez le résultat à scanner et je demanderai uniquement le détail manquant."
        ),
        ConversationLanguage.SPANISH: (
            "No pude responder con seguridad desde el borrador actual. Describe el "
            "resultado que quieres escanear y preguntaré solo el dato que falta."
        ),
        ConversationLanguage.RUSSIAN: (
            "Я не могу безопасно ответить по текущему черновику. Опишите результат "
            "сканирования, и я спрошу только недостающую деталь."
        ),
    },
    # -- on-demand scan ----------------------------------------------------------
    "scan.acknowledged": {
        ConversationLanguage.ENGLISH: "I can run that as a read-only Scanner check.",
        ConversationLanguage.ARABIC: "أقدر أنفذ هذا كفحص Scanner للقراءة فقط.",
        ConversationLanguage.FRENCH: (
            "Je peux exécuter cette demande comme un scan en lecture seule."
        ),
        ConversationLanguage.SPANISH: "Puedo ejecutar esto como un escaneo de solo lectura.",
        ConversationLanguage.RUSSIAN: "Я могу выполнить это как Scanner только для чтения.",
    },
    "scan.scope_required": {
        ConversationLanguage.ENGLISH: (
            "First choose the screened scope: all eligible coins, a Favorites list, or "
            "specific eligible coins."
        ),
        ConversationLanguage.ARABIC: (
            "اختر أولًا نطاق الفحص: كل العملات المؤهلة، قائمة المفضلة، أو عملات مؤهلة محددة."
        ),
        ConversationLanguage.FRENCH: (
            "Choisissez d'abord la portée filtrée : toutes les cryptos éligibles, une "
            "liste de favoris ou des cryptos précises."
        ),
        ConversationLanguage.SPANISH: (
            "Primero elige el alcance filtrado: todas las monedas elegibles, una lista "
            "de favoritos o monedas específicas."
        ),
        ConversationLanguage.RUSSIAN: (
            "Сначала выберите проверяемый охват: все подходящие монеты, список "
            "избранного или конкретные монеты."
        ),
    },
    "scan.mode_required": {
        ConversationLanguage.ENGLISH: (
            "Choose Scanner first. I'll keep this current-market request and continue "
            "when the screened scope is ready."
        ),
        ConversationLanguage.ARABIC: (
            "اختر Scanner أولًا. سأحتفظ بطلب السوق الحالي وأكمل بمجرد تجهيز نطاق الفحص."
        ),
        ConversationLanguage.FRENCH: (
            "Choisissez d'abord Scanner. Je conserve cette demande et je continuerai "
            "dès que la portée filtrée sera prête."
        ),
        ConversationLanguage.SPANISH: (
            "Primero elige Scanner. Conservaré esta solicitud y continuaré cuando el "
            "alcance filtrado esté listo."
        ),
        ConversationLanguage.RUSSIAN: (
            "Сначала выберите Scanner. Я сохраню запрос и продолжу, когда проверяемый "
            "охват будет готов."
        ),
    },
    "scan.running": {
        ConversationLanguage.ENGLISH: "Running the governed read-only Scanner now.",
        ConversationLanguage.ARABIC: "أشغّل الآن فحص Scanner المقيّد للقراءة فقط.",
        ConversationLanguage.FRENCH: "Exécution du Scanner gouverné en lecture seule.",
        ConversationLanguage.SPANISH: "Ejecutando ahora el Scanner gobernado de solo lectura.",
        ConversationLanguage.RUSSIAN: "Запускаю управляемый Scanner только для чтения.",
    },
    # -- governed screened scope --------------------------------------------------
    "scope.methodology_unavailable": {
        ConversationLanguage.ENGLISH: (
            "Screened monitoring is unavailable because no approved methodology is active."
        ),
        ConversationLanguage.ARABIC: (
            "المراقبة المفحوصة غير متاحة لعدم وجود منهجية معتمدة ونشطة."
        ),
        ConversationLanguage.FRENCH: (
            "La surveillance filtrée est indisponible car aucune méthodologie approuvée "
            "n'est active."
        ),
        ConversationLanguage.SPANISH: (
            "La monitorización filtrada no está disponible porque no hay una metodología "
            "aprobada activa."
        ),
        ConversationLanguage.RUSSIAN: (
            "Проверенный мониторинг недоступен: нет активной утверждённой методологии."
        ),
    },
    "scope.watchlist_missing": {
        ConversationLanguage.ENGLISH: (
            "You do not have a Favorites list yet. Choose another screened scope."
        ),
        ConversationLanguage.ARABIC: (
            "لا توجد لديك قائمة مفضلة بعد. اختر نطاقًا مفحوصًا آخر."
        ),
        ConversationLanguage.FRENCH: (
            "Vous n'avez pas encore de liste de favoris. Choisissez un autre périmètre "
            "filtré."
        ),
        ConversationLanguage.SPANISH: (
            "Todavía no tienes una lista de favoritos. Elige otro alcance filtrado."
        ),
        ConversationLanguage.RUSSIAN: (
            "У вас пока нет списка избранного. Выберите другую проверенную область."
        ),
    },
    "scope.selected": {
        ConversationLanguage.ENGLISH: (
            "The screened scope is selected. I only need the measurement window."
        ),
        ConversationLanguage.ARABIC: (
            "تم اختيار النطاق المفحوص. أحتاج فقط إلى نافذة القياس."
        ),
        ConversationLanguage.FRENCH: (
            "Le périmètre filtré est sélectionné. Il ne manque que la fenêtre de mesure."
        ),
        ConversationLanguage.SPANISH: (
            "El alcance filtrado está seleccionado. Solo falta la ventana de medición."
        ),
        ConversationLanguage.RUSSIAN: (
            "Проверенная область выбрана. Осталось указать окно измерения."
        ),
    },
    "ask.universe": {
        ConversationLanguage.ENGLISH: "Which screened assets should HilalMarkets watch?",
        ConversationLanguage.ARABIC: "أي أصول مفحوصة تريد من HilalMarkets مراقبتها؟",
        ConversationLanguage.FRENCH: (
            "Quels actifs filtrés HilalMarkets doit-il surveiller ?"
        ),
        ConversationLanguage.SPANISH: "¿Qué activos filtrados debe vigilar HilalMarkets?",
        ConversationLanguage.RUSSIAN: (
            "Какие проверенные активы должен отслеживать HilalMarkets?"
        ),
    },
    "ask.watchlist": {
        ConversationLanguage.ENGLISH: "Which Favorites list should HilalMarkets use?",
        ConversationLanguage.ARABIC: "أي قائمة مفضلة تريد أن يستخدمها HilalMarkets؟",
        ConversationLanguage.FRENCH: (
            "Quelle liste de favoris HilalMarkets doit-il utiliser ?"
        ),
        ConversationLanguage.SPANISH: "¿Qué lista de favoritos debe usar HilalMarkets?",
        ConversationLanguage.RUSSIAN: (
            "Какой список избранного должен использовать HilalMarkets?"
        ),
    },
    "ask.explicit_assets": {
        ConversationLanguage.ENGLISH: (
            "Which eligible spot assets should HilalMarkets watch?"
        ),
        ConversationLanguage.ARABIC: "ما أصول السبوت المؤهلة التي تريد مراقبتها؟",
        ConversationLanguage.FRENCH: (
            "Quels actifs spot éligibles HilalMarkets doit-il surveiller ?"
        ),
        ConversationLanguage.SPANISH: (
            "¿Qué activos spot elegibles debe vigilar HilalMarkets?"
        ),
        ConversationLanguage.RUSSIAN: (
            "Какие допустимые спотовые активы нужно отслеживать?"
        ),
    },
    # -- read-only Scanner presentation -------------------------------------------
    "scanner.title": {
        ConversationLanguage.ENGLISH: "Read-only Scanner",
        ConversationLanguage.ARABIC: "الفاحص للقراءة فقط",
        ConversationLanguage.FRENCH: "Scanner en lecture seule",
        ConversationLanguage.SPANISH: "Escáner de solo lectura",
        ConversationLanguage.RUSSIAN: "Сканер только для чтения",
    },
    "scanner.checked": {
        ConversationLanguage.ENGLISH: "screened coins checked",
        ConversationLanguage.ARABIC: "عملة مفحوصة تم التحقق منها",
        ConversationLanguage.FRENCH: "cryptos filtrées vérifiées",
        ConversationLanguage.SPANISH: "monedas filtradas revisadas",
        ConversationLanguage.RUSSIAN: "проверенных отфильтрованных монет",
    },
    "scanner.matched": {
        ConversationLanguage.ENGLISH: "matched",
        ConversationLanguage.ARABIC: "مطابقة",
        ConversationLanguage.FRENCH: "correspondances",
        ConversationLanguage.SPANISH: "coincidencias",
        ConversationLanguage.RUSSIAN: "совпадений",
    },
    "scanner.read_only": {
        ConversationLanguage.ENGLISH: "Read-only",
        ConversationLanguage.ARABIC: "للقراءة فقط",
        ConversationLanguage.FRENCH: "Lecture seule",
        ConversationLanguage.SPANISH: "Solo lectura",
        ConversationLanguage.RUSSIAN: "Только чтение",
    },
    "scanner.no_changes": {
        ConversationLanguage.ENGLISH: "no setup changes",
        ConversationLanguage.ARABIC: "لا تغييرات على الإعداد",
        ConversationLanguage.FRENCH: "aucune modification de la configuration",
        ConversationLanguage.SPANISH: "sin cambios en la configuración",
        ConversationLanguage.RUSSIAN: "настройки не изменены",
    },
    #: Never softened and never dropped: a scan result is research, not advice.
    "scanner.research": {
        ConversationLanguage.ENGLISH: "Research only. Not buy or sell advice.",
        ConversationLanguage.ARABIC: "للبحث فقط، وليست توصية شراء أو بيع.",
        ConversationLanguage.FRENCH: "Recherche uniquement, sans conseil d'achat ou de vente.",
        ConversationLanguage.SPANISH: "Solo investigación; no es consejo de compra o venta.",
        ConversationLanguage.RUSSIAN: (
            "Только исследование, не рекомендация покупать или продавать."
        ),
    },
    "scan_result.none": {
        ConversationLanguage.ENGLISH: (
            "No screened coins matched among {checked} checked over the rolling "
            "24-hour window. Data time: {time}."
        ),
        ConversationLanguage.ARABIC: (
            "لم أجد عملات مطابقة بين {checked} عملة مفحوصة خلال نافذة آخر 24 ساعة. "
            "وقت البيانات: {time}."
        ),
        ConversationLanguage.FRENCH: (
            "Aucune crypto filtrée ne correspond parmi les {checked} vérifiées sur la "
            "fenêtre glissante de 24 heures. Heure des données : {time}."
        ),
        ConversationLanguage.SPANISH: (
            "Ninguna moneda filtrada coincidió entre las {checked} revisadas en la "
            "ventana móvil de 24 horas. Hora de los datos: {time}."
        ),
        ConversationLanguage.RUSSIAN: (
            "В скользящем окне 24 часа совпадений среди {checked} проверенных монет "
            "нет. Время данных: {time}."
        ),
    },
    "scan_result.some": {
        ConversationLanguage.ENGLISH: (
            "Matches over the rolling 24-hour window: {rows}{extra}. Checked {checked} "
            "screened coins. Data time: {time}."
        ),
        ConversationLanguage.ARABIC: (
            "العملات المطابقة خلال نافذة آخر 24 ساعة: {rows}{extra}. تم فحص {checked} "
            "عملة. وقت البيانات: {time}."
        ),
        ConversationLanguage.FRENCH: (
            "Correspondances sur la fenêtre glissante de 24 heures : {rows}{extra}. "
            "{checked} cryptos filtrées vérifiées. Heure des données : {time}."
        ),
        ConversationLanguage.SPANISH: (
            "Coincidencias en la ventana móvil de 24 horas: {rows}{extra}. Se revisaron "
            "{checked} monedas filtradas. Hora de los datos: {time}."
        ),
        ConversationLanguage.RUSSIAN: (
            "Совпадения в скользящем окне 24 часа: {rows}{extra}. Проверено монет: "
            "{checked}. Время данных: {time}."
        ),
    },
    # -- refused scans -------------------------------------------------------------
    "scan_error.scope": {
        ConversationLanguage.ENGLISH: (
            "I couldn't run the scan because its screened scope is not ready. Choose the "
            "methodology and assets, then run it again."
        ),
        ConversationLanguage.ARABIC: (
            "لم أتمكن من تشغيل الفحص لأن نطاق العملات المفحوصة غير مكتمل. "
            "اختر المنهجية والعملات ثم أعد المحاولة."
        ),
        ConversationLanguage.FRENCH: (
            "Le scan n'a pas pu démarrer car son périmètre filtré n'est pas prêt. "
            "Choisissez la méthodologie et les actifs, puis réessayez."
        ),
        ConversationLanguage.SPANISH: (
            "No pude ejecutar el escaneo porque su alcance filtrado no está listo. "
            "Elige la metodología y los activos e inténtalo de nuevo."
        ),
        ConversationLanguage.RUSSIAN: (
            "Сканирование не запущено: выбранная проверенная область ещё не готова. "
            "Выберите методологию и активы и повторите попытку."
        ),
    },
    "scan_error.quota": {
        ConversationLanguage.ENGLISH: (
            "Scanner is not available under the current plan or its scan allowance has "
            "been used."
        ),
        ConversationLanguage.ARABIC: (
            "الفاحص غير متاح في الخطة الحالية أو تم استخدام حد الفحوصات المتاح."
        ),
        ConversationLanguage.FRENCH: (
            "Le Scanner n'est pas disponible avec l'offre actuelle ou le quota de scans "
            "est épuisé."
        ),
        ConversationLanguage.SPANISH: (
            "El Escáner no está disponible en el plan actual o se agotó su cuota."
        ),
        ConversationLanguage.RUSSIAN: (
            "Сканер недоступен на текущем плане или лимит сканирований исчерпан."
        ),
    },
    "scan_error.provider": {
        ConversationLanguage.ENGLISH: (
            "I couldn't load verified data for this screened scan, so I returned no "
            "invented results."
        ),
        ConversationLanguage.ARABIC: (
            "تعذر تحميل بيانات موثقة لهذا الفحص، لذلك لم أعرض أي نتائج مخترعة."
        ),
        ConversationLanguage.FRENCH: (
            "Les données vérifiées n'ont pas pu être chargées; aucun résultat n'a été "
            "inventé."
        ),
        ConversationLanguage.SPANISH: (
            "No pude cargar datos verificados para este escaneo; no se inventaron "
            "resultados."
        ),
        ConversationLanguage.RUSSIAN: (
            "Проверенные данные для сканирования не загрузились; вымышленные результаты "
            "не возвращались."
        ),
    },
    "scan_error.window": {
        ConversationLanguage.ENGLISH: (
            "This verified scan currently supports the rolling 24-hour window only."
        ),
        ConversationLanguage.ARABIC: "الفحص الموثق يدعم حاليًا نافذة آخر 24 ساعة فقط.",
        ConversationLanguage.FRENCH: (
            "Ce scan vérifié prend actuellement en charge uniquement la fenêtre "
            "glissante de 24 heures."
        ),
        ConversationLanguage.SPANISH: (
            "Este escaneo verificado solo admite actualmente la ventana móvil de 24 horas."
        ),
        ConversationLanguage.RUSSIAN: (
            "Проверенный сканер сейчас поддерживает только скользящее окно 24 часа."
        ),
    },
    "scan_error.generic": {
        ConversationLanguage.ENGLISH: (
            "I couldn't complete this read-only scan. No setup was changed."
        ),
        ConversationLanguage.ARABIC: "تعذر إكمال هذا الفحص للقراءة فقط، ولم يتغير أي إعداد.",
        ConversationLanguage.FRENCH: (
            "Ce scan en lecture seule n'a pas pu être terminé. La configuration n'a pas "
            "été modifiée."
        ),
        ConversationLanguage.SPANISH: (
            "No pude completar este escaneo de solo lectura. La configuración no cambió."
        ),
        ConversationLanguage.RUSSIAN: (
            "Не удалось завершить сканирование только для чтения. Настройки не изменены."
        ),
    },
}

def _distinctive_words(language: ConversationLanguage) -> frozenset[str]:
    """A language's marker words, minus the ones it shares with its Latin-script peer.

    Detection and the reply check must agree on what counts as evidence for a language.
    Two separate word lists were the original defect here — one module thought
    ``para`` was Spanish, another had never heard of it — so both read this.
    """

    return frozenset(_WORD_MARKERS.get(language, ())) - _AMBIGUOUS


#: The choices a clarification offers, per field. Answer options are wording the server
#: writes, so they belong beside every other sentence it writes — a private copy in the
#: agent meant an Arabic question arrived with English buttons under it.
#: ``trigger_timeframe`` is deliberately untranslated: ``1h``/``4h``/``1d`` are codes the
#: product shows identically in every language.
#: Button wording, keyed by the canonical value it stands for. Pairing by key rather
#: than by list position is deliberate: a positional table silently mislabels every
#: option the moment one language orders them differently or omits one.
_OPTION_LABELS: Final[dict[str, dict[ConversationLanguage, dict[str, str]]]] = {
    "reference_point": {
        ConversationLanguage.ENGLISH: {
            "candle_open": "Candle open",
            "previous_close": "Previous candle close",
        },
        ConversationLanguage.ARABIC: {
            "candle_open": "افتتاح الشمعة",
            "previous_close": "إغلاق الشمعة السابقة",
        },
        ConversationLanguage.FRENCH: {
            "candle_open": "Ouverture de la bougie",
            "previous_close": "Clôture de la bougie précédente",
        },
        ConversationLanguage.SPANISH: {
            "candle_open": "Apertura de la vela",
            "previous_close": "Cierre de la vela anterior",
        },
        ConversationLanguage.RUSSIAN: {
            "candle_open": "Открытие свечи",
            "previous_close": "Закрытие предыдущей свечи",
        },
    },
    "comparator": {
        ConversationLanguage.ENGLISH: {
            "gte": "At the threshold or beyond",
            "gt": "Only beyond the threshold",
        },
        ConversationLanguage.ARABIC: {
            "gte": "عند الحد أو أكثر",
            "gt": "فقط بعد تجاوز الحد",
        },
        ConversationLanguage.FRENCH: {
            "gte": "Au seuil ou au-delà",
            "gt": "Seulement au-delà du seuil",
        },
        ConversationLanguage.SPANISH: {
            "gte": "En el umbral o más",
            "gt": "Solo por encima del umbral",
        },
        ConversationLanguage.RUSSIAN: {
            "gte": "На пороге или выше",
            "gt": "Только выше порога",
        },
    },
}

#: Codes shown the same way in every language. The timeframe shortlist comes from the
#: canonical registry, so it can never offer a period the compiler cannot execute.
_UNTRANSLATED_OPTIONS: Final[dict[str, tuple[str, ...]]] = {
    "trigger_timeframe": COMMON_TIMEFRAMES,
}

#: The names of the governed screened scopes, as a trader reads them. These label
#: buttons, so they are wording the server writes and belong here with the rest of it.
_SCOPE_LABELS: Final[dict[ConversationLanguage, dict[str, str]]] = {
    ConversationLanguage.ENGLISH: {
        "eligible_market": "All eligible spot assets",
        "approved_watchlist": "My Favorites",
        "explicit_assets": "Specific eligible assets",
    },
    ConversationLanguage.ARABIC: {
        "eligible_market": "كل أصول السبوت المؤهلة",
        "approved_watchlist": "قائمة المفضلة",
        "explicit_assets": "أصول مؤهلة محددة",
    },
    ConversationLanguage.FRENCH: {
        "eligible_market": "Tous les actifs spot éligibles",
        "approved_watchlist": "Mes favoris",
        "explicit_assets": "Actifs éligibles précis",
    },
    ConversationLanguage.SPANISH: {
        "eligible_market": "Todos los activos spot elegibles",
        "approved_watchlist": "Mis favoritos",
        "explicit_assets": "Activos elegibles específicos",
    },
    ConversationLanguage.RUSSIAN: {
        "eligible_market": "Все допустимые спотовые активы",
        "approved_watchlist": "Мои избранные",
        "explicit_assets": "Выбранные допустимые активы",
    },
}


def scope_labels(language: ConversationLanguage) -> dict[str, str]:
    """The screened-scope button labels, in ``language``."""

    return dict(_SCOPE_LABELS.get(language) or _SCOPE_LABELS[ConversationLanguage.ENGLISH])


def scanner_labels(language: ConversationLanguage) -> dict[str, str]:
    """The words the read-only Scanner result card is built from."""

    return {
        name: localized(f"scanner.{name}", language)
        for name in ("title", "checked", "matched", "read_only", "no_changes", "research")
    }


#: Which refusal a governed scan error belongs to. The trader is told what to do next,
#: which depends on the family, never on the internal code.
_SCAN_ERROR_FAMILY: Final[dict[str, str]] = {
    "screening_methodology_required": "scope",
    "screened_universe_required": "scope",
    "screening_methodology_unavailable": "scope",
    "empty_screened_universe": "scope",
    "approved_watchlist_required": "scope",
    "light_prompt_scan_not_available": "quota",
    "light_prompt_scans_quota_exceeded": "quota",
    "market_provider_unavailable": "provider",
    "percentage_data_unavailable": "provider",
    "scanner_runtime_failure": "provider",
    "percentage_window_not_supported": "window",
}


#: Refusals the trader or the platform can still clear — a missing screened scope, a
#: provider that is down. The scan itself is still what they asked for, so its values
#: are kept. Everything else (quota spent, window unavailable) ends the attempt.
_RESOLVABLE_SCAN_ERROR_FAMILIES: Final[frozenset[str]] = frozenset({"scope", "provider"})


def scan_error_is_resolvable(code: str) -> bool:
    """Whether a refused scan is worth keeping the collected values for."""

    return _SCAN_ERROR_FAMILY.get(code, "generic") in _RESOLVABLE_SCAN_ERROR_FAMILIES


def governed_scan_error(code: str, safe_message: str, language: ConversationLanguage) -> str:
    """Why a governed scan did not run, in the trader's language.

    The internal code never reaches the reader. Only the English generic case appends
    the safe message, because that message is not translated and appending it to a
    Russian sentence would be the language switch this module exists to prevent.
    """

    family = _SCAN_ERROR_FAMILY.get(code, "generic")
    message = localized(f"scan_error.{family}", language)
    tail = (safe_message or "").strip()
    if language is ConversationLanguage.ENGLISH and family == "generic" and tail:
        return f"{message} {tail}"[:500]
    return message


def option_label_map(field: str, language: ConversationLanguage) -> dict[str, str]:
    """Canonical value → the words its button shows, in ``language``.

    The map is the authority; the ordered list below is a view of it. Callers that
    need to know *which* option a label stands for must use this, never the position.
    """

    fixed = _UNTRANSLATED_OPTIONS.get(field)
    if fixed is not None:
        return {item: item for item in fixed}
    entry = _OPTION_LABELS.get(field, {})
    chosen = entry.get(language) or entry.get(ConversationLanguage.ENGLISH) or {}
    return dict(chosen)


def localized_options(field: str, language: ConversationLanguage) -> list[str]:
    """The answer choices for ``field``, in ``language``. Empty when the field has none."""

    return list(option_label_map(field, language).values())


def language_of(value: str | ConversationLanguage | None) -> ConversationLanguage:
    """The conversation language for a stored code, never failing.

    Callers hold the language as a plain string on a turn, a chat row or a context
    blob. Each one used to coerce it with its own private helper, and the helpers
    disagreed about ``"ar-EG"`` and about the empty string. This is the single coercion.
    """

    return ConversationLanguage.parse(
        value if value is None else str(value)
    ) or ConversationLanguage.ENGLISH


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
    if language is ConversationLanguage.ARABIC:
        return bool(_ARABIC_SCRIPT.search(body))
    if language is ConversationLanguage.RUSSIAN:
        return bool(_CYRILLIC_SCRIPT.search(body))
    # A Latin-script conversation may legitimately quote an Arabic or Russian name, but
    # a reply that is *mostly* another script is a language switch, not a quotation.
    foreign = len(_ARABIC_SCRIPT.findall(body)) + len(_CYRILLIC_SCRIPT.findall(body))
    if foreign * 2 > len(body):
        return False
    # French and Spanish share an alphabet, so script alone cannot separate them. Their
    # own marker words can: a French reply to a Spanish conversation is a switch a
    # script check would wave through.
    words = {word.casefold() for word in _WORD_RE.findall(body)}
    if language not in {ConversationLanguage.FRENCH, ConversationLanguage.SPANISH}:
        return True
    mine = _distinctive_words(language)
    other = _distinctive_words(
        ConversationLanguage.SPANISH
        if language is ConversationLanguage.FRENCH
        else ConversationLanguage.FRENCH
    )
    # Silence is not a mismatch. A short reply carrying neither language's marker words
    # is accepted rather than rejected on no evidence.
    return len(words & other) <= len(words & mine)


def translation_coverage() -> dict[str, list[str]]:
    """Keys missing a translation, per language. Used by the language matrix test."""

    missing: dict[str, list[str]] = {}
    for language in ConversationLanguage:
        absent = sorted(key for key, entry in _CATALOGUE.items() if language not in entry)
        if absent:
            missing[language.value] = absent
    return missing
