"""
Everything that reads the outside world.

    portal_scraper    logs into the portal, downloads the CSV export
    portal_crawler    walks roles and their live assessment markdown
    workable_client   the Workable API: auth, rate limiting, 429 back-off
    workable_scanner  picks the candidates inside the reminder window
    resume_reader     fetches a resume file and extracts its text (PDF/DOCX)

These are the modules that can fail because someone else's service is down, and
they are grouped so that fact is visible from the directory listing.
"""
