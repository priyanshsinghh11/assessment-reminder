"""
Candidate unsubscribes: the token, the suppression list, and the check.

WHY A WHOLE MODULE FOR ONE HEADER. `List-Unsubscribe` is not a courtesy line in
a footer -- it is the header Gmail, Yahoo and Outlook read to decide whether we
are a sender worth delivering. Bulk mail that arrives without it is scored
down, and the reminder run is the most bulk-shaped thing this system does: a
few hundred near-identical messages to people who did not ask for a second one.

But a header is only half of it, and the half that is theatre on its own. An
unsubscribe link that records nothing means the next run mails that person
again, which is worse than never offering it: they asked, we said yes, and then
we did it anyway. So this module owns three things that have to agree --

    token_for() / email_for()   the link in the mail, and reading it back
    suppress()                  what happens when somebody uses it
    is_suppressed()             the check every candidate send makes first

-- and the send paths call the third one. A suppression that is recorded and
never consulted is the same failure with more steps.

THE TOKEN IS SIGNED, NOT STORED. "<base64url(email)>.<hmac>" verifies against a
server-side secret, so a link stays valid as long as the secret does, with no
row minted per candidate per send and nothing to expire. It also means the
endpoint learns the address from the token itself rather than looking it up, so
a forged or truncated token resolves to nothing rather than to somebody else.
The address is readable by whoever holds the link -- which is the person whose
address it is, in their own inbox -- and the signature is what stops it being
edited into a neighbour's.

NOT APPLIED TO THE HIRING MANAGER'S SHORTLIST. That is internal mail to a
colleague about work they own, not bulk mail to the public, and an unsubscribe
on it would silently switch off a hand-off somebody is waiting for.
"""

import base64
import hashlib
import hmac
import logging
import re
from datetime import datetime, timezone

from backend.config import PUBLIC_BASE_URL, UNSUBSCRIBE_MAILTO
from backend.db import store

log = logging.getLogger(__name__)

_SEPARATOR = "."


def _secret() -> bytes:
    """The signing key, minted once into Mongo and reused."""
    return store.get_app_secret().encode("utf-8")


def _clean(email: str) -> str:
    return (email or "").strip().lower()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(address: str) -> str:
    digest = hmac.new(_secret(), address.encode("utf-8"),
                      hashlib.sha256).digest()
    # 128 bits of signature, far past what forging one would be worth: the
    # prize is unsubscribing somebody else from a recruitment reminder.
    return _b64(digest[:16])


def token_for(email: str) -> str:
    """The opaque, signed token identifying one address in a link."""
    address = _clean(email)
    return f"{_b64(address.encode('utf-8'))}{_SEPARATOR}{_sign(address)}"


def email_for(token: str) -> str | None:
    """
    The address a token names, or None if it does not verify.

    hmac.compare_digest rather than `==`, so a wrong signature takes the same
    time to reject however much of it happened to be right.
    """
    raw = (token or "").strip()
    if raw.count(_SEPARATOR) != 1:
        return None
    body, signature = raw.split(_SEPARATOR)
    try:
        address = _unb64(body).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if not address:
        return None
    if not hmac.compare_digest(signature, _sign(address)):
        return None
    return address


def unsubscribe_url(email: str) -> str:
    """The https link that goes in the header and the footer."""
    return f"{PUBLIC_BASE_URL}/unsubscribe/{token_for(email)}"


def suppress(email: str, source: str = "link", note: str = "") -> bool:
    """
    Record that this address wants no more candidate mail. Idempotent.

    Returns True only if this call is what added them -- so a mail client that
    fetches the one-click URL twice does not read as two separate decisions.
    """
    address = _clean(email)
    if not address:
        return False
    result = store.get_db().unsubscribes.update_one(
        {"_id": address},
        {"$setOnInsert": {
            "unsubscribed_at": datetime.now(timezone.utc),
            "source": source,
            "note": note,
        }},
        upsert=True,
    )
    added = result.upserted_id is not None
    if added:
        log.info("Unsubscribed %s (%s)", address, source)
    return added


def is_suppressed(email: str) -> bool:
    """
    Has this address opted out of candidate mail?

    FAILS CLOSED, unlike every other database read here. Elsewhere the cost of
    an unreachable Mongo is a page that does not draw, so those reads fail
    open. The cost here is mailing somebody who explicitly asked us not to,
    which is the one error that cannot be taken back -- so a database we cannot
    read stops the send rather than waving it through.
    """
    address = _clean(email)
    if not address:
        return False
    try:
        return store.get_db().unsubscribes.find_one(
            {"_id": address}, {"_id": 1}) is not None
    except Exception as exc:
        log.error("Cannot read the unsubscribe list (%s). Treating %s as "
                  "unsubscribed rather than risk mailing an opt-out.",
                  exc, address)
        return True


def suppressed_among(emails) -> set[str]:
    """
    Which of these addresses have opted out. One query, not one per candidate.

    For the reminder run, which decides about a few hundred people at once and
    would otherwise make a round trip for each. Same fail-closed rule: if the
    list cannot be read, every address is treated as suppressed.
    """
    wanted = {_clean(e) for e in emails if _clean(e)}
    if not wanted:
        return set()
    try:
        rows = store.get_db().unsubscribes.find(
            {"_id": {"$in": sorted(wanted)}}, {"_id": 1})
        return {row["_id"] for row in rows}
    except Exception as exc:
        log.error("Cannot read the unsubscribe list (%s). Treating all %d "
                  "addresses as unsubscribed rather than risk an opt-out.",
                  exc, len(wanted))
        return set(wanted)


def mail_headers(email: str) -> dict:
    """
    The headers that make an unsubscribe machine-readable.

    List-Unsubscribe carries both a URL and a mailto:. Clients prefer the URL
    and fall back to the address, and the address is what still works when this
    server is unreachable -- a reply somebody reads beats a link that 502s.

    List-Unsubscribe-Post is what makes it ONE CLICK. Without it, a client that
    offers an unsubscribe button has to send the reader to a web page and hope;
    with it, the client POSTs the URL itself and the reader is done inside their
    inbox. Gmail and Yahoo both look for this pair on bulk mail.

    The URL half is only ever offered over https. A one-click endpoint on plain
    http is an unsubscribe anyone on the path can read or forge, and the link
    would be built from a loopback PUBLIC_BASE_URL that opens on nobody's
    machine but ours.
    """
    over_https = PUBLIC_BASE_URL.startswith("https://")

    targets = []
    if over_https:
        targets.append(f"<{unsubscribe_url(email)}>")
    if UNSUBSCRIBE_MAILTO:
        targets.append(f"<mailto:{UNSUBSCRIBE_MAILTO}?subject=unsubscribe>")

    if not targets:
        # Said out loud rather than quietly shipping bulk mail with no way out.
        log.warning(
            "No List-Unsubscribe header on this send: PUBLIC_BASE_URL is not "
            "https and UNSUBSCRIBE_MAILTO is unset. Bulk mail without one is "
            "scored down by Gmail and Yahoo, and the candidate has no way to "
            "stop it.")
        return {}

    headers = {"List-Unsubscribe": ", ".join(targets)}
    if over_https:
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    return headers


_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def looks_like_email(value: str) -> bool:
    return bool(_ADDRESS.match(_clean(value)))
