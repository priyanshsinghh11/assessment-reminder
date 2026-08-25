"""
Bulk candidate rejections: the paste box, the ledger, and the send.

THE PROBLEM THIS EXISTS FOR. Several hundred people a round are turned down
before they ever sit an assessment -- at the CV screen, or inside Workable --
and until now they were told by hand: copy four hundred addresses into BCC,
write the message, hope. That works exactly once. The second round nobody can
say who was already written to, so somebody gets two rejections and somebody
else gets none, and neither is recoverable.

So there are two halves here and both are needed:

    RECORD    somebody was rejected outside this system. Write them down, so
              the next send skips them. Nothing is emailed.
    SEND      write the message here, read it, and send it -- one personalised
              message per candidate, recorded as it goes.

They meet at store's rejection ledger, which is the single answer to "have we
already told this person no". Recording and sending both write to it, the send
reads it before every message, and the board's own rejection (candidate_mail)
reads and writes it too -- so a candidate rejected on the pipeline in March is
not rejected again by a paste in August.

ONE MESSAGE PER PERSON, NOT ONE BCC. It costs four hundred API calls instead of
one, and it is worth every one of them: a BCC carries no name, no role and no
unsubscribe link the recipient can use without exposing the other 399, and a
single message with four hundred hidden recipients is scored as bulk by every
filter that sees it. Personalised sends are also the only ones that can be
recorded individually, which is the whole point.

NOTHING IN HERE SENDS ON ITS OWN. send_bulk() is reached from one place: the
Send button on the Rejections page, pressed by a recruiter who has just read
the preview of the exact message.
"""

import logging
import re
import time

from backend.core.config import (
    BREVO_SENDER_NAME,
    CANDIDATE_REPLY_TO,
    PIPELINE_EMAILS_ENABLED,
    REJECTION_ABORT_AFTER,
    REJECTION_MAX_PER_SEND,
    REJECTION_SEND_DELAY,
)
from backend.database import mongo_store as store
from backend.notifications import brevo_client, candidate_mail, unsubscribe

log = logging.getLogger(__name__)

MUTED = "#5b6270"


class RejectionError(RuntimeError):
    """A batch that cannot be built or started, with a reason worth showing."""


# ---------------------------------------------------------------------------
# Reading a pasted list
# ---------------------------------------------------------------------------
#
# WHAT ACTUALLY GETS PASTED IN HERE. Not a clean list of addresses. It is
# whatever came out of the last thing the recruiter had open: a BCC field
# ("Asha Menon <asha@x.com>; ravi@y.io; ..."), a column dragged out of a
# spreadsheet (one per line), a Workable export (name and address separated by
# a comma or a tab), or all three stuck together. Refusing anything but a
# comma-separated list would send them back to a text editor to clean it up by
# hand, which is the chore this page is replacing.
#
# So the parser is deliberately permissive about the shape and strict about the
# one thing that matters -- whether each piece contains a real address -- and
# hands back everything it could not read, so nothing is dropped silently.

# The address inside a line, wherever it sits: bare, inside angle brackets, or
# at the end of "Name, addr@host". Anchored to word-ish boundaries rather than
# to the whole string, because the whole string is usually not the address.
_ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Records are separated by newlines, semicolons or commas -- but a comma also
# separates a name from an address in a CSV row, so commas are only a separator
# when what follows still parses. Splitting on newlines and semicolons first
# and treating a comma as an in-record separator handles both without guessing.
_RECORD_SPLIT = re.compile(r"[\r\n;]+")


def parse_recipients(text: str) -> tuple[list[dict], list[str]]:
    """
    Read a pasted blob into [{"email", "name"}, ...] and a list of leftovers.

    De-duplicated on the address, keeping the FIRST occurrence -- and the first
    occurrence that carried a name wins over a bare repeat of the same address,
    so "Asha Menon <asha@x.com>" further down does not lose to a bare
    "asha@x.com" near the top.

    The second return value is every fragment that had no address in it. It is
    shown to the recruiter rather than discarded: a line that did not parse is
    usually a typo in somebody's address, and a person who therefore never
    hears from us at all.
    """
    entries: dict[str, dict] = {}
    unreadable: list[str] = []

    for chunk in _RECORD_SPLIT.split(str(text or "")):
        for record in _records(chunk):
            found = _ADDRESS.search(record)
            if not found:
                if record.strip():
                    unreadable.append(record.strip()[:120])
                continue
            address = store.clean_email(found.group(0))
            name = _name_beside(record, found.group(0))
            existing = entries.get(address)
            if existing is None:
                entries[address] = {"email": address, "name": name}
            elif name and not existing["name"]:
                existing["name"] = name

    return list(entries.values()), unreadable


def _records(chunk: str) -> list[str]:
    """
    One pasted line into the records it holds.

    A line with two addresses on it is two records, however they were
    separated; a line with one is one record whatever else is on it, so
    "Menon, Asha <asha@x.com>" keeps its comma and its name rather than being
    torn into a nameless address and an orphan word.
    """
    if len(_ADDRESS.findall(chunk)) <= 1:
        return [chunk]
    return re.split(r"[,\t]+", chunk)


def _name_beside(record: str, address: str) -> str:
    """
    The human name sharing a record with an address, or "".

    Everything that is not the address, stripped of the punctuation that held
    it -- angle brackets, quotes, the trailing comma of a CSV row. An address
    on its own yields "", and the mail then opens "Hi there", which is the
    honest greeting when we do not know.
    """
    rest = record.replace(address, " ")
    rest = re.sub(r"[<>\"',;\t]+", " ", rest)
    # A second address in the same record (a "reply-to" column, say) is not a
    # name, and neither is a bare word made only of punctuation.
    rest = _ADDRESS.sub(" ", rest)
    name = " ".join(rest.split())
    return name if re.search(r"[A-Za-z]", name) else ""


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------
#
# THE RECRUITER WRITES THIS ONE, unlike the board's rejection in
# candidate_mail, which is fixed company wording. The difference is who is
# being written to: that one goes to a named person a hiring manager has just
# read, this one goes to four hundred people at the top of a funnel, and the
# reason they did not go further ("we filled the role", "we are looking for
# more Django than your CV shows") changes every round.
#
# What they cannot touch is the shell -- the Ajaia header, the signature and
# the unsubscribe footer. Same boundary as the interview invitation, for the
# same reason: a manager editing raw HTML can delete the one line that makes
# bulk mail legal to send, and a rejection with no way out is the single worst
# message this system could put in four hundred inboxes.

PLACEHOLDERS = ("first_name", "name", "role")

DEFAULT_SUBJECT = "Your application to Ajaia"

DEFAULT_MESSAGE = "\n\n".join([
    "Hi {first_name},",
    "Thank you for applying to Ajaia and for the time you put into your "
    "application. We received a very large number of applications for this "
    "round.",
    "After reviewing everything, we have decided not to move forward with "
    "your application at this stage. This is not a judgement on your ability "
    "-- the pool was deep and the bar was specific to what we need right now.",
    "We will keep your details on file and will reach out if something opens "
    "up that fits you better.",
    "Wishing you the very best with your search.",
])


def placeholder_values(name: str, role_title: str = "") -> dict:
    """What {first_name} and friends resolve to for one candidate."""
    return {
        "first_name": candidate_mail.first_name_of(name),
        "name": str(name or "").strip() or "there",
        "role": str(role_title or "").strip() or "the role",
    }


def build_email(name: str, subject: str = "", message: str = "",
                role_title: str = "", to_email: str = "") -> dict:
    """
    Render one candidate's rejection. Returns {subject, html, text, message}.

    Pure: it takes plain values and gives back the three strings that would be
    sent, so the preview on the page is rendered by the function that sends and
    cannot disagree with it.

    `to_email` only reaches the unsubscribe footer. It is optional so a preview
    against a made-up recipient still renders -- the footer then carries no
    link, which is the honest thing to show for a message that is not going
    anywhere.
    """
    values = placeholder_values(name, role_title)
    written = str(message or "").strip() or DEFAULT_MESSAGE
    body_text = candidate_mail.fill(written, values)
    line = candidate_mail.fill(
        str(subject or "").strip() or DEFAULT_SUBJECT, values)

    body = (candidate_mail.paragraphs_html(body_text)
            + _signature_html()
            + _footer_html(to_email))

    lines = body_text.split("\n")
    lines += ["", "Best,", BREVO_SENDER_NAME, "Ajaia Hiring Team"]
    if to_email:
        lines += ["", "If you would rather not hear from us again: "
                  + unsubscribe.unsubscribe_url(to_email)]

    return {
        "subject": line,
        "html": candidate_mail.shell_html(body),
        "text": "\n".join(lines),
        # The recruiter's own words with the placeholders filled in, handed
        # back so the composer can show what one real candidate will read.
        "message": body_text,
    }


def _signature_html() -> str:
    """
    Signed by the hiring team, never by an individual.

    A rejection is the company's decision rather than one person's, and a
    candidate who wants to argue it should be writing to the team that made it
    -- not to whichever recruiter happened to click Send. Same reasoning as the
    board's rejection in candidate_mail, and deliberately the same sign-off, so
    the two never read as coming from different companies.
    """
    return f"""
        <p style="margin:0;">
          Best,<br>
          {candidate_mail.esc_html(BREVO_SENDER_NAME)}<br>
          <span style="color:{MUTED};font-size:13px;">Ajaia Hiring Team</span>
        </p>"""


def _footer_html(to_email: str) -> str:
    """
    The visible way out, under the signature.

    The List-Unsubscribe header does the real work for the clients that honour
    it; this is for the reader whose client does not, and it is not optional on
    a send this size. Omitted only when there is no recipient to build a link
    for, which is the preview.
    """
    if not to_email:
        return ""
    url = unsubscribe.unsubscribe_url(to_email)
    return f"""
        <p style="margin:28px 0 0;padding-top:16px;border-top:1px solid
                  {candidate_mail.LINE};color:{MUTED};font-size:12px;">
          You are receiving this because you applied to a role at Ajaia.
          <a href="{candidate_mail.esc_html(url)}" style="color:{MUTED};">
            Unsubscribe from future emails</a>.
        </p>"""


# ---------------------------------------------------------------------------
# The send
# ---------------------------------------------------------------------------

# What every recipient is decided to be, in the order the decisions are made.
# The order is the design: a person who has opted out is skipped before we ask
# whether they have already been rejected, because the answer to that does not
# change what we are allowed to do.
OUTCOMES = ("sent", "unsubscribed", "already", "failed")


def plan(entries: list[dict], resend: bool = False) -> dict:
    """
    Who this batch would actually mail, decided before a single message goes.

    Two bulk queries -- the opt-out list and the ledger -- rather than two
    lookups per candidate, and it answers the question the recruiter asks
    before pressing Send: "how many of these four hundred are new?"

    The send calls this too, so what the confirmation says and what the batch
    does are the same computation rather than two that agree today.
    """
    addresses = [store.clean_email(e.get("email")) for e in entries]
    opted_out = unsubscribe.suppressed_among(addresses)
    already = set() if resend else store.already_rejected(addresses)

    mailable, skipped = [], []
    for entry in entries:
        address = store.clean_email(entry.get("email"))
        if not address:
            continue
        if address in opted_out:
            skipped.append({**entry, "outcome": "unsubscribed",
                            "reason": "Asked not to be emailed."})
        elif address in already:
            skipped.append({**entry, "outcome": "already",
                            "reason": "Already told -- in the rejection ledger."})
        else:
            mailable.append(entry)

    return {"mailable": mailable, "skipped": skipped,
            "unsubscribed": sum(1 for s in skipped if s["outcome"] == "unsubscribed"),
            "already": sum(1 for s in skipped if s["outcome"] == "already")}


def send_bulk(entries: list[dict], subject: str = "", message: str = "",
              job_id=None, job_title: str = "", by: str = "",
              note: str = "", resend: bool = False,
              progress=None) -> dict:
    """
    Mail everyone in `entries` their rejection, recording each as it goes.

    Returns a totals dict. `progress(done, total, totals)` is called after
    every candidate so the page watching this can draw a bar -- a five-hundred
    message run takes minutes, and a spinner that says nothing for three of
    them is indistinguishable from one that has hung.

    RECORDED IMMEDIATELY, ONE AT A TIME, NOT BATCHED AT THE END. If the process
    dies at message 300, the 300 people who were mailed are already in the
    ledger and the re-run skips them. Writing the ledger in one go afterwards
    would mail 300 people and remember none of them, and the re-run would mail
    them all again.

    Failures do not stop the batch -- one bad address in a pasted list is
    ordinary -- but REJECTION_ABORT_AFTER consecutive ones do: that is not a
    bad address, that is the API key or the network, and discovering it 400
    messages later helps nobody.
    """
    if not PIPELINE_EMAILS_ENABLED:
        raise RejectionError("Candidate emails are switched off on this server "
                             "(PIPELINE_EMAILS_ENABLED=0).")
    if len(entries) > REJECTION_MAX_PER_SEND:
        raise RejectionError(
            f"That is {len(entries)} recipients, over the {REJECTION_MAX_PER_SEND} "
            f"cap for one send. Split it, or raise REJECTION_MAX_PER_SEND if "
            f"you meant it.")

    decided = plan(entries, resend=resend)
    queue = decided["mailable"]

    totals = {
        "considered": len(entries),
        "queued": len(queue),
        "sent": 0,
        "failed": 0,
        "unsubscribed": decided["unsubscribed"],
        "already": decided["already"],
    }
    results = [{"email": s["email"], "name": s.get("name") or "",
                "outcome": s["outcome"], "reason": s["reason"]}
               for s in decided["skipped"]]

    consecutive = 0
    for index, entry in enumerate(queue, start=1):
        address = store.clean_email(entry["email"])
        name = entry.get("name") or ""
        email = build_email(name, subject, message, job_title, to_email=address)

        try:
            brevo_client.send_email(
                to=[{"email": address, "name": name or address}],
                subject=email["subject"],
                html=email["html"],
                text=email["text"],
                reply_to=CANDIDATE_REPLY_TO,
                headers=unsubscribe.mail_headers(address) or None,
            )
        except brevo_client.BrevoError as exc:
            consecutive += 1
            totals["failed"] += 1
            results.append({"email": address, "name": name,
                            "outcome": "failed", "reason": str(exc)[:300]})
            _record(address, name, job_id, job_title, "failed", by, note,
                    email["subject"], str(exc))
            log.error("Rejection to %s failed: %s", address, exc)
            if consecutive >= REJECTION_ABORT_AFTER:
                totals["aborted"] = (
                    f"Stopped after {consecutive} failures in a row -- this is "
                    f"the mail service, not the addresses. "
                    f"{len(queue) - index} recipient(s) were not attempted.")
                log.error("Rejection batch aborted: %s", totals["aborted"])
                break
        else:
            consecutive = 0
            totals["sent"] += 1
            results.append({"email": address, "name": name, "outcome": "sent",
                            "reason": ""})
            _record(address, name, job_id, job_title, "sent", by, note,
                    email["subject"])

        if progress is not None:
            progress(index, len(queue), dict(totals))
        # Not after the last one: it would add a pause to the end of every
        # batch for nothing.
        if REJECTION_SEND_DELAY > 0 and index < len(queue):
            time.sleep(REJECTION_SEND_DELAY)

    totals["results"] = results
    log.info("Rejection batch: %d sent, %d failed, %d already told, "
             "%d unsubscribed.", totals["sent"], totals["failed"],
             totals["already"], totals["unsubscribed"])
    return totals


def _record(email: str, name: str, job_id, job_title: str, status: str,
            by: str, note: str, subject: str, error: str = "") -> None:
    """
    Write one outcome to the ledger. Never raises.

    The message has already left by the time this runs, so a database that
    refuses the write must not turn a delivered rejection into a reported
    failure. It is logged loudly instead: that person WILL be mailed again by
    the next batch, and the log line is the only warning of it.
    """
    try:
        store.record_rejection(email, name=name, job_id=job_id,
                               job_title=job_title, status=status,
                               source="bulk", by=by, subject=subject,
                               note=note, error=error)
    except Exception:
        log.exception("Could not write %s to the rejection ledger -- they may "
                      "be emailed a second time by the next batch.", email)
