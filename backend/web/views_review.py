"""
The manager review surface, and the invitation they write.

Token-authenticated and outside the account system: a hiring manager
opens this from a link in their mail. _review_guard is the whole of
what stands in front of it.

Split out of server.py, which was 4,673 lines and 51 routes. The app and
everything shared come from backend.web.app; this module imports from
there and nowhere else in backend.web.
"""


from flask import jsonify, request, send_from_directory


from backend.mail import candidate_mail
from backend.db import store
from backend.mail import shortlist

from backend.web.app import (FRONTEND_DIR, _mongo_guard, app, log)

# ---------------------------------------------------------------------------
# Manager review surface
#
# The one part of this server a person outside the company ever touches. Every
# route here is reached with a token and nothing else -- no session, no login,
# no user id in the path -- so each one re-derives what the token may see
# rather than trusting anything the browser sent alongside it.
#
# Three rules hold throughout:
#   1. The token names the role AND the manager AND the exact candidate list.
#      Nothing is scoped by a parameter the caller controls.
#   2. Scores never enter a payload. submissions_for_review() projects
#      `evaluation` out at the database, not at the template.
#   3. An unknown token and a revoked one get different answers, because a
#      person holding a real-but-dead link needs to be told which they have.
# ---------------------------------------------------------------------------

# What a manager may set. `hired` and `rejected` are terminal, `interview` is
# the step before them. Returning someone to the shortlist is not offered: it
# is the recruiter's undo, and a manager who changes their mind should say so
# to a person rather than silently rewind a board other people are reading.
MANAGER_STAGES = ("interview", "hired", "rejected")

# ...and which of them /decision handles. Not `interview`: an invitation is a
# message the manager writes, so it goes through the composer and its own route
# below, where there is a subject, a body and a preview. A one-click interview
# button would send copy nobody read -- which is the thing this whole surface
# was built to stop.
MANAGER_DECISION_STAGES = ("hired", "rejected")

REVIEW_DEAD = {
    "unknown": ("This review link is not valid. It may have been mistyped — "
                "try opening it from the original email again.", 404),
    "revoked": ("This review link has been withdrawn. Ask the recruiter who "
                "sent it for a new one.", 410),
    "expired": ("This review link has expired. Ask the recruiter who sent it "
                "for a new one.", 410),
}


def _review_guard(token: str):
    """
    Resolve a token to its link document, or an error response.

    Returns (link, None) when the link may be used and (None, response)
    when it may not.
    """
    link = store.get_review_link(token)
    state = store.review_link_state(link)
    if state != "ok":
        message, status = REVIEW_DEAD[state]
        return None, (jsonify({"error": message, "state": state}), status)
    return link, None


def _review_row(sub: dict, rank: int) -> dict:
    """
    One candidate as the manager's page sees them.

    Built field by field from a fixed list rather than by copying the document
    and deleting what should not go -- the score is not in `evaluation` alone,
    and a submission gains fields over time. An allowlist cannot leak a field
    nobody thought about.
    """
    pipeline = sub.get("pipeline") or {}
    return {
        "rank": rank,
        "submission_id": sub["_id"],
        "name": sub.get("candidate_name") or "(no name)",
        "email": sub.get("candidate_email") or "",
        "resume_link": sub.get("resume_link") or "",
        "video_link": sub.get("video_link") or "",
        "assessment_url": sub.get("admin_url") or "",
        "submitted_at": shortlist._fmt_date(sub.get("submitted_at")),
        # Where they already are, so a manager coming back to the page sees
        # their own earlier decisions instead of a fresh set of buttons.
        "stage": pipeline.get("stage"),
        "stage_at": pipeline.get("at").isoformat() if pipeline.get("at") else None,
        "stage_by": pipeline.get("by"),
        "note": pipeline.get("note"),
        "interview_at": pipeline.get("interview_at") or None,
        # Whether the invitation actually left. Distinct from the stage: a
        # candidate can sit at `interview` with nothing in their inbox, because
        # the send failed or because mail is switched off, and a manager
        # re-reading the list needs to see which of the two they are looking at
        # before they wait another week for a booking that cannot come.
        "invited_at": _invited_at(sub),
        # The one thing about the grading that reaches this page. Not the
        # score, not the band -- only whether the AI finished the rubric. This
        # page shows rank and no number by design, so without this a candidate
        # whose grid was renormalised from two rows would sit at position 1
        # with nothing to read against it. `store.submissions_for_review` sets
        # it; the score itself never enters the payload.
        "grading_incomplete": bool(sub.get("grading_incomplete")),
    }


def _invited_at(sub: dict) -> str | None:
    """When this candidate was last successfully sent an invitation, or None."""
    previous = store.last_stage_email(sub, "interview")
    at = (previous or {}).get("at")
    return at.isoformat() if at else None


@app.route("/review/<token>")
def review_page(token: str):
    """
    The manager's page. Serves the same HTML whatever the token is.

    The token is checked by /api/review/<token>, which the page calls on load,
    so an invalid link renders a sentence explaining which kind of invalid it
    is rather than a bare 404 from the web server. Serving the shell
    unconditionally also means the token never reaches a Flask error handler,
    a log line or an access log entry as a 404 that says "this one was wrong".
    """
    return send_from_directory(FRONTEND_DIR, "review.html")


@app.route("/api/review/<token>")
def api_review(token: str):
    """Everything the manager's page draws: the role, them, and their list."""
    error = _mongo_guard()
    if error:
        return error

    link, dead = _review_guard(token)
    if dead:
        return dead

    store.touch_review_link(token)
    role = store.get_role(link["job_id"]) or {}
    rows = [_review_row(sub, i)
            for i, sub in enumerate(store.submissions_for_review(link), start=1)]

    return jsonify({
        "role": {"title": role.get("title"), "slug": role.get("slug")},
        "manager": link["manager"],
        "candidates": rows,
        "expires_at": link["expires_at"].isoformat() if link.get("expires_at") else None,
        "stages": list(MANAGER_STAGES),
        # Which stages reach the candidate, so the page can warn before the
        # click rather than explain after it. `hired` is absent from
        # MAILED_STAGES on purpose -- an offer is a conversation, not a
        # templated mail a board click fires.
        "mailed_stages": list(candidate_mail.MAILED_STAGES),
        # Whether anything on this page reaches a candidate, as two switches
        # rather than one, because they now govern two different clicks:
        #
        #   emails_enabled  the master switch. Off, and nothing leaves the
        #                   building from anywhere -- decisions are recorded
        #                   and recruiting writes to people by hand.
        #   auto_email      whether HIRED and REJECTED mail on the click. Off
        #                   is how the system ships: the decision is recorded
        #                   and a recruiter sends the message after reading it.
        #
        # The interview invitation answers to the master switch and to nothing
        # else, deliberately. It leaves from a composer the manager wrote and
        # previewed a second earlier, and that IS the human read of the message
        # that auto_email exists to force.
        "emails_enabled": candidate_mail.PIPELINE_EMAILS_ENABLED,
        "auto_email": candidate_mail.PIPELINE_AUTO_EMAIL,
        # The tokens a manager may write into the invitation. Served rather
        # than hard-coded in the page, so the list the composer offers cannot
        # drift from the list fill() actually substitutes.
        "placeholders": list(candidate_mail.PLACEHOLDERS),
        # Whether this manager can actually invite anyone. An interview mail
        # with no booking link is refused, and a manager should find that out
        # from a line on the page rather than from a red error after clicking.
        #
        # Resolved LIVE against the role rather than read off the token: the
        # link document snapshots who the manager was when it was minted, and
        # the commonest fix for "we have no booking link for you" is the
        # recruiter adding one afterwards. Reading the snapshot would leave the
        # page insisting they still cannot book until somebody re-sent the
        # whole shortlist. Same call the decision route gates on, so the notice
        # and the refusal can never disagree.
        "can_book": bool(candidate_mail.booking_link(
            role, "", link["manager"].get("name") or "",
            link["manager"].get("email") or "")[0]),
    })


@app.route("/api/review/<token>/decision", methods=["POST"])
def api_review_decision(token: str):
    """
    A manager marking one candidate hired or rejected.

    Body: {submission_id, stage, note?}.

    NOT INTERVIEW. That is an invitation with a subject line and a body the
    manager writes, and it goes through /api/review/<token>/invite, where there
    is a composer and a preview in front of it.

    NOBODY IS EMAILED FROM HERE while PIPELINE_AUTO_EMAIL is off. The manager's
    decision is recorded and comes back as `mail.queued`, and the page says "we
    will email them shortly" rather than claiming a send that has not happened.
    A recruiter then reads the message in the dashboard and clicks Send. This
    is the same pause the board itself now takes -- one system, one moment
    where a person looks at the mail before a candidate does.

    With the switch on, the board write happens first and the candidate's email
    second, and a failed email does NOT fail the request. The move is the
    durable thing; a manager who saw a red error would click again, moving the
    same person twice and possibly sending two mails. What the mail did is
    reported back in `mail` either way, so the page can say "marked for
    interview, but the invitation could not be sent" -- which is the honest
    sentence.
    """
    error = _mongo_guard()
    if error:
        return error

    link, dead = _review_guard(token)
    if dead:
        return dead

    body = request.get_json(silent=True) or {}
    submission_id = body.get("submission_id")
    stage = body.get("stage")

    if not isinstance(submission_id, int):
        return jsonify({"error": "submission_id must be a number."}), 400
    if stage == "interview":
        return jsonify({
            "error": "Interview invitations are written and sent from the "
                     "invite composer, so the candidate gets a message you "
                     "have read.",
            "needs": "compose",
        }), 400
    if stage not in MANAGER_DECISION_STAGES:
        return jsonify({"error": f"Unknown stage: {stage}"}), 400
    # The token's own list is the authority, not the role. Without this a
    # manager could move any candidate whose id they guessed -- including one
    # on a role they have nothing to do with.
    if submission_id not in (link.get("submission_ids") or []):
        return jsonify({
            "error": "That candidate is not on the list you were sent."
        }), 403

    submission = store.get_submission(submission_id)
    if submission is None:
        return jsonify({"error": f"No submission {submission_id}."}), 404

    def field(name: str) -> str | None:
        value = body.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else None

    manager = link["manager"]
    note = field("note")
    role = store.get_role(link["job_id"]) or {}

    # The booking-link refusal lives on the invite route now, with the only
    # stage that ever needed one.

    store.set_pipeline_stage(
        submission_id, stage,
        # The manager IS the interviewer here, which is what lets
        # candidate_mail.resolve_manager() find their calendar and sign a later
        # message with their name.
        interviewer=manager.get("name"),
        note=note,
        reason=note if stage == "rejected" else None,
        source="manager",
        by=f"{manager.get('name')} <{manager.get('email')}>",
    )
    store.record_review_action(token, submission_id, stage, note)

    # The same function the dashboard sends through, so a candidate gets the
    # same message whichever surface moved them -- including the duplicate
    # suppression, which is what stops a manager clicking twice from sending
    # two rejections. Best-effort: the move has already happened and is not
    # rolled back because mail failed.
    mail: dict = {"sent": False, "reason": "No email is sent for this stage."}
    if candidate_mail.stage_is_mailed(stage) and not candidate_mail.PIPELINE_AUTO_EMAIL:
        # Manual mode: the decision is the manager's, the email is ours to
        # send once a recruiter has read it. Reported as `queued` rather than
        # as a failure -- nothing went wrong here, and a manager should not be
        # shown a warning about a system that is working as configured.
        mail = {"sent": False, "queued": True,
                "reason": "the recruiting team will send their email."}
    elif candidate_mail.stage_is_mailed(stage):
        moved = store.get_submission(submission_id) or submission
        try:
            mail = candidate_mail.send_stage_email(
                moved, role, stage,
                interviewer=manager.get("name") or "",
                manager_email=manager.get("email") or "",
                note=note or "",
            )
        except candidate_mail.CandidateMailError as exc:
            mail = {"sent": False, "reason": str(exc)}
        except Exception as exc:
            log.exception("review-page stage mail failed")
            mail = {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}

    # A transport failure carries our provider's own words -- an API endpoint,
    # a key problem, an IP allowlist URL. Useful to a recruiter reading the
    # dashboard, wrong in front of a hiring manager: it is infrastructure
    # detail about us, shown to someone outside the company, and there is
    # nothing they could do with it anyway. `error` is only set on a transport
    # failure, so the outcomes a manager CAN act on -- already emailed, no
    # address on record, emails switched off -- still come through verbatim.
    if mail.get("error"):
        log.error("Review-page mail to candidate %s failed: %s",
                  submission_id, mail["error"])
        mail = {"sent": False,
                "reason": "we could not send their email just now. The "
                          "recruiting team has been alerted and will follow "
                          "up — your decision is saved."}

    name = submission.get("candidate_name") or f"submission {submission_id}"
    said = {"hired": "marked hired", "rejected": "marked rejected"}[stage]
    message = f"{name} {said}."
    if candidate_mail.stage_is_mailed(stage):
        if mail.get("sent"):
            message += " They have been emailed."
        elif mail.get("queued"):
            message += " We will email them shortly."
        else:
            message += f" Not emailed: {mail.get('reason', 'no reason given')}"

    return jsonify({
        "message": message,
        "submission_id": submission_id,
        "stage": stage,
        "mail": mail,
    })


# ---------------------------------------------------------------------------
# The invitation the manager writes
# ---------------------------------------------------------------------------
#
# The one door into the interview stage -- see INTERVIEW_IS_THE_MANAGERS. A
# manager ticks the people they want to meet, edits the message, reads it
# rendered, and sends. Two routes, because reading it and sending it are two
# decisions and only one of them is irreversible.


def _invite_picks(link: dict, body: dict) -> tuple[list[int], object]:
    """
    The candidates this request may invite, in the order the manager reads them.

    Returns (ids, None) or ([], error_response). The token's own list is the
    authority, exactly as it is for a single decision: without that check a
    manager could invite any candidate whose id they guessed, including one on
    a role they have nothing to do with.
    """
    raw = body.get("submission_ids")
    if not isinstance(raw, list) or not raw:
        return [], (jsonify({"error": "Pick at least one candidate first."}), 400)
    if not all(isinstance(i, int) for i in raw):
        return [], (jsonify({"error": "submission_ids must be numbers."}), 400)

    allowed = link.get("submission_ids") or []
    if any(i not in allowed for i in raw):
        return [], (jsonify({
            "error": "Some of those candidates are not on the list you were sent."
        }), 403)

    # De-duplicated, and put back into the ranked order the manager is looking
    # at rather than the order the checkboxes happened to be ticked in.
    picked = set(raw)
    return [i for i in allowed if i in picked], None


def _invite_context(link: dict, body: dict) -> tuple[dict | None, object]:
    """
    Everything both invite routes need, resolved once: the role, the manager,
    their booking link, the submissions picked, and the words they typed.

    Returns (context, None) or (None, error_response). Shared so the preview
    and the send cannot disagree about who is being written to, with what, or
    over whose calendar.
    """
    ids, dead = _invite_picks(link, body)
    if dead:
        return None, dead

    role = store.get_role(link["job_id"]) or {}
    manager = link["manager"]

    # Resolved live against the role rather than read off the token: the link
    # snapshots who the manager was when it was minted, and the commonest fix
    # for "we have no booking link for you" is the recruiter adding one
    # afterwards.
    booking, _resolved = candidate_mail.booking_link(
        role, "", manager.get("name") or "", manager.get("email") or "")

    def text(name: str) -> str:
        value = body.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else ""

    when = text("interview_at")
    # One pencilled-in time cannot be right for twelve people. Refused for a
    # batch rather than quietly written onto everybody, which is how twelve
    # candidates all get told to come at two o'clock on Thursday.
    if when and len(ids) > 1:
        return None, (jsonify({
            "error": "A suggested time only fits one candidate at a time. "
                     "Clear it, or invite them one by one.",
        }), 400)

    found = {sub["_id"]: sub for sub in store.submissions_for_review(link)}
    subs = [found[i] for i in ids if i in found]
    if not subs:
        return None, (jsonify({
            "error": "Those candidates are no longer on this list."
        }), 404)

    return {
        "role": role,
        "manager": manager,
        "booking": booking,
        "subs": subs,
        # Blank means "the default", resolved by candidate_mail rather than
        # here, so there is one copy of our copy.
        "subject": text("subject"),
        "message": text("message"),
        "interview_at": when,
        # Internal. Deliberately NOT passed to the mail: the manager already
        # wrote what the candidate reads, in the box above it, and quietly
        # appending a second note would put a line in the email they did not
        # see in the preview.
        "note": text("note"),
    }, None


def _no_booking_link() -> tuple:
    """The one refusal a manager can act on themselves, worded for them."""
    return jsonify({
        "error": "We do not have your booking link yet, so the candidate would "
                 "have no way to pick a time. Reply to the email that brought "
                 "you here with your cal.com link and we will add it.",
        "needs": "cal_link",
    }), 409


def _invite_preview(link: dict):
    """
    The shared half of the invite flow, credential already resolved.

    Takes a link DOCUMENT rather than a token, so one body answers both a
    manager holding a mailed token and a manager signed in to the
    dashboard. The two differ only in how they prove who they are; what
    they may do once proved is identical, and a second copy of this would
    become a second set of rules the moment either was edited.
    """

    context, dead = _invite_context(link, request.get_json(silent=True) or {})
    if dead:
        return dead
    if not context["booking"]:
        return _no_booking_link()

    title = context["role"].get("title") or ""
    defaults = {
        "subject": candidate_mail.default_interview_subject(title),
        "message": candidate_mail.default_interview_message(context["interview_at"]),
    }
    subject = context["subject"] or defaults["subject"]
    message = context["message"] or defaults["message"]

    # Who it is about to go to, and who has already had one. An invitation sent
    # twice is a candidate with two booking links wondering which is real, and
    # this is the line that lets a manager notice before it happens rather than
    # after.
    recipients = [{
        "submission_id": sub["_id"],
        "name": sub.get("candidate_name") or "(no name)",
        "email": sub.get("candidate_email") or "",
        "invited_at": _invited_at(sub),
    } for sub in context["subs"]]

    first = context["subs"][0]
    try:
        email = candidate_mail.build_stage_email(
            first, context["role"], "interview",
            interviewer=context["manager"].get("name") or "",
            manager_email=context["manager"].get("email") or "",
            message=message, subject=subject,
            interview_at=context["interview_at"],
        )
    except candidate_mail.CandidateMailError as exc:
        return jsonify({"error": str(exc), "needs": "cal_link"}), 409

    return jsonify({
        "subject": subject,
        "message": message,
        "defaults": defaults,
        "placeholders": list(candidate_mail.PLACEHOLDERS),
        "cal_link": context["booking"],
        "manager": context["manager"],
        "recipients": recipients,
        "count": len(recipients),
        "preview": {
            "submission_id": first["_id"],
            "name": first.get("candidate_name") or "",
            "to": email["to"],
            "subject": email["subject"],
            "html": email["html"],
        },
    })


@app.route("/api/review/<token>/invite/preview", methods=["POST"])
def api_review_invite_preview(token: str):
    """
    The invitation exactly as it currently reads.

    Body: {submission_ids: [...], subject?, message?, interview_at?}.

    Rendered against the FIRST candidate picked, through the same builder the
    send uses -- a preview from a second template is a preview of nothing. The
    manager's placeholders come back resolved for that one person while the box
    they are still typing in keeps "{first_name}", which is the only honest way
    to show what a batch of twelve is going to say.

    A blank subject or message means the default, and the defaults come back
    alongside, so the composer fills its boxes from this one request instead of
    keeping a second copy of our copy in the frontend.
    """
    error = _mongo_guard()
    if error:
        return error

    link, dead = _review_guard(token)
    if dead:
        return dead
    return _invite_preview(link)

def _invite_send(link: dict, token: str = ""):
    """
    The shared half of the invite flow, credential already resolved.

    Takes a link DOCUMENT rather than a token, so one body answers both a
    manager holding a mailed token and a manager signed in to the
    dashboard. The two differ only in how they prove who they are; what
    they may do once proved is identical, and a second copy of this would
    become a second set of rules the moment either was edited.
    """

    body = request.get_json(silent=True) or {}
    context, dead = _invite_context(link, body)
    if dead:
        return dead
    # Checked once for the batch, before a single move is written: it is the
    # same manager and the same calendar for all of them, and writing the
    # stages first would leave the board saying "booked" for a dozen people who
    # were never given a way to book.
    if not context["booking"]:
        return _no_booking_link()

    manager = context["manager"]
    role = context["role"]
    note = context["note"]
    resend = bool(body.get("resend"))
    signed = f"{manager.get('name')} <{manager.get('email')}>"

    results, sent = [], 0
    for sub in context["subs"]:
        submission_id = sub["_id"]
        name = sub.get("candidate_name") or f"submission {submission_id}"

        store.set_pipeline_stage(
            submission_id, "interview",
            interview_at=context["interview_at"] or None,
            # The manager IS the interviewer, which is what lets
            # candidate_mail.resolve_manager() find their calendar and sign the
            # invitation with their name.
            interviewer=manager.get("name"),
            note=note or None,
            source="manager",
            by=signed,
        )
        # Only when there IS a token. This trail answers "what did this link
        # do while it was live", which is a question about a link -- asked when
        # one is suspected of having been forwarded. A signed-in manager has no
        # link to suspect, and their move is already on the candidate's own
        # pipeline history, stamped with their account and source="manager".
        if token:
            store.record_review_action(token, submission_id, "interview",
                                       note or None)

        # Re-read, so the invitation quotes back the time that was just written
        # rather than the one it replaced.
        moved = store.get_submission(submission_id) or sub
        try:
            mail = candidate_mail.send_stage_email(
                moved, role, "interview",
                interviewer=manager.get("name") or "",
                manager_email=manager.get("email") or "",
                message=context["message"], subject=context["subject"],
                force=resend,
            )
        except candidate_mail.CandidateMailError as exc:
            mail = {"sent": False, "reason": str(exc)}
        except Exception as exc:
            log.exception("invite mail failed for %s", submission_id)
            mail = {"sent": False, "error": f"{type(exc).__name__}: {exc}"}

        # A transport failure carries our provider's own words -- an API
        # endpoint, a key problem, an IP allowlist URL. Useful to a recruiter
        # reading the dashboard, wrong in front of a hiring manager: it is
        # infrastructure detail about us, shown to somebody outside the
        # company, and there is nothing they could do with it anyway. The
        # outcomes a manager CAN act on -- already invited, no address on
        # record, mail switched off -- still come through verbatim.
        if mail.get("error"):
            log.error("Invitation to candidate %s failed: %s",
                      submission_id, mail["error"])
            mail = {"sent": False,
                    "reason": "we could not send their invitation just now. "
                              "The recruiting team has been alerted and will "
                              "follow up \u2014 they are still marked for interview."}

        if mail.get("sent"):
            sent += 1
        results.append({
            "submission_id": submission_id,
            "name": name,
            "sent": bool(mail.get("sent")),
            "already": bool(mail.get("already")),
            "reason": mail.get("reason", ""),
            "invited_at": _invited_at(store.get_submission(submission_id) or moved),
        })

    total = len(results)
    if sent == total:
        message = (f"{results[0]['name']} has been invited." if total == 1
                   else f"All {total} invitations are on their way.")
    elif sent:
        message = (f"Invited {sent} of {total}. "
                   + "; ".join(f"{r['name']}: {r['reason']}"
                               for r in results if not r["sent"])[:400])
    else:
        message = ("Marked for interview, but nothing was emailed. "
                   + "; ".join(f"{r['name']}: {r['reason']}"
                               for r in results)[:400])

    return jsonify({
        "message": message,
        "stage": "interview",
        "sent": sent,
        "total": total,
        "results": results,
    })


@app.route("/api/review/<token>/invite", methods=["POST"])
def api_review_invite(token: str):
    """
    Move the manager's picks to interview and send each of them the invitation
    the manager just wrote.

    Body: {submission_ids: [...], subject?, message?, interview_at?, note?,
           resend?}.

    THIS IS THE ONLY WAY INTO THE INTERVIEW STAGE. Both dashboard routes refuse
    it -- see INTERVIEW_IS_THE_MANAGERS.

    It sends while PIPELINE_AUTO_EMAIL is off, and that is not a hole in the
    manual mode; it is the manual mode arriving at its point. The pause exists
    so a person reads the message before a candidate does, and here that person
    is the one who wrote it, in the composer this request came from, one click
    earlier. What the switch still governs is everything that is not that: a
    board move on the dashboard, and this page's own hired and rejected
    buttons. PIPELINE_EMAILS_ENABLED is absolute either way -- with mail off
    the candidates are still moved and the reply says plainly that nobody was
    written to, rather than reporting a send that never happened.

    Per candidate, not per batch. One missing address or one refused send must
    not cost the other eleven their invitation, so each row carries its own
    outcome and the summary counts them. The move is committed before the send
    and is never rolled back because the send failed: where somebody stands is
    the durable fact, and a manager shown a red error would click again and
    invite the same person twice.
    """
    error = _mongo_guard()
    if error:
        return error

    link, dead = _review_guard(token)
    if dead:
        return dead
    return _invite_send(link, token)
