from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


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


class PublicChatAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=800)
    session_id: str = Field(min_length=16, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    source_page: str = Field(default="/", min_length=1, max_length=240)

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
    status: Literal["answered", "unsupported", "refused"]
    message: str
    source_ids: list[str]
    related_links: list[PublicChatRelatedLink]
    coverage_score: float = Field(ge=0, le=1)
    show_inquiry_form: bool
    knowledge_gap_category: str | None = None


InquiryCategory = Literal[
    "product",
    "screening",
    "pricing",
    "technical_support",
    "partnership",
    "other",
]


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
