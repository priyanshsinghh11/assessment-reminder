"""
Candidate-facing mail for a hiring-manager decision.

When a manager moves someone on the board, the candidate hears about it. Two
messages, and only two:

    interview   the invitation, carrying the manager's own cal.com link so the
                candidate books a slot that is actually free rather than
                starting a thread about times.
    rejected    the turn-down after a human has read them.

Nothing is sent for `hired`, or for a removal from the board. An offer is a
conversation a person has; a removal is usually a misclick being undone, and
mailing a candidate about our own correction is worse than saying nothing.

THE REJECTION HERE IS NOT THE MISSING-ARTEFACT REJECTION. That one goes to
someone whose submission had no CV attached and never reached a reviewer; this
one goes to someone a manager read and considered. Sending either copy to the
other candidate is the single worst mistake this module could make, which is
why they are separate templates in separate files rather than one with a flag.

Everything is pure apart from send_stage_email(): build_*_email() takes plain
values and returns {subject, html, text}, so the dashboard's preview renders
byte-for-byte what would be delivered.

NOTHING IN HERE SENDS ON ITS OWN. While PIPELINE_AUTO_EMAIL is off -- which is
how it ships -- send_stage_email() is only ever reached from a Send click in
the dashboard, after somebody read the preview. Moving a candidate on the board
records the move and stops there, and so does a hiring manager's decision on
their review link. Flipping that one flag in .env restores the automatic send
on both surfaces without touching this module.
"""

import logging
import re
from datetime import datetime

from backend.core.config import (
    BREVO_SENDER_NAME,
    CANDIDATE_REPLY_TO,
    INTERVIEW_BOOK_WITHIN_DAYS,
    PIPELINE_AUTO_EMAIL,
    PIPELINE_EMAILS_ENABLED,
)
from backend.notifications import brevo_client
from backend.notifications import unsubscribe
from backend.database import mongo_store as store

log = logging.getLogger(__name__)

# Ajaia's palette, shared with the reminder and the shortlist so every message
# from this system reads as one sender.
NAVY = "#001d6b"
INK = "#1b1c1c"
LINE = "#e4e7ec"
MUTED = "#5b6270"

# The stages a candidate is told about. `hired` and `None` are absent on
# purpose -- see the module docstring.
MAILED_STAGES = ("interview", "rejected")


class CandidateMailError(RuntimeError):
    """A stage mail that cannot be built or sent, with a reason worth showing."""


def stage_is_mailed(stage) -> bool:
    return stage in MAILED_STAGES


# ---------------------------------------------------------------------------
# Who the interview is with
# ---------------------------------------------------------------------------

def _norm(value) -> str:
    return str(value or "").strip().lower()


def resolve_manager(role: dict, interviewer: str = "",
                    manager_email: str = "") -> dict | None:
    """
    Which hiring manager this interview belongs to, best guess first.

    The candidate is told a name and given a calendar, and both have to be the
    same person -- "Anita will meet you" over Ravi's booking page is a mistake
    the candidate discovers in the meeting.

    Order: the manager the dashboard names outright (whoever is signed in and
    clicked), then the `interviewer` field matched against the role's managers,
    then the sole manager if the role has exactly one. Nothing after that: a
    role with three managers and an unmatched interviewer name is genuinely
    ambiguous, and guessing which of the three owns the meeting would put a
    stranger's calendar in front of the candidate.
    """
    managers = [m for m in (role.get("hiring_managers") or []) if m.get("email")]
    if not managers:
        return None

    if manager_email:
        wanted = _norm(manager_email)
        for manager in managers:
            if _norm(manager.get("email")) == wanted:
                return manager

    hint = _norm(interviewer)
    if hint:
        for manager in managers:
            if hint in (_norm(manager.get("email")), _norm(manager.get("name"))):
                return manager
        # A first name is what gets typed into an "Interviewer" box, and it is
        # unambiguous only while it stays unique across the role's managers.
        first = [m for m in managers
                 if _norm(m.get("name")).split(" ")[0] == hint.split(" ")[0]]
        if len(first) == 1:
            return first[0]

    return managers[0] if len(managers) == 1 else None


def booking_link(role: dict, override: str = "", interviewer: str = "",
                 manager_email: str = "") -> tuple[str, dict | None]:
    """
    The cal.com link to put in the mail, and the manager it came from.

    An override wins: it is the link the manager just typed into the dashboard,
    which is by definition newer than anything stored. It is normalised through
    the same function that stores it, so a pasted "cal.com/anita" cannot reach a
    candidate as a relative href.
    """
    manager = resolve_manager(role, interviewer, manager_email)
    link = store.clean_cal_link(override)
    if not link and manager:
        link = store.clean_cal_link(manager.get("cal_link"))
    return link, manager


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
        "Sunday"]


def fmt_when(value) -> str:
    """
    "2026-08-20T14:30" -> "Thursday 20 August, 14:30".

    Formatted by hand rather than through a locale: the stored string is the
    wall-clock time the interviewer typed, in their own day, and parsing it
    into an instant would attach a timezone nobody chose and move real meetings
    in the candidate's inbox.
    """
    if isinstance(value, datetime):
        value = value.strftime("%Y-%m-%dT%H:%M")
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?",
                     str(value or "").strip())
    if not match:
        return str(value or "").strip()
    year, month, day, hour, minute = match.groups()
    try:
        weekday = DAYS[datetime(int(year), int(month), int(day)).weekday()]
    except ValueError:
        return str(value)
    date = f"{weekday} {int(day)} {MONTHS[int(month) - 1]}"
    if int(year) != datetime.now().year:
        date += f" {year}"
    return f"{date}, {hour}:{minute}" if hour else date


# ---------------------------------------------------------------------------
# The editable invitation
# ---------------------------------------------------------------------------
#
# THE HIRING MANAGER WRITES THE INVITATION. What they are handed is the default
# below, as plain text in a box they can rewrite; what they cannot touch is the
# shell around it -- the Ajaia header, the booking button carrying their own
# cal.com link, the fallback URL under it, and the signature.
#
# That split is the whole design. A manager editing raw HTML can delete the
# booking button, and an invitation with no way to book is the one message this
# module exists to prevent. A manager editing prose cannot: the button is
# rendered after their words by the same function whatever they wrote.
#
# One message goes to everyone they picked, so it is written in placeholders
# rather than in one candidate's name. Unknown braces are left exactly as typed
# -- a manager who writes "{salary}" should see "{salary}" in the preview and
# notice, rather than have it silently vanish somewhere between here and an
# inbox.

# `interviewer` and `manager` resolve to the same person. Both exist because
# the default copy asks "who will I be meeting", where "interviewer" is the
# natural word, while a manager writing their own may well be talking about
# the hiring manager as such. Neither is dropped: a message typed before the
# other existed still fills in.
PLACEHOLDERS = ("first_name", "name", "role", "manager", "interviewer",
                "when")

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def fill(text: str, values: dict) -> str:
    """Substitute {first_name} and friends, leaving anything unknown as typed."""
    return _PLACEHOLDER.sub(
        lambda match: str(values.get(match.group(1), match.group(0))),
        str(text or ""))


def placeholder_values(candidate_name: str, role_title: str,
                       manager: dict | None = None,
                       interview_at: str = "") -> dict:
    """
    What the placeholders in a manager's message resolve to for one candidate.

    `manager` is "Anita Desai, VP Engineering" where we know who owns the
    meeting and "The hiring manager" where we do not -- naming the wrong person
    is worse than naming nobody, and the candidate finds out in the room.
    """
    who = (manager or {}).get("name") or ""
    who_title = (manager or {}).get("title") or ""
    return {
        "first_name": _first_name(candidate_name),
        "name": str(candidate_name or "").strip() or "there",
        "role": role_title or "the role",
        "manager": (f"{who}, {who_title}" if who and who_title
                    else (who or "The hiring manager")),
        # The NAME ALONE, where `manager` is "name, title". The two differ
        # because of where each one lands in a sentence: "{manager} would like
        # to meet you" carries a title comfortably at the head of a clause,
        # while "you'll meet with {interviewer} to walk through your
        # submission" does not -- "Priyansh Singh, Engineering to walk
        # through" reads as a dropped word. Lower-cased fallback for the same
        # reason: it is mid-sentence.
        "interviewer": who or "the hiring manager",
        "when": fmt_when(interview_at) if str(interview_at or "").strip() else "",
    }


def default_interview_subject(role_title: str = "") -> str:
    return f"Next step for the {role_title or 'the role'} role at Ajaia"


def default_interview_message(interview_at: str = "", note: str = "") -> str:
    """
    The invitation a manager starts from, in placeholders and plain text.

    Ends on "Grab a time here:" because the booking button is appended straight
    after it by build_interview_email(). Everything that has to be in the mail
    for it to work -- the button, the fallback URL, the signature -- is below
    the manager's cursor, not inside the box they are editing.

    TWO DEPARTURES FROM THE COPY THIS WAS WRITTEN FROM, both forced by that
    boundary rather than chosen:

      * the source read "Grab a time here: {link}", and there is no {link}
        placeholder. The button is appended and cannot be edited away, so the
        line stops at the colon and the button is what follows it. Left as
        written, fill() would have leaked a literal "{link}" to candidates: it
        passes unknown placeholders through untouched on purpose, so that a
        manager's typo comes back to us rather than going out as one.

      * the "book within the next few days" nudge sits above the ask rather
        than below it, because nothing can sit below it -- the button ends the
        message.

    The sign-off is not here either. _signature() appends it, and signs with
    the name of the person the candidate is about to meet rather than with the
    team; see the note there for why that is worth keeping.
    """
    parts = [
        "Hi {first_name},",
        "We reviewed your assessment and we're impressed. We'd like to move "
        "you forward to an interview.",
        "You'll meet with {interviewer} to walk through your submission and "
        "dig into the role. Expect the call to last about 20 minutes.",
    ]
    if str(interview_at or "").strip():
        parts.append(
            "We have pencilled in {when}. If that does not work, pick any "
            "other slot on the calendar below — no need to reply.")
    if str(note or "").strip():
        parts.append(note.strip())
    parts.append(
        "We're moving quickly on this role, so please book within the next "
        "few days. If none of the times work, reply to this email and we'll "
        "find one that does.")
    parts.append("Grab a time here:")
    return "\n\n".join(parts)


def _paragraphs(text: str) -> str:
    """
    A manager's plain text as HTML paragraphs.

    Blank-line separated blocks become <p>; single newlines inside a block
    survive on pre-wrap, so a list typed as three lines arrives as three lines.
    Escaped rather than templated -- this is free text from someone outside the
    codebase, and a stray angle bracket in it should reach the candidate as an
    angle bracket rather than as a broken layout.
    """
    blocks = [block.strip()
              for block in re.split(r"\n\s*\n", str(text or "").strip())
              if block.strip()]
    return "".join(f'<p style="margin:0 0 16px;white-space:pre-wrap;">'
                   f'{_esc(block)}</p>' for block in blocks)


def _first_name(full_name: str) -> str:
    """"Viral Chovatiya" -> "Viral". The greeting is personal in both mails."""
    name = str(full_name or "").strip()
    return name.split()[0] if name else "there"


def _esc(value) -> str:
    """Escape for HTML. Names, role titles and manager notes are all free text."""
    return (str(value if value is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _shell(body: str) -> str:
    """The Ajaia header and card both mails sit inside."""
    return f"""
    <div style="font-family:'Poppins',Arial,sans-serif;max-width:600px;margin:0 auto;
                padding:32px;color:{INK};font-size:15px;line-height:1.6;">

      {brevo_client.header_html()}

      <div style="padding:32px;border:1px solid {LINE};border-top:none;
                  border-radius:0 0 8px 8px;">{body}
      </div>
    </div>
    """


# The shell, the paragraph renderer and the escaper, under public names.
#
# rejections.py sends the bulk turn-down and has to sit inside the same navy
# header and the same card as the two mails here, or the one candidate who gets
# both a reminder and a rejection sees two different companies. These are the
# same three functions rather than a second copy of them, for the reason
# header_html() is shared with brevo_client: a shell copied into two files is a
# shell that is right in one of them a release later.
shell_html = _shell
paragraphs_html = _paragraphs
esc_html = _esc
first_name_of = _first_name


def _note_block(note: str) -> str:
    if not str(note or "").strip():
        return ""
    return f"""
        <p style="margin:0 0 20px;padding:12px 16px;background:#f6f7fb;
                  border-left:3px solid {NAVY};white-space:pre-wrap;">{
            _esc(note.strip())}</p>"""


def _signature(manager: dict | None) -> str:
    """
    Signed by the manager where there is one, by the hiring team otherwise.

    A meeting invitation signed by a name the candidate will actually meet
    reads as a person asking; the same words from "Ajaia Hiring Team" read as a
    workflow.
    """
    if manager and manager.get("name"):
        title = manager.get("title") or "Ajaia"
        return f"""
        <p style="margin:0;">
          Best,<br>
          {_esc(manager['name'])}<br>
          <span style="color:{MUTED};font-size:13px;">{_esc(title)}</span>
        </p>"""
    return f"""
        <p style="margin:0;">
          Best,<br>
          {_esc(BREVO_SENDER_NAME)}<br>
          <span style="color:{MUTED};font-size:13px;">Ajaia Hiring Team</span>
        </p>"""


def _signature_text(manager: dict | None) -> list[str]:
    if manager and manager.get("name"):
        return ["Best,", manager["name"], manager.get("title") or "Ajaia"]
    return ["Best,", BREVO_SENDER_NAME, "Ajaia Hiring Team"]


def build_interview_email(candidate_name: str, role_title: str, cal_link: str,
                          manager: dict | None = None, interview_at: str = "",
                          note: str = "", message: str = "",
                          subject: str = "") -> dict:
    """
    The interview invitation. Requires a booking link -- that is the message.

    `message` and `subject` are the hiring manager's own words, written on
    their review page and arriving here as plain text with placeholders still
    in it. Both fall back to the default copy when they are blank, so a manager
    who changes nothing sends exactly what this system has always sent.

    What the manager writes is the top of the mail and nothing else. The
    booking button, the fallback URL and the signature are appended here
    afterwards, every time, whatever they typed -- see the editable-invitation
    note above for why that boundary is where it is.

    A time already pencilled in changes the ask rather than replacing it: the
    candidate is told what is held for them and still given the calendar, so
    "that slot does not work" is one click instead of a reply and a wait.
    """
    link = store.clean_cal_link(cal_link)
    if not link:
        raise CandidateMailError(
            "No booking link for this interview. Add the manager's cal.com "
            "link in the dashboard before emailing the candidate."
        )

    values = placeholder_values(candidate_name, role_title, manager, interview_at)
    written = str(message or "").strip() or default_interview_message(interview_at, note)
    body_text = fill(written, values)
    line = fill(str(subject or "").strip() or default_interview_subject(role_title),
                values)

    body = f"""
        {_paragraphs(body_text)}
        <p style="margin:0 0 20px;">
          <a href="{_esc(link)}"
             style="display:inline-block;background:{NAVY};color:#ffffff;
                    text-decoration:none;font-weight:600;padding:12px 24px;
                    border-radius:6px;">Book your interview</a>
        </p>

        <p style="margin:0 0 24px;color:{MUTED};font-size:13px;">
          If the button does not work, use this link:<br>
          <a href="{_esc(link)}" style="color:#0b2e8e;word-break:break-all;">{_esc(link)}</a>
        </p>
        {_signature(manager)}"""

    lines = body_text.split("\n")
    lines += ["", link, ""]
    lines += _signature_text(manager)

    return {
        "subject": line,
        "html": _shell(body),
        "text": "\n".join(lines),
        # Handed back so a preview can show the manager the resolved words --
        # their placeholders filled in for the candidate it rendered against --
        # next to the box they are still editing.
        "message": body_text,
    }


def build_rejection_email(candidate_name: str, role_title: str,
                          manager: dict | None = None, note: str = "") -> dict:
    """
    The turn-down, after a human read the submission.

    No score, no band, no criterion breakdown. The candidate is told the
    outcome and that the round was competitive, which is the part that is true
    and the part they are owed; an AI number they cannot argue with is neither.

    The greeting is deliberately "Hi there" rather than a first name, and the
    sign-off is the hiring team rather than whoever clicked: a rejection is the
    company's decision, not one person's, and a candidate who wants to argue it
    should be writing to the team that made it. `manager` is kept in the
    signature for callers' sake but is not used in this copy.

    A manager's note is passed through verbatim when there is one. Specific
    feedback from the person who read you is worth more than every sentence of
    the template around it.
    """
    title = role_title or "the role"

    body = f"""
        <p style="margin:0 0 16px;">Hi there,</p>

        <p style="margin:0 0 16px;">
          Thank you for taking the time to complete the
          <strong>{_esc(title)}</strong> assessment and interview. We know
          timed assignments require real effort, and we appreciate you
          investing that in our process.
        </p>

        <p style="margin:0 0 16px;">
          After reviewing all submissions, we&rsquo;ve decided to move forward
          with other candidates whose work more closely matched what we&rsquo;re
          looking for at this stage.
        </p>
        {_note_block(note)}
        <p style="margin:0 0 16px;">
          This doesn&rsquo;t reflect on your abilities overall. The applicant
          pool was large and the bar was specific to our current needs. We
          encourage you to keep building and shipping.
        </p>

        <p style="margin:0 0 16px;">
          We&rsquo;ll keep your information on file and may reach out if a
          future role is a better fit.
        </p>

        <p style="margin:0 0 24px;">Wishing you the best in your search.</p>

        <p style="margin:0;">Ajaia Hiring Team</p>"""

    lines = [
        "Hi there,",
        "",
        f"Thank you for taking the time to complete the {title} assessment and "
        f"interview. We know timed assignments require real effort, and we "
        f"appreciate you investing that in our process.",
        "",
        "After reviewing all submissions, we've decided to move forward with "
        "other candidates whose work more closely matched what we're looking "
        "for at this stage.",
        "",
    ]
    if str(note or "").strip():
        lines += [note.strip(), ""]
    lines += [
        "This doesn't reflect on your abilities overall. The applicant pool was "
        "large and the bar was specific to our current needs. We encourage you "
        "to keep building and shipping.",
        "",
        "We'll keep your information on file and may reach out if a future role "
        "is a better fit.",
        "",
        "Wishing you the best in your search.",
        "",
        "Ajaia Hiring Team",
    ]

    return {
        "subject": f"Your application for {title} at Ajaia",
        "html": _shell(body),
        "text": "\n".join(lines),
    }


def build_stage_email(submission: dict, role: dict, stage: str,
                      cal_link: str = "", interviewer: str = "",
                      manager_email: str = "", note: str = "",
                      message: str = "", subject: str = "",
                      interview_at: str = "") -> dict:
    """
    Render whichever mail `stage` calls for, plus who and what it resolved to.

    Returns {subject, html, text, to, to_name, stage, cal_link, manager}. The
    preview endpoint and the send path both come through here, so a preview
    cannot show a link the send would not use.

    `message` and `subject` are the hiring manager's edits to the invitation.
    They are ignored for a rejection: that copy is the company's fixed wording,
    signed by the hiring team rather than by whoever clicked, and no part of it
    is one a manager should be able to reword by accident. What a manager does
    have to say to a rejected candidate goes through `note`, which is passed
    through verbatim and is the part worth more than the template around it.
    """
    if not stage_is_mailed(stage):
        raise CandidateMailError(f"No candidate email is sent for {stage or 'this move'}.")

    to = str(submission.get("candidate_email") or "").strip()
    if "@" not in to:
        raise CandidateMailError(
            "This candidate has no email address on record, so nothing can be sent."
        )

    name = submission.get("candidate_name") or ""
    title = submission.get("job_title") or role.get("title") or ""
    link, manager = booking_link(role, cal_link, interviewer, manager_email)

    if stage == "interview":
        email = build_interview_email(
            name, title, link, manager,
            # A proposed time wins over the stored one. The composer previews a
            # move that has not happened yet, so the submission still carries
            # nothing -- and a preview whose "{when}" renders empty is a
            # preview of a different email from the one about to be sent.
            interview_at=(interview_at
                          or (submission.get("pipeline") or {}).get("interview_at")
                          or ""),
            note=note, message=message, subject=subject,
        )
    else:
        email = build_rejection_email(name, title, manager, note)
        link = ""

    return {**email, "to": to, "to_name": name or to, "stage": stage,
            "cal_link": link, "manager": manager}


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def already_sent(submission: dict, stage: str, cal_link: str = "") -> dict | None:
    """
    A previous successful send that makes this one a duplicate, or None.

    A rejection is a duplicate the second time, always -- there is no version of
    "you were not successful" worth reading twice. An interview is only a
    duplicate while nothing about it has changed: a new booking link or a new
    time is a genuine second message, and suppressing it would leave the
    candidate holding a calendar that is no longer the right one.

    FOR A REJECTION THE LEDGER IS CONSULTED TOO, and it is consulted first.
    `pipeline.emails` only knows about mail this board sent, and most of the
    people who have been turned down were told through the bulk send on the
    Rejections page or by hand out of a BCC field -- neither of which writes to
    this submission. Reading only the board's own history is how somebody who
    was rejected in March gets a second, differently-worded rejection in
    August, which is the exact mistake the ledger exists to stop.
    """
    if stage == "rejected":
        row = store.rejection_for(submission.get("candidate_email"))
        if row and row.get("status") in store.REJECTION_DELIVERED:
            return {"stage": "rejected", "at": row.get("rejected_at"),
                    "to": row.get("_id"), "subject": row.get("subject") or "",
                    "ok": True, "source": row.get("source") or "ledger"}

    previous = store.last_stage_email(submission, stage)
    if previous is None:
        return None
    if stage == "interview":
        link = store.clean_cal_link(cal_link)
        if link and link != previous.get("cal_link"):
            return None
    return previous


def send_stage_email(submission: dict, role: dict, stage: str,
                     cal_link: str = "", interviewer: str = "",
                     manager_email: str = "", note: str = "",
                     message: str = "", subject: str = "",
                     force: bool = False) -> dict:
    """
    Mail one candidate about a stage move and record what happened.

    Returns {sent: bool, reason?, ...}. Nothing here raises for an ordinary
    "nothing to send" -- the stage move itself has already happened and is not
    being rolled back because an email was skipped, so the caller needs a result
    it can report rather than an exception it has to swallow. A build that is
    genuinely wrong (no address, no booking link) raises CandidateMailError,
    because that is a thing the manager can fix and try again.
    """
    if not PIPELINE_EMAILS_ENABLED:
        return {"sent": False, "reason": "Candidate emails are switched off "
                                         "(PIPELINE_EMAILS_ENABLED=0)."}
    if not stage_is_mailed(stage):
        return {"sent": False, "reason": f"No candidate email is sent for "
                                         f"{stage or 'a removal from the board'}."}

    if not force:
        previous = already_sent(submission, stage, cal_link)
        if previous is not None:
            when = previous.get("at")
            when = when.strftime("%d %b") if isinstance(when, datetime) else str(when)[:10]
            return {"sent": False, "already": True,
                    "reason": f"Already emailed on {when}. Use resend to send it again."}

    email = build_stage_email(submission, role, stage, cal_link=cal_link,
                              interviewer=interviewer, manager_email=manager_email,
                              note=note, message=message, subject=subject)

    # Replies go to a person -- the manager who owns the meeting where we know
    # them, the recruiter otherwise. "I cannot make any of those times" and
    # "could I ask what let me down" both deserve somewhere to land.
    reply_to = (email["manager"] or {}).get("email") or CANDIDATE_REPLY_TO

    # A rejection is the one message here that is bulk-shaped -- the same words
    # to everybody who did not make it -- so it carries the header that lets a
    # reader stop, and it respects the list of people who already have. An
    # interview invitation does neither, deliberately: it is a message the
    # candidate is waiting for about a meeting they are being offered, and
    # withholding it because they once opted out of a reminder would cost them
    # the interview.
    headers = None
    if stage == "rejected":
        if unsubscribe.is_suppressed(email["to"]):
            return {"sent": False, "unsubscribed": True,
                    "reason": f"{email['to']} has asked not to be emailed."}
        headers = unsubscribe.mail_headers(email["to"])

    try:
        brevo_client.send_email(
            to=[{"email": email["to"], "name": email["to_name"]}],
            subject=email["subject"],
            html=email["html"],
            text=email["text"],
            reply_to=reply_to,
            headers=headers or None,
        )
    except brevo_client.BrevoError as exc:
        log.error("Stage mail (%s) to %s failed: %s", stage, email["to"], exc)
        store.record_stage_email(submission["_id"], stage, email["to"],
                                 email["subject"], email["cal_link"], str(exc))
        if stage == "rejected":
            _ledger(submission, email, "failed", str(exc))
        return {"sent": False, "error": str(exc),
                "reason": f"Could not email {email['to']}: {exc}"}

    store.record_stage_email(submission["_id"], stage, email["to"],
                             email["subject"], email["cal_link"])
    if stage == "rejected":
        _ledger(submission, email, "sent")
    log.info("Stage mail (%s) sent to %s", stage, email["to"])
    return {"sent": True, "to": email["to"], "subject": email["subject"],
            "cal_link": email["cal_link"],
            "manager": (email["manager"] or {}).get("email", "")}


def _ledger(submission: dict, email: dict, status: str, error: str = "") -> None:
    """
    Note a board rejection in the ledger the bulk send reads.

    Written here as well as onto the submission so that the two rejection
    surfaces agree about who has already been told. Best-effort: the mail has
    already gone, and an exception raised now would report a send that
    succeeded as one that failed.
    """
    try:
        store.record_rejection(
            email["to"], name=email.get("to_name") or "",
            job_id=submission.get("job_id"),
            job_title=submission.get("job_title") or "",
            status=status, source="pipeline", subject=email.get("subject") or "",
            submission_id=submission.get("_id"), error=error,
        )
    except Exception:
        log.exception("Could not write %s to the rejection ledger", email["to"])
