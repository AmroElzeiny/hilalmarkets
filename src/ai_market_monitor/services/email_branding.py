from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from ai_market_monitor.core.config import Settings

EMAIL_SHELL_MARKER = 'data-hm-email-shell="true"'


@dataclass(frozen=True, slots=True)
class BrandedEmail:
    subject: str
    text_body: str
    html_body: str


class HilalMarketsEmailRenderer:
    """Render one email-safe HilalMarkets frame for every SMTP message."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def ensure_shell(
        self,
        *,
        subject: str,
        html_body: str,
        preheader: str | None = None,
    ) -> str:
        if EMAIL_SHELL_MARKER in html_body:
            return html_body
        return self.shell(
            title=subject,
            preheader=preheader or subject,
            content_html=html_body,
        )

    def auth_code(
        self,
        *,
        recipient: str,
        code: str,
        purpose: str,
        ttl_minutes: int,
    ) -> BrandedEmail:
        labels = {
            "login": ("Your login code", "Use this code to sign in securely."),
            "password_reset": (
                "Reset your password",
                "Use this code to continue your password reset.",
            ),
            "signup": (
                "Verify your email",
                "Use this code to finish creating your HilalMarkets account.",
            ),
            "system_brain": (
                "System Brain access code",
                "Use this code to complete your protected administrator sign-in.",
            ),
        }
        title, introduction = labels.get(
            purpose,
            ("Your verification code", "Use this code to continue securely."),
        )
        subjects = {
            "login": "Your HilalMarkets login code",
            "password_reset": "Your HilalMarkets password reset code",
            "signup": "Verify your HilalMarkets email",
            "system_brain": "Your HilalMarkets System Brain access code",
        }
        subject = subjects.get(purpose, "Your HilalMarkets verification code")
        text_body = (
            f"{title}\n\n"
            f"{introduction}\n\n"
            f"Code: {code}\n\n"
            f"It expires in {ttl_minutes} minutes. If you did not request this code, "
            "you can safely ignore this email."
        )
        content = (
            f'<p style="{_P}">{html.escape(introduction)}</p>'
            '<div style="margin:26px 0;padding:22px;border:1px solid #d0d6de;'
            'border-radius:18px;background:#fafbfc;text-align:center">'
            '<div style="margin-bottom:8px;color:#7a8089;font-size:12px;'
            'letter-spacing:.08em;text-transform:uppercase">One-time code</div>'
            f'<div style="color:#2b2e35;font-family:Geometria,Onest,Arial,sans-serif;'
            f'font-size:34px;font-weight:700;letter-spacing:.18em">{html.escape(code)}</div>'
            "</div>"
            f'<p style="{_P}">This code expires in <strong>{ttl_minutes} minutes</strong>. '
            "If you did not request it, no action is needed.</p>"
            '<p style="margin:22px 0 0;color:#7a8089;font-size:12px;line-height:1.65">'
            f"Sent securely to {html.escape(_masked_email(recipient))}. "
            "HilalMarkets will never ask you to reply with this code.</p>"
        )
        return BrandedEmail(
            subject=subject,
            text_body=text_body,
            html_body=self.shell(
                title=title,
                preheader=f"Your code expires in {ttl_minutes} minutes.",
                content_html=content,
            ),
        )

    def access_changed(
        self,
        *,
        first_name: str,
        plan_name: str,
        duration_label: str,
        ends_at_label: str | None,
    ) -> BrandedEmail:
        subject = f"Your HilalMarkets access is now {plan_name}"
        greeting = f"Assalamu Alaikum {first_name or 'there'},"
        expiry_line = (
            f"Your access is scheduled through {ends_at_label}."
            if ends_at_label
            else "Your access has no scheduled end date."
        )
        text_body = (
            f"{greeting}\n\n"
            "Your HilalMarkets access has been updated by our team.\n\n"
            f"Access: {plan_name}\n"
            f"Duration: {duration_label}\n"
            f"{expiry_line}\n"
            "WhatsApp is not included in this access level.\n\n"
            "You can sign in now and continue using your available screening, Watchlist, "
            "market-check, evidence, and monitoring tools.\n\n"
            "Hilal Markets"
        )
        dashboard_url = f"{str(self.settings.public_base_url).rstrip('/')}/dashboard"
        rows = [
            ("Access", plan_name),
            ("Duration", duration_label),
            ("WhatsApp", "Not included"),
        ]
        if ends_at_label:
            rows.insert(2, ("Access through", ends_at_label))
        details = "".join(
            "<tr>"
            f'<td style="padding:11px 0;border-bottom:1px solid #e1e5ea;'
            f'color:#7a8089">{html.escape(label)}</td>'
            f'<td align="right" style="padding:11px 0;border-bottom:1px solid #e1e5ea;'
            f'color:#2b2e35;font-weight:700">{html.escape(value)}</td>'
            "</tr>"
            for label, value in rows
        )
        content = (
            f'<p style="{_P}"><strong>{html.escape(greeting)}</strong></p>'
            f'<p style="{_P}">Your HilalMarkets access has been updated by our team. '
            "The change is active now.</p>"
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="margin:24px 0;border-collapse:collapse">'
            f"{details}</table>"
            f'<p style="{_P}">{html.escape(expiry_line)}</p>'
            f'<p style="margin:26px 0 10px"><a href="{html.escape(dashboard_url, quote=True)}" '
            'style="display:inline-block;padding:13px 21px;border-radius:999px;'
            'background:#cbfa4d;color:#2b2e35;text-decoration:none;font-weight:700">'
            "Open HilalMarkets</a></p>"
            '<p style="margin:20px 0 0;color:#7a8089;font-size:12px;line-height:1.65">'
            "This access update does not authorize trade execution. HilalMarkets remains "
            "a screening, evidence, and monitoring platform.</p>"
        )
        return BrandedEmail(
            subject=subject,
            text_body=text_body,
            html_body=self.shell(
                title=f"{plan_name} is active",
                preheader=f"Your HilalMarkets access is now {plan_name}.",
                content_html=content,
            ),
        )

    def shell(
        self,
        *,
        title: str,
        preheader: str,
        content_html: str,
    ) -> str:
        legal_name = html.escape(self.settings.site_legal_name or "HilalMarkets")
        base_url = str(self.settings.public_base_url).rstrip("/")
        return (
            "<!doctype html>"
            '<html lang="en" data-hm-email-shell="true"><head>'
            '<meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title></head>"
            '<body style="margin:0;background:#f5f8fb;color:#2b2e35;'
            'font-family:Onest,Arial,sans-serif">'
            f'<div style="display:none;max-height:0;overflow:hidden">{html.escape(preheader)}</div>'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="width:100%;background:#f5f8fb;padding:28px 12px"><tr><td align="center">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="width:100%;max-width:640px;overflow:hidden;border:1px solid #e1e5ea;'
            'border-radius:24px;background:#ffffff">'
            '<tr><td style="padding:25px 30px;background:#2b2e35;color:#ffffff">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
            '<td><div style="font-family:Geometria,Onest,Arial,sans-serif;font-size:18px;'
            'font-weight:700;letter-spacing:-.02em">hilal markets</div>'
            '<div style="margin-top:5px;color:#cdd2d8;font-size:11px">'
            "Evidence-led crypto monitoring</div></td>"
            '<td align="right"><span style="display:inline-block;width:13px;height:13px;'
            'border-radius:50%;background:#cbfa4d"></span></td></tr></table>'
            f'<h1 style="margin:24px 0 0;color:#ffffff;font-family:Geometria,Onest,Arial,'
            f'sans-serif;font-size:28px;line-height:1.18">{html.escape(title)}</h1>'
            "</td></tr>"
            f'<tr><td style="padding:30px">{content_html}</td></tr>'
            '<tr><td style="padding:20px 30px;border-top:1px solid #e1e5ea;'
            'background:#fafbfc;color:#7a8089;font-size:11px;line-height:1.65">'
            "HilalMarkets supports research, screening, and monitoring. It does not execute "
            "trades, hold funds, promise returns, or make personal religious rulings.<br>"
            f'<a href="{html.escape(base_url, quote=True)}" style="color:#55712a;'
            f'text-decoration:none">{legal_name}</a>'
            "</td></tr></table></td></tr></table></body></html>"
        )


_P = "margin:0 0 16px;color:#50555e;font-size:15px;line-height:1.7"


def _masked_email(value: str) -> str:
    local, separator, domain = value.partition("@")
    if not separator:
        return "your email address"
    visible = local[:1]
    return f"{visible}{'*' * max(2, min(len(local) - 1, 6))}@{domain}"


def safe_email_payload(value: Any) -> str:
    return html.escape(str(value or ""))
