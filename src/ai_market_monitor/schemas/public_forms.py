from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class StrictPublicFormModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FirstTouchAttribution(StrictPublicFormModel):
    utm_source: str | None = Field(default=None, max_length=120)
    utm_medium: str | None = Field(default=None, max_length=120)
    utm_campaign: str | None = Field(default=None, max_length=120)
    utm_content: str | None = Field(default=None, max_length=120)
    utm_term: str | None = Field(default=None, max_length=120)
    referrer: str | None = Field(default=None, max_length=500)
    landing_page: str | None = Field(default=None, max_length=500)

    @field_validator("*", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        normalized = " ".join(str(value).split()).strip()
        return normalized or None


class WaitlistSignupRequest(StrictPublicFormModel):
    email: EmailStr
    source_page: str = Field(default="/", min_length=1, max_length=240)
    attribution: FirstTouchAttribution = Field(default_factory=FirstTouchAttribution)
    idempotency_key: str = Field(
        min_length=16,
        max_length=160,
        pattern=r"^[A-Za-z0-9:_-]+$",
    )
    company_website: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def reject_honeypot(self) -> "WaitlistSignupRequest":
        if self.company_website.strip():
            raise ValueError("Invalid form submission")
        return self


WaitlistDeliveryStatus = Literal["sent", "queued", "retrying", "not_configured"]


class WaitlistSignupResponse(StrictPublicFormModel):
    status: Literal["created", "already_registered"]
    created: bool
    code: Literal["waitlist_created", "duplicate_email"]
    sheet_delivery_status: WaitlistDeliveryStatus
    message: str


class ContactSubmissionRequest(StrictPublicFormModel):
    title: str = Field(min_length=3, max_length=180)
    email: EmailStr
    description: str = Field(min_length=10, max_length=5000)
    source_page: str = Field(default="/contact", min_length=1, max_length=240)
    idempotency_key: str = Field(
        min_length=16,
        max_length=160,
        pattern=r"^[A-Za-z0-9:_-]+$",
    )
    company_website: str = Field(default="", max_length=300)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return " ".join(value.split()).strip()

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        normalized = "\n".join(line.strip() for line in value.splitlines()).strip()
        if len(normalized) < 10:
            raise ValueError("Please include a little more detail")
        return normalized

    @model_validator(mode="after")
    def reject_honeypot(self) -> "ContactSubmissionRequest":
        if self.company_website.strip():
            raise ValueError("Invalid form submission")
        return self


class ContactSubmissionResponse(StrictPublicFormModel):
    status: Literal["sent", "queued"]
    message: str
    #: How many more messages this person may send inside the window. Returned so the
    #: page can say "one message left" instead of letting somebody write a second one
    #: and only then discover it will be refused.
    remaining_messages: int = Field(default=0, ge=0)
    window_hours: float = Field(default=1, gt=0)
