"""Welcome email for newly provisioned accounts.

Sent from the platform operator's connected Gmail (the admin's tenant) to the
new user, with their login URL, credentials, and a short getting-started guide.
Failures never block account creation — the caller treats this as best-effort.
"""

from __future__ import annotations

import html as _html
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_STEPS = [
    ("Connect your Gmail", "So Disha can send on your behalf and capture replies."),
    ("Set up your profile", "Add your name, title and who you want to reach (your ICP)."),
    ("Find leads", "Source a batch — Disha enriches, scores and drafts outreach."),
    ("Let it run", "Approved emails send automatically; you just handle the replies."),
]


def _render(*, name: str | None, login_url: str, email: str, password: str | None) -> tuple[str, str]:
    """Return (plain, html) for the welcome email."""
    greeting_name = (name or "there").split(" ")[0]

    # ── plain text ──
    lines = [
        f"Hi {greeting_name},",
        "",
        "Your Disha account is ready. Disha automates your outbound — it finds "
        "leads, writes and sends personalised emails, and captures replies so "
        "you can focus on the conversations that matter.",
        "",
        "Your login details:",
        f"  Login:    {login_url}",
        f"  Email:    {email}",
    ]
    if password:
        lines.append(f"  Password: {password}  (you'll set your own on first login)")
    lines += ["", "Getting started:"]
    for i, (title, desc) in enumerate(_STEPS, 1):
        lines.append(f"  {i}. {title} — {desc}")
    lines += [
        "",
        "Just reply to this email if you have any questions — happy to help.",
        "",
        "Welcome aboard,",
        "The Disha team",
    ]
    plain = "\n".join(lines)

    # ── html ──
    ink = "#18181b"
    grey = "#6b7280"
    border = "#e5e7eb"
    rows = "".join(
        f'<tr>'
        f'<td style="padding:3px 12px 3px 0;color:{grey};font-size:13px;white-space:nowrap">{label}</td>'
        f'<td style="padding:3px 0;color:{ink};font-size:14px;font-family:ui-monospace,Menlo,monospace">{_html.escape(value)}</td>'
        f"</tr>"
        for label, value in (
            ("Login", login_url),
            ("Email", email),
            *((("Password", password),) if password else ()),
        )
    )
    steps_html = "".join(
        f'<tr>'
        f'<td style="padding:8px 12px 8px 0;vertical-align:top;color:{ink};font-weight:600;font-size:14px">{i}</td>'
        f'<td style="padding:8px 0;font-size:14px;line-height:1.5">'
        f'<span style="color:{ink};font-weight:600">{_html.escape(title)}</span><br>'
        f'<span style="color:{grey}">{_html.escape(desc)}</span></td>'
        f"</tr>"
        for i, (title, desc) in enumerate(_STEPS, 1)
    )
    html_doc = (
        '<div style="max-width:560px;font-family:-apple-system,Segoe UI,Roboto,'
        'Helvetica,Arial,sans-serif;color:#1f2937">'
        f'<p style="font-size:15px;line-height:1.6;margin:0 0 14px">Hi {_html.escape(greeting_name)},</p>'
        '<p style="font-size:15px;line-height:1.6;margin:0 0 18px">Your Disha account is '
        "ready. Disha automates your outbound — it finds leads, writes and sends "
        "personalised emails, and captures replies so you can focus on the "
        "conversations that matter.</p>"
        f'<a href="{_html.escape(login_url, quote=True)}" style="display:inline-block;'
        f'background:{ink};color:#ffffff;text-decoration:none;font-size:14px;'
        'font-weight:600;padding:10px 20px;border-radius:6px;margin:0 0 18px">'
        "Log in to Disha</a>"
        f'<div style="border:1px solid {border};border-radius:8px;padding:14px 16px;'
        'margin:0 0 20px">'
        f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:.04em;'
        f'color:{grey};margin-bottom:8px">Your login details</div>'
        f'<table style="border-collapse:collapse">{rows}</table>'
        + (
            f'<div style="font-size:12px;color:{grey};margin-top:10px">'
            "You'll be asked to choose your own password the first time you log in.</div>"
            if password
            else ""
        )
        + "</div>"
        f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:.04em;'
        f'color:{grey};margin:0 0 6px">Getting started</div>'
        f'<table style="border-collapse:collapse;margin:0 0 20px">{steps_html}</table>'
        '<p style="font-size:14px;line-height:1.6;color:#374151;margin:0 0 18px">'
        "Just reply to this email if you have any questions — happy to help.</p>"
        f'<div style="border-top:1px solid {border};padding-top:12px;font-size:14px;color:{grey}">'
        f'Welcome aboard,<br><span style="color:{ink};font-weight:600">The Disha team</span></div>'
        "</div>"
    )
    return plain, html_doc


async def send_welcome_email(
    session: AsyncSession,
    from_tenant_id: UUID,
    *,
    to_email: str,
    login_url: str,
    name: str | None = None,
    password: str | None = None,
) -> bool:
    """Best-effort: send the welcome email from the operator's Gmail.

    Returns True if sent, False if it couldn't (no Gmail connected, send error).
    Never raises — account creation must succeed even if the email doesn't.
    """
    try:
        from disha.adapters.gmail import GmailAdapter

        plain, html_body = _render(
            name=name, login_url=login_url, email=to_email, password=password
        )
        await GmailAdapter().send_raw(
            session,
            from_tenant_id,
            to_addr=to_email,
            subject="Welcome to Disha — your account is ready",
            body=plain,
            html_body=html_body,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — never block account creation
        logger.warning("welcome email to %s failed: %s", to_email, exc)
        return False
