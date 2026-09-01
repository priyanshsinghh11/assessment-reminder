"""
Brevo (Sendinblue) transactional email client.

Two kinds of mail go out of here: the candidate-facing assessment reminder,
and the internal shortlist that goes to a hiring manager. send_email() is the
one transport both sit on, so a change to sender, timeout or error reporting
lands on every message rather than half of them.
"""

import base64
import logging
import requests

from backend.core.config import (BREVO_API_KEY, BREVO_SENDER_NAME, BREVO_SENDER_EMAIL,
                    EMAIL_LOGO_URL, PUBLIC_BASE_URL, UNSUBSCRIBE_MAILTO)
from backend.notifications import unsubscribe
from backend.notifications.text import first_name as _first_name

log = logging.getLogger(__name__)

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"

NAVY = "#001d6b"


def header_html(width: int = 600) -> str:
    """
    The navy bar every outgoing message opens with, carrying the wordmark.

    Shared rather than copied into each template, because the three mails a
    candidate or a manager receives -- reminder, invitation, shortlist -- are
    meant to look like one sender, and a logo swapped in two of three places is
    exactly how that stops being true.

    Written as a table with inline attributes and not a styled div: Outlook
    renders this, and its HTML engine is Word's. `alt` carries the brand in
    words for the ordinary case where a mail client blocks remote images until
    the reader trusts the sender -- the first message from us always is that
    case -- and the alt text is styled white so a blocked logo reads as the
    heading it replaced rather than as a fault.
    """
    mark = (f'<img src="{EMAIL_LOGO_URL}" alt="Ajaia" width="104" height="25" '
            'style="display:block;border:0;outline:none;text-decoration:none;'
            'height:25px;width:104px;color:#ffffff;'
            "font-family:'Poppins',Arial,sans-serif;font-size:20px;"
            'font-weight:600;letter-spacing:-0.01em;">')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%" style="max-width:{width}px;background:{NAVY};'
            'border-radius:8px 8px 0 0;">'
            '<tr><td style="padding:24px;">' + mark + '</td></tr></table>')


class BrevoError(RuntimeError):
    """A send that failed, carrying Brevo's own words about why."""


def send_email(
    to: list[dict],
    subject: str,
    html: str,
    text: str = "",
    attachments: list[tuple[str, bytes]] | None = None,
    reply_to: str | None = None,
    headers: dict | None = None,
) -> dict:
    """
    Send one message. `to` is [{"email": ..., "name": ...}, ...].

    Raises BrevoError rather than returning False, which is the difference
    between this and send_reminder_email() below: a reminder that fails is one
    of a hundred in a loop that has to keep going, whereas a shortlist send is
    a single deliberate click whose failure the recruiter needs to read.

    `attachments` are (filename, bytes) pairs, base64'd into the payload --
    Brevo's `content` field wants a base64 string, not raw bytes, and a
    spreadsheet posted raw comes back as a 400 with no useful message.
    """
    if not BREVO_API_KEY:
        raise BrevoError("BREVO_API_KEY is not set, so nothing can be sent.")
    if not to:
        raise BrevoError("No recipients.")

    payload = {
        "sender": {"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
        "to": to,
        "subject": subject,
        "htmlContent": html,
    }
    if text:
        payload["textContent"] = text
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    if headers:
        # Brevo passes these straight through to the message. This is how
        # List-Unsubscribe gets onto candidate mail -- see unsubscribe.py.
        payload["headers"] = dict(headers)
    if attachments:
        payload["attachment"] = [
            {"name": name, "content": base64.b64encode(blob).decode("ascii")}
            for name, blob in attachments
        ]

    try:
        resp = requests.post(
            BREVO_SEND_URL,
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        detail = ""
        if exc.response is not None:
            detail = f" -- {exc.response.status_code}: {exc.response.text[:300]}"
        raise BrevoError(f"Brevo send failed: {exc}{detail}") from exc

    log.info("Sent %r to %s", subject, ", ".join(r["email"] for r in to))
    try:
        return resp.json()
    except ValueError:
        return {}


def build_reminder_email(
    to_name: str,
    role_title: str,
    assessment_url: str,
    reminder_number: int = 1,
    to_email: str = "",
) -> dict:
    """
    Render the reminder without sending it.

    Both the real send and the dashboard's terminal preview go through here,
    so what you preview is byte-for-byte what would be delivered.
    """
    first = _first_name(to_name)

    subject = f"Quick follow-up: your {role_title} assessment"
    if reminder_number > 1:
        subject = f"Last check-in: your {role_title} assessment"

    return {
        "subject": subject,
        "html": _build_html(first, role_title, assessment_url, to_email),
        # A plain-text alternative alongside the HTML. Multipart mail is less
        # likely to be filtered as spam, which matters when sending in bulk.
        "text": _build_text(first, role_title, assessment_url, to_email),
    }


def send_reminder_email(
    to_email: str,
    to_name: str,
    role_title: str,
    assessment_url: str,
    reminder_number: int = 1,
) -> bool:
    """
    Send a reminder email through Brevo.
    Returns True on success, False on failure.

    THE LAST GATE BEFORE A CANDIDATE IS MAILED. send_batch() already filters
    the opt-out list in bulk before it gets here, and this checks again anyway:
    a run can be minutes long, the CLI and the dashboard both reach this
    function, and somebody who unsubscribes while a batch is in flight must not
    be mailed by the tail of it. One extra lookup per send is nothing against
    mailing a person who asked us to stop.
    """
    if unsubscribe.is_suppressed(to_email):
        log.info("Skipping %s: unsubscribed.", to_email)
        return False

    email = build_reminder_email(to_name, role_title, assessment_url,
                                 reminder_number, to_email=to_email)

    payload = {
        "sender": {"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
        "to": [{"email": to_email, "name": to_name}],
        "subject": email["subject"],
        "htmlContent": email["html"],
        "textContent": email["text"],
    }
    unsub = unsubscribe.mail_headers(to_email)
    if unsub:
        payload["headers"] = unsub

    headers = {
        "api-key": BREVO_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        resp = requests.post(
            BREVO_SEND_URL, headers=headers, json=payload, timeout=15
        )
        resp.raise_for_status()
        log.info("Reminder %d sent to %s for %s", reminder_number, to_email, role_title)
        return True
    except requests.RequestException as exc:
        detail = ""
        if exc.response is not None:
            detail = f" -- {exc.response.status_code}: {exc.response.text[:200]}"
        log.error("Brevo send failed for %s: %s%s", to_email, exc, detail)
        return False


# ---------------------------------------------------------------------------
# Email content
# ---------------------------------------------------------------------------

def _unsubscribe_footer_text(to_email: str) -> str:
    """
    The line a person can act on, as opposed to the header a client reads.

    Both are needed and they are not substitutes. List-Unsubscribe is invisible
    to the reader and only some clients surface it as a button; a reader who
    wants out and cannot find one replies angrily or reports the mail as spam,
    and the second of those costs the sending domain far more than the
    unsubscribe would have.
    """
    if not to_email:
        return ""
    url = unsubscribe.unsubscribe_url(to_email)
    if not PUBLIC_BASE_URL.startswith("https://"):
        # A loopback or plain-http base URL produces a link that opens on
        # nobody's machine but ours. Offer the mailbox instead of a dead link.
        if UNSUBSCRIBE_MAILTO:
            return ("\n\nDon't want these? Reply to this email, or write to "
                    f"{UNSUBSCRIBE_MAILTO} and we'll stop.\n")
        return ""
    return (f"\n\nDon't want these? Unsubscribe here and we'll stop:\n{url}\n")


def _unsubscribe_footer_html(to_email: str) -> str:
    """The same line, in the HTML part."""
    if not to_email:
        return ""
    if not PUBLIC_BASE_URL.startswith("https://"):
        if not UNSUBSCRIBE_MAILTO:
            return ""
        target = f"mailto:{UNSUBSCRIBE_MAILTO}?subject=unsubscribe"
        label = "let us know"
    else:
        target = unsubscribe.unsubscribe_url(to_email)
        label = "unsubscribe"
    return (
        '<p style="margin: 24px 0 0; padding-top: 16px; '
        'border-top: 1px solid #e4e7ec; font-size: 12px; color: #667085;">'
        "Don&rsquo;t want these? "
        f'<a href="{target}" '
        'style="color: #667085; text-decoration: underline;">'
        f"{label}</a> and we&rsquo;ll stop.</p>"
    )


def _build_text(name: str, role_title: str, assessment_url: str,
                to_email: str = "") -> str:
    """Plain-text version, sent as the multipart alternative."""
    return f"""Hi {name},

Just a quick follow-up. We sent over the assessment for the {role_title} role a few days ago and wanted to make sure it didn't get buried in your inbox.

You can start it anytime here:
{assessment_url}

It's a timed take-home, so it won't begin until you're ready. If you have any questions or need more time, just reply to this email.

Looking forward to seeing your work.

Best,
Ajaia Hiring Team
""" + _unsubscribe_footer_text(to_email)


def _build_html(name: str, role_title: str, assessment_url: str,
                to_email: str = "") -> str:
    """HTML version of the same copy, in Ajaia's colours."""
    return f"""
    <div style="font-family: 'Poppins', Arial, sans-serif; max-width: 600px;
                margin: 0 auto; padding: 32px; color: #1b1c1c;
                font-size: 15px; line-height: 1.6;">

        {header_html()}

        <div style="padding: 32px; border: 1px solid #e4e7ec;
                    border-top: none; border-radius: 0 0 8px 8px;">

            <p style="margin: 0 0 16px;">Hi {name},</p>

            <p style="margin: 0 0 16px;">
                Just a quick follow-up. We sent over the assessment for the
                <strong>{role_title}</strong> role a few days ago and wanted to
                make sure it didn't get buried in your inbox.
            </p>

            <p style="margin: 0 0 8px;">You can start it anytime here:</p>

            <p style="margin: 0 0 16px;">
                <a href="{assessment_url}"
                   style="color: #0b2e8e; font-weight: 600; word-break: break-all;">
                    {assessment_url}
                </a>
            </p>

            <p style="margin: 0 0 16px;">
                It's a timed take-home, so it won't begin until you're ready.
                If you have any questions or need more time, just reply to
                this email.
            </p>

            <p style="margin: 0 0 24px;">Looking forward to seeing your work.</p>

            <p style="margin: 0;">
                Best,<br>
                Ajaia Hiring Team
            </p>

            {_unsubscribe_footer_html(to_email)}

        </div>
    </div>
    """
