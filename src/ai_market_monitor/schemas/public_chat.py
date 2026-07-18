import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

InquiryCategory = Literal[
    "product",
    "screening",
    "pricing",
    "technical_support",
    "partnership",
    "other",
]

PublicSupportStage = Literal[
    "GREETING_AND_PROFILE",
    "UNDERSTAND_QUESTION",
    "RETRIEVE_PRODUCT_DATA",
    "ANSWER",
    "CLARIFY",
    "PUBLIC_PASSPORT_LOOKUP",
    "AUTHENTICATED_ACCOUNT_SUPPORT",
    "TROUBLESHOOT",
    "KNOWLEDGE_GAP",
    "INQUIRY_FORM",
    "INQUIRY_CONFIRMED",
    "RATING",
    "FOLLOW_UP",
    "REFUSAL",
]
PublicChatAnswerStatus = Literal["answered", "unsupported", "refused"]

PublicSupportToolName = Literal[
    "account_state",
    "telegram_status",
    "watch_plan_summary",
    "recent_alerts",
    "entitlement_usage",
    "screened_watchlist",
    "public_passport",
]


class StrictPublicChatModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PublicChatProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    remember_on_device: bool = False

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Name is required")
        return normalized


class PublicChatBootstrapResponse(BaseModel):
    csrf_token: str
    profile_storage_key: str
    profile_version: int
    consent_version: int
    privacy_url: str
    max_message_length: int
    max_inquiry_length: int
    conversation_retention_days: int


class PublicChatAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=800)
    session_id: str = Field(min_length=16, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    client_message_id: str = Field(
        min_length=8,
        max_length=120,
        pattern=r"^[A-Za-z0-9:_-]+$",
    )
    source_page: str = Field(default="/", min_length=1, max_length=240)
    profile: PublicChatProfile | None = None

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Question is required")
        return normalized


class PublicChatRelatedLink(BaseModel):
    route_id: str
    label: str
    path: str


class PublicChatAnswerResponse(BaseModel):
    status: PublicChatAnswerStatus
    message: str
    source_ids: list[str]
    related_links: list[PublicChatRelatedLink]
    coverage_score: float = Field(ge=0, le=1)
    show_inquiry_form: bool
    knowledge_gap_category: str | None = None
    stage: PublicSupportStage = "ANSWER"
    intent: str = "product_help"
    clarification_question: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    answer_complete: bool = True
    suggested_follow_ups: list[str] = Field(default_factory=list, max_length=4)
    safety_boundary: str | None = None
    authenticated_context_used: bool = False


class PublicSupportAIResponse(StrictPublicChatModel):
    stage: PublicSupportStage
    intent: str = Field(min_length=1, max_length=100)
    answer: str = Field(min_length=1, max_length=1800)
    tone: Literal["friendly", "concise", "supportive", "clear"]
    clarification_question: str | None = Field(max_length=500)
    source_ids: list[str] = Field(max_length=12)
    related_route_ids: list[str] = Field(max_length=8)
    requested_tools: list[PublicSupportToolName] = Field(max_length=6)
    confidence: float = Field(ge=0, le=1)
    answer_complete: bool
    show_inquiry_form: bool
    inquiry_category: InquiryCategory | None
    handoff_reason: str | None = Field(max_length=500)
    safety_boundary: Literal[
        "none",
        "no_investment_advice",
        "no_religious_ruling",
        "no_private_data",
        "product_scope_only",
        "security_boundary",
    ]
    suggested_follow_ups: list[str] = Field(max_length=4)

    @field_validator("suggested_follow_ups")
    @classmethod
    def validate_follow_ups(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(item.split()) for item in value]
        if any(not item or len(item) > 160 for item in cleaned):
            raise ValueError("Suggested follow-ups must contain 1-160 characters")
        if any(re.search(r"(?:https?://|www\.)", item, flags=re.I) for item in cleaned):
            raise ValueError("Suggested follow-ups cannot contain URLs")
        return cleaned

    @field_validator(
        "answer",
        "clarification_question",
        "handoff_reason",
        "safety_boundary",
    )
    @classmethod
    def reject_model_authored_urls(cls, value: str | None) -> str | None:
        if value and re.search(r"(?:https?://|www\.)", value, flags=re.IGNORECASE):
            raise ValueError("Public support responses cannot contain model-authored URLs")
        return value.strip() if value else value


class PublicSupportToolResult(StrictPublicChatModel):
    tool_name: PublicSupportToolName
    status: Literal["success", "unavailable", "blocked"]
    data: dict[str, Any]
    evidence_refs: list[str] = Field(max_length=30)
    route_id: str | None = Field(max_length=80)
    authoritative: bool = True


class PublicInquiryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: PublicChatProfile
    details: str = Field(min_length=5, max_length=4000)
    category: InquiryCategory = "other"
    source_page: str = Field(default="/", min_length=1, max_length=240)
    attribution_consent: bool = False
    referrer: str | None = Field(default=None, max_length=500)
    utm_source: str | None = Field(default=None, max_length=120)
    utm_medium: str | None = Field(default=None, max_length=120)
    utm_campaign: str | None = Field(default=None, max_length=120)
    knowledge_gap_category: str = Field(default="unverified_product_question", max_length=80)
    idempotency_key: str = Field(min_length=16, max_length=160, pattern=r"^[A-Za-z0-9:_-]+$")
    company_website: str = Field(default="", max_length=300)

    @field_validator("details")
    @classmethod
    def clean_details(cls, value: str) -> str:
        normalized = "\n".join(line.strip() for line in value.splitlines()).strip()
        if len(normalized) < 5:
            raise ValueError("Please include a little more detail")
        return normalized

    @model_validator(mode="after")
    def reject_honeypot(self) -> "PublicInquiryRequest":
        if self.company_website.strip():
            raise ValueError("Invalid form submission")
        if not self.attribution_consent:
            self.referrer = None
            self.utm_source = None
            self.utm_medium = None
            self.utm_campaign = None
        return self


class PublicInquiryResponse(BaseModel):
    reference: str
    status: Literal["received"]
    masked_email: str
    feedback_token: str
    email_delivery_status: Literal["queued", "sent", "partial", "retrying"]
    message: str


class PublicInquiryRatingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=8, max_length=32)
    feedback_token: str = Field(min_length=32, max_length=128)
    rating: int | None = Field(default=None, ge=1, le=5)
    helpful: bool | None = None
    feedback: str | None = Field(default=None, max_length=800)

    @model_validator(mode="after")
    def require_rating(self) -> "PublicInquiryRatingRequest":
        if self.rating is None and self.helpful is None:
            raise ValueError("Choose a rating or helpful response")
        return self


class PublicInquiryRatingResponse(BaseModel):
    status: Literal["recorded"]
    message: str
