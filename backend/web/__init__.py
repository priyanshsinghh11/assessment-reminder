"""
The Flask dashboard.

`server` is the entry point: it imports `app` and the four view modules, whose
@app.route decorators are what register the 51 routes, and holds main().

    app                 the Flask object and everything the views share: the
                        two before_request guards, the session helpers, the
                        role and submission scope checks, the run lock
    views_dashboard     sign-in and the reminder dashboard
    views_evaluations   the evaluations page's API: roles, rubrics, grading,
                        pipeline stages
    views_shortlist     the hand-off and everything it mails, including the
                        bulk rejections
    views_review        the manager review surface -- the one that may face the
                        internet

It serves the static `frontend/` directory and every endpoint behind it on top
of the same functions the CLI calls, and enforces, on every route that names a
role, who is allowed to see it.
"""
