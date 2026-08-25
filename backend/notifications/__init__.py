"""
Every outbound email.

    brevo_client     the transport: Brevo's API, used by everything below
    reminder         the assessment nudge, and the orchestration around it
    candidate_mail   the two candidate-facing outcomes: interview and rejection
    shortlist        the hiring-manager hand-off: table, spreadsheet, send
    rejections       the bulk turn-down, and the ledger of who has been told
    unsubscribe      the way out, and the check every candidate send makes

If a message leaves this system, it leaves from here. That is the point of the
grouping: one directory to read before changing anything a real person receives.
"""
