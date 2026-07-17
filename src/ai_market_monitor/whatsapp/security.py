import hashlib
import hmac
import re


class WhatsAppSecurityError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def verify_webhook_token(*, supplied: str | None, expected: str | None) -> bool:
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


def verify_webhook_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str | None,
) -> bool:
    if not signature_header or not app_secret:
        return False
    prefix, separator, supplied = signature_header.partition("=")
    if (
        prefix.casefold() != "sha256"
        or not separator
        or not re.fullmatch(r"[0-9a-fA-F]{64}", supplied)
    ):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied.casefold(), expected)


def normalize_e164(value: str) -> str:
    compact = re.sub(r"[\s().-]", "", value.strip())
    if not re.fullmatch(r"\+[1-9]\d{7,14}", compact):
        raise WhatsAppSecurityError(
            "invalid_phone_number",
            "Enter a valid international phone number beginning with + and country code.",
        )
    return compact


def wa_id_to_e164(wa_id: str) -> str:
    normalized = wa_id.strip()
    if not re.fullmatch(r"[1-9]\d{7,19}", normalized):
        raise WhatsAppSecurityError("invalid_wa_id", "Meta returned an invalid WhatsApp recipient.")
    e164 = f"+{normalized}"
    if len(e164) > 16:
        raise WhatsAppSecurityError("invalid_wa_id", "Meta returned an invalid WhatsApp recipient.")
    return e164


def mask_e164(value: str) -> str:
    try:
        normalized = normalize_e164(value)
    except WhatsAppSecurityError:
        return "unavailable"
    digits = normalized[1:]
    visible_prefix = digits[: min(3, max(1, len(digits) - 4))]
    hidden = "*" * max(4, len(digits) - len(visible_prefix) - 2)
    return f"+{visible_prefix}{hidden}{digits[-2:]}"


def payload_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
