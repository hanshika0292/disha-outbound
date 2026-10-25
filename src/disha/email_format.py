"""Email rendering: turn a drafted plain-text body into a clean HTML email
with a consistent, branded signature.

Why this exists
───────────────
Drafts are stored as well-formatted plain text (greeting, blank-line-separated
paragraphs, a sign-off). Sent as `text/plain`, Gmail collapses the line breaks
into a run-on blob. Sending `multipart/alternative` (plain + HTML) makes the
paragraph structure explicit so it renders correctly everywhere, and lets us
attach a real signature block (name, credentials, LinkedIn, booking link).

The signature is built deterministically from tenant settings — never from the
LLM — so branding is always present and correct. The model's own trailing
sign-off ("Best,\\nMeera") is stripped first so we don't double-sign.
"""

from __future__ import annotations

import html
import re

# Closing words that mark the model's sign-off line (case-insensitive).
_SIGNOFF_RE = re.compile(
    r"^\s*(best|best regards|regards|warm regards|warmly|thanks|thank you|"
    r"cheers|sincerely|kind regards|all the best|talk soon|speak soon)\b[,.!]?\s*$",
    re.IGNORECASE,
)


def strip_model_signoff(body: str) -> str:
    """Remove a trailing sign-off block (closing word + following name lines).

    Scans the last few non-empty lines for a closing token; if found, cuts it
    and everything after. Leaves the body untouched if no clear sign-off exists.
    """
    lines = body.rstrip().split("\n")
    # Walk back over trailing blank lines.
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    # Look for a closing line within the last 4 lines of real content.
    window_start = max(0, end - 4)
    for i in range(window_start, end):
        if _SIGNOFF_RE.match(lines[i]):
            # Cut from this closing line onward (drops "Best,\nMeera").
            trimmed = "\n".join(lines[:i]).rstrip()
            return trimmed
    return body.rstrip()


def build_signature(settings: dict, *, from_email: str | None = None) -> tuple[str, str]:
    """Return (plain_text, html) signature from tenant settings.

    Recognised keys: sender_name, sender_title, sender_credentials,
    linkedin_url, cal_link.
    """
    name = (settings.get("sender_name") or "").strip()
    title = (settings.get("sender_title") or "").strip()
    creds = (settings.get("sender_credentials") or "").strip()
    linkedin = (settings.get("linkedin_url") or "").strip()
    cal = (settings.get("cal_link") or "").strip()

    # Signature subtitle prefers the explicit credentials line; falls back to
    # the sender title (which also feeds the email-body persona).
    subtitle = creds or title
    href = ""
    if linkedin:
        href = linkedin if linkedin.startswith("http") else f"https://{linkedin}"

    # ── plain text ── (links can't be inline, so the LinkedIn URL sits under
    # the name and the booking link gets a label).
    pt_lines = ["Best,"]
    if name:
        pt_lines.append(name)
    if subtitle:
        pt_lines.append(subtitle)
    link_bits = []
    if href:
        link_bits.append(f"LinkedIn Profile: {href}")
    if cal:
        link_bits.append(f"Book a call: {cal}")
    if link_bits:
        pt_lines.append(" · ".join(link_bits))
    plain = "\n".join(pt_lines)

    # ── html ── (the name itself is the LinkedIn hyperlink).
    grey = "#6b7280"
    ink = "#18181b"
    parts = [
        '<div style="margin-top:18px;font-family:-apple-system,Segoe UI,Roboto,'
        'Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5;color:#374151">',
        '<div>Best,</div>',
        '<div style="border-top:1px solid #e5e7eb;margin-top:10px;'
        'padding-top:10px;display:inline-block;min-width:220px">',
    ]
    if name:
        name_html = html.escape(name)
        if href:
            name_html = (
                f'<a href="{html.escape(href, quote=True)}" '
                f'style="color:{ink};text-decoration:none;font-weight:600">'
                f"{name_html}</a>"
            )
        else:
            name_html = f'<span style="font-weight:600;color:{ink}">{name_html}</span>'
        parts.append(f"<div>{name_html}</div>")
    if subtitle:
        parts.append(
            f'<div style="color:{grey};font-size:13px;margin-top:2px">'
            f"{html.escape(subtitle)}</div>"
        )
    # Visible link row — underlined so it reads as clickable (the name link
    # alone wasn't obviously a link). LinkedIn shows the profile path as text.
    link_color = "#185fa5"
    link_html = []
    if href:
        link_html.append(
            f'<a href="{html.escape(href, quote=True)}" '
            f'style="color:{link_color};text-decoration:underline">LinkedIn Profile</a>'
        )
    if cal:
        link_html.append(
            f'<a href="{html.escape(cal, quote=True)}" '
            f'style="color:{link_color};text-decoration:underline">Book a call</a>'
        )
    if link_html:
        sep = f'<span style="color:{grey}"> · </span>'
        parts.append(
            f'<div style="font-size:13px;margin-top:5px">{sep.join(link_html)}</div>'
        )
    parts.append("</div></div>")
    return plain, "".join(parts)


def _body_to_html(body: str) -> str:
    """Plain-text body → HTML: blank lines become paragraphs, single newlines
    become <br>. Escapes HTML special chars."""
    paragraphs = re.split(r"\n\s*\n", body.strip())
    out = []
    for para in paragraphs:
        if not para.strip():
            continue
        safe = html.escape(para.strip()).replace("\n", "<br>")
        out.append(
            f'<p style="margin:0 0 14px 0;font-family:-apple-system,Segoe UI,'
            f"Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;"
            f'color:#1f2937">{safe}</p>'
        )
    return "\n".join(out)


def render_email(
    body: str, settings: dict, *, from_email: str | None = None
) -> tuple[str, str]:
    """Render a drafted body into (plain_text, html) for sending.

    - strips the model's sign-off
    - appends the branded signature
    - HTML version has real paragraphs + signature block
    """
    core = strip_model_signoff(body)
    sig_plain, sig_html = build_signature(settings, from_email=from_email)

    plain = f"{core}\n\n{sig_plain}"
    html_doc = (
        '<div style="max-width:560px">'
        f"{_body_to_html(core)}\n{sig_html}"
        "</div>"
    )
    return plain, html_doc
