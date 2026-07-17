from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

WA_ID_PATTERN = r"^[1-9]\d{7,19}$"
LOCALE_PATTERN = r"^[a-z]{2}(?:_[A-Z]{2})?$"
TEMPLATE_NAME_PATTERN = r"^[a-z0-9_]{1,512}$"

WHATSAPP_OPT_IN_CATEGORIES = frozenset(
    {
        "account",
        "subscription",
        "compliance",
        "evidence",
        "watchlist_health",
        "operational",
        "lifecycle",
        "opportunity",
    }
)


class StrictWhatsAppModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WhatsAppReplyButton(StrictWhatsAppModel):
    id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=20)


class WhatsAppListRow(StrictWhatsAppModel):
    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=24)
    description: str | None = Field(default=None, max_length=72)


class WhatsAppListSection(StrictWhatsAppModel):
    title: str = Field(min_length=1, max_length=24)
    rows: list[WhatsAppListRow] = Field(min_length=1, max_length=10)


class WhatsAppSessionText(StrictWhatsAppModel):
    kind: Literal["session_text"] = "session_text"
    to: str = Field(pattern=WA_ID_PATTERN)
    body: str = Field(min_length=1, max_length=4096)
    preview_url: bool = False


class WhatsAppInteractiveButtons(StrictWhatsAppModel):
    kind: Literal["interactive_buttons"] = "interactive_buttons"
    to: str = Field(pattern=WA_ID_PATTERN)
    body: str = Field(min_length=1, max_length=1024)
    buttons: list[WhatsAppReplyButton] = Field(min_length=1, max_length=3)
    footer: str | None = Field(default=None, max_length=60)


class WhatsAppInteractiveList(StrictWhatsAppModel):
    kind: Literal["interactive_list"] = "interactive_list"
    to: str = Field(pattern=WA_ID_PATTERN)
    body: str = Field(min_length=1, max_length=1024)
    button_text: str = Field(min_length=1, max_length=20)
    sections: list[WhatsAppListSection] = Field(min_length=1, max_length=10)
    footer: str | None = Field(default=None, max_length=60)


class WhatsAppTemplateParameter(StrictWhatsAppModel):
    type: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=1024)


class WhatsAppTemplateComponent(StrictWhatsAppModel):
    type: Literal["header", "body", "button"]
    parameters: list[WhatsAppTemplateParameter] = Field(min_length=1, max_length=10)
    sub_type: Literal["url", "quick_reply"] | None = None
    index: int | None = Field(default=None, ge=0, le=9)

    @model_validator(mode="after")
    def validate_button_shape(self) -> "WhatsAppTemplateComponent":
        if self.type == "button" and (self.sub_type is None or self.index is None):
            raise ValueError("Template button components require sub_type and index")
        if self.type != "button" and (self.sub_type is not None or self.index is not None):
            raise ValueError("Only template button components accept sub_type and index")
        return self


class WhatsAppTemplateMessage(StrictWhatsAppModel):
    kind: Literal["template"] = "template"
    to: str = Field(pattern=WA_ID_PATTERN)
    name: str = Field(pattern=TEMPLATE_NAME_PATTERN)
    language: str = Field(pattern=LOCALE_PATTERN)
    components: list[WhatsAppTemplateComponent] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_component_order(self) -> "WhatsAppTemplateMessage":
        order = {"header": 0, "body": 1, "button": 2}
        positions = [order[component.type] for component in self.components]
        if positions != sorted(positions):
            raise ValueError("Template components must be ordered header, body, then button")
        body_count = sum(component.type == "body" for component in self.components)
        header_count = sum(component.type == "header" for component in self.components)
        if body_count > 1 or header_count > 1:
            raise ValueError("Templates accept at most one header and one body component")
        button_indexes = [
            component.index for component in self.components if component.type == "button"
        ]
        if len(button_indexes) != len(set(button_indexes)):
            raise ValueError("Template button indexes must be unique")
        return self


WhatsAppOutboundMessage = Annotated[
    WhatsAppSessionText
    | WhatsAppInteractiveButtons
    | WhatsAppInteractiveList
    | WhatsAppTemplateMessage,
    Field(discriminator="kind"),
]


class WhatsAppInboundBase(StrictWhatsAppModel):
    message_id: str = Field(min_length=1, max_length=255)
    wa_id: str = Field(pattern=WA_ID_PATTERN)
    profile_name: str | None = Field(default=None, max_length=160)
    timestamp: datetime


class WhatsAppInboundText(WhatsAppInboundBase):
    kind: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=4096)


class WhatsAppInboundButtonReply(WhatsAppInboundBase):
    kind: Literal["button_reply"] = "button_reply"
    reply_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=200)


class WhatsAppInboundListReply(WhatsAppInboundBase):
    kind: Literal["list_reply"] = "list_reply"
    reply_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1024)


class WhatsAppInboundUnsupported(WhatsAppInboundBase):
    kind: Literal["unsupported"] = "unsupported"
    message_type: str = Field(min_length=1, max_length=80)


WhatsAppInboundMessage = Annotated[
    WhatsAppInboundText
    | WhatsAppInboundButtonReply
    | WhatsAppInboundListReply
    | WhatsAppInboundUnsupported,
    Field(discriminator="kind"),
]


class WhatsAppDeliveryStatusEvent(StrictWhatsAppModel):
    provider_message_id: str = Field(min_length=1, max_length=255)
    status: Literal["sent", "delivered", "read", "failed", "deleted", "unknown"]
    timestamp: datetime
    recipient_wa_id: str | None = Field(default=None, pattern=WA_ID_PATTERN)
    error_code: str | None = Field(default=None, max_length=80)
    error_title: str | None = Field(default=None, max_length=160)
    error_message: str | None = Field(default=None, max_length=500)
    error_details: str | None = Field(default=None, max_length=500)


class WhatsAppDeliveryResult(StrictWhatsAppModel):
    provider_message_id: str = Field(min_length=1, max_length=255)
    accepted_wa_id: str | None = Field(default=None, pattern=WA_ID_PATTERN)


class WhatsAppLinkRequest(StrictWhatsAppModel):
    phone_e164: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    consent: Literal[True]
    categories: list[str] = Field(min_length=1, max_length=8)
    locale: str = Field(default="en_US", pattern=LOCALE_PATTERN)

    @model_validator(mode="after")
    def validate_categories(self) -> "WhatsAppLinkRequest":
        normalized = list(dict.fromkeys(self.categories))
        unknown = set(normalized) - WHATSAPP_OPT_IN_CATEGORIES
        if unknown:
            raise ValueError("Unsupported WhatsApp opt-in category")
        self.categories = normalized
        return self


class WhatsAppPreferencesUpdate(StrictWhatsAppModel):
    alerts_enabled: bool | None = None
    categories: list[str] | None = Field(default=None, min_length=1, max_length=8)
    locale: str | None = Field(default=None, pattern=LOCALE_PATTERN)

    @model_validator(mode="after")
    def validate_categories(self) -> "WhatsAppPreferencesUpdate":
        if self.categories is not None:
            normalized = list(dict.fromkeys(self.categories))
            if set(normalized) - WHATSAPP_OPT_IN_CATEGORIES:
                raise ValueError("Unsupported WhatsApp opt-in category")
            self.categories = normalized
        return self
