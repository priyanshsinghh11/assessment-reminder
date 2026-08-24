#!/usr/bin/env python3
"""
Regression test for the access rules: who can sign in, and which roles they see.

    python test_access.py

WHY THIS FILE AND NOT THE OTHERS. Most of this system is checkable by reading
it -- a wrong reminder window shows up on the dashboard, a wrong grid shows up
in a score. An access rule is different: it is correct exactly when nothing
happens, and the failure is silent by definition. A route added next month with
no `_role_guard()` call will not look wrong on any screen; it will just quietly
hand one team's pipeline to another. This is the thing that notices.

It runs against the real MongoDB and is READ-ONLY on everything that matters:
roles, submissions and evaluations are never written. It creates two throwaway
accounts, uses them, and deletes them -- including on the way out of a failure.

Its fixtures come from the data that is already there: it borrows an existing
hiring manager's address so the manager account has real roles to own, and one
role they do not own to be refused. If no role has a hiring manager yet it says
so and skips those checks rather than inventing data in a live database.

WHEN YOU ADD A ROUTE THAT NAMES A ROLE, add it here. The list in `foreign_role`
below is the closest thing this repo has to a map of the guarded surface.
"""

import sys

import auth
import mongo_store as store
import server
from config import AUTH_ENABLED

ADMIN = "zz-access-test-admin@ajaia.ai"
PASSWORD = "zz-access-test-password-92"

failures: list[str] = []
skipped = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  PASS  " if condition else "  FAIL  ") + name
          + (f"    {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def skip(name: str, why: str) -> None:
    global skipped
    skipped += 1
    print(f"  SKIP  {name}    ({why})")


def main() -> int:
    if not AUTH_ENABLED:
        print("AUTH_ENABLED=0, so there is nothing to test. Unset it and "
              "run again.")
        return 1

    try:
        store.ping()
    except store.MongoUnavailable as exc:
        print(f"error: {exc}")
        return 1

    # Borrow a real hiring manager, so the manager account owns real roles.
    owned_by = store.roles_by_manager()
    manager_email = next(iter(sorted(owned_by)), None)
    if manager_email is None:
        print("No role has a hiring manager yet, so there is no scope to "
              "test. Assign one in the dashboard and run again.")
        return 1

    auth.ensure_indexes()
    cleanup(manager_email)
    try:
        return run(manager_email)
    finally:
        cleanup(manager_email)


def cleanup(manager_email: str) -> None:
    for address in (ADMIN, manager_email):
        try:
            auth.delete_user(address)
        except Exception:                          # nothing to remove
            pass


def run(manager_email: str) -> int:
    auth.create_user(ADMIN, name="Access Test", role="admin",
                     password=PASSWORD, must_change=False)
    auth.create_user(manager_email, role="manager", password=PASSWORD,
                     must_change=False)

    owned = sorted(store.job_ids_for_manager(manager_email))
    every = [role["_id"] for role in store.get_roles()]
    foreign = [job_id for job_id in every if job_id not in owned]
    print(f"\n{manager_email} owns {owned}; {len(foreign)} other roles exist\n")

    server.app.config["TESTING"] = True
    server.app.config["REVIEW_ONLY"] = False

    def sign_in(email: str):
        client = server.app.test_client()
        reply = client.post("/api/auth/login",
                            json={"email": email, "password": PASSWORD})
        assert reply.status_code == 200, (email, reply.get_json())
        client.environ_base["csrf"] = reply.get_json()["csrf"]
        return client

    def post(client, path, body=None):
        return client.post(path, json=body or {},
                           headers={"X-CSRF-Token": client.environ_base["csrf"]})

    # --- signed out ------------------------------------------------------
    print("--- signed out ---")
    anon = server.app.test_client()
    check("API without a session is 401",
          anon.get("/api/evaluations/roles").status_code == 401)
    check("a page without a session redirects to the login form",
          anon.get("/evaluations.html",
                   headers={"Accept": "text/html"}).status_code == 302)
    check("the login form itself is served", anon.get("/login.html").status_code == 200)
    check("the dashboard's JS is not -- it is a map of every endpoint",
          anon.get("/evaluations.js").status_code in (302, 401))
    # An unknown address and a wrong password must be indistinguishable, or the
    # form becomes a way of asking whether somebody works here.
    unknown = anon.post("/api/auth/login",
                        json={"email": "nobody@nowhere.invalid", "password": "x"})
    wrong = anon.post("/api/auth/login", json={"email": ADMIN, "password": "x"})
    check("wrong password is 401", wrong.status_code == 401)
    check("an unknown address is refused in the same words",
          unknown.get_json()["error"] == wrong.get_json()["error"])

    admin = sign_in(ADMIN)
    manager = sign_in(manager_email)

    # --- what each account is given --------------------------------------
    print("\n--- what each account is given ---")
    as_admin = admin.get("/api/evaluations/roles").get_json()
    as_manager = manager.get("/api/evaluations/roles").get_json()
    check("the admin gets every role", len(as_admin["roles"]) == len(every),
          f"{len(as_admin['roles'])}/{len(every)}")
    check("the manager gets only the roles they are listed on",
          sorted(r["id"] for r in as_manager["roles"]) == owned,
          str(sorted(r["id"] for r in as_manager["roles"])))
    check("the manager is not told they are an admin",
          as_manager["is_admin"] is False)
    check("the manager gets no staff directory",
          as_manager["known_managers"] == [])
    check("the admin does", len(as_admin["known_managers"]) > 0)

    # --- a role they do not own ------------------------------------------
    print("\n--- a role they do not own ---")
    if not foreign:
        skip("every role-scoped route", "this manager owns every role")
    else:
        other = foreign[0]
        foreign_role(manager, admin, other, post)

    # --- a role they do own ----------------------------------------------
    print("\n--- a role they do own ---")
    ours = owned[0]
    check("their own role opens",
          manager.get(f"/api/evaluations/role/{ours}").status_code == 200)
    check("their own shortlist opens",
          manager.get(f"/api/shortlist/{ours}").status_code == 200)
    check("they can read who else owns it",
          manager.get(f"/api/roles/{ours}/managers").status_code == 200)
    # The one write that would defeat everything else: this list IS the rule.
    check("they cannot rewrite the hiring-manager list",
          post(manager, f"/api/roles/{ours}/managers",
               {"managers": [{"email": manager_email}]}).status_code == 403)
    check("they cannot redirect a shortlist send to an address they choose",
          post(manager, "/api/shortlist/send",
               {"job_id": ours, "to": ["somewhere@else.invalid"]}
               ).status_code == 403)
    check("they cannot set somebody else's booking link",
          post(manager, "/api/managers/cal-link",
               {"email": "not.them@ajaia.ai", "cal_link": "cal.com/x"}
               ).status_code == 403)

    # --- lists that span roles -------------------------------------------
    print("\n--- lists that span roles ---")
    board = manager.get("/api/pipeline").get_json()
    check("the board holds only their roles",
          all(row["job_id"] in owned for row in board["candidates"]),
          f"{len(board['candidates'])} rows")
    check("its totals are theirs, not the company's",
          sum(board["counts"]["stages"].values())
          <= sum(admin.get("/api/pipeline").get_json()["counts"]["stages"].values()))
    rejected = manager.get("/api/evaluations/rejected").get_json()
    check("the rejection list holds only their roles",
          all(row["job_id"] in owned for row in rejected["candidates"]),
          f"{rejected['total']} of "
          f"{admin.get('/api/evaluations/rejected').get_json()['total']}")

    # --- one candidate on somebody else's role ----------------------------
    print("\n--- one candidate on somebody else's role ---")
    stranger = None
    for job_id in foreign:
        rows = store.list_submissions(job_id=job_id, limit=1)
        if rows:
            stranger = rows[0]["_id"]
            break
    if stranger is None:
        skip("candidate-scoped routes", "no submissions on a role they do not own")
    else:
        check("their drawer is 404",
              manager.get(f"/api/evaluations/submission/{stranger}"
                          ).status_code == 404)
        check("their decision cannot be overridden",
              post(manager, "/api/evaluations/decision",
                   {"submission_id": stranger, "status": "pending"}
                   ).status_code == 404)
        check("they cannot be graded",
              post(manager, "/api/evaluations/grade",
                   {"submission_id": stranger}).status_code in (404, 503))
        check("they cannot be moved on the board",
              post(manager, "/api/pipeline",
                   {"submission_id": stranger, "stage": "rejected"}
                   ).status_code == 404)
        check("they cannot be emailed",
              post(manager, "/api/pipeline/send",
                   {"submission_id": stranger}).status_code == 404)
        check("the admin can still open them",
              admin.get(f"/api/evaluations/submission/{stranger}"
                        ).status_code == 200)

    # --- the machinery ----------------------------------------------------
    print("\n--- the machinery ---")
    for path in ("/api/state", "/api/logs"):
        check(f"a manager cannot read {path}",
              manager.get(path).status_code == 403)
        check(f"an admin can read {path}", admin.get(path).status_code != 403)
    check("a manager cannot start a reminder run",
          post(manager, "/api/run", {"mode": "preview"}).status_code == 403)
    check("a manager cannot re-crawl the portal",
          post(manager, "/api/evaluations/ingest").status_code == 403)
    check("a manager cannot list accounts",
          manager.get("/api/auth/users").status_code == 403)
    check("an admin can", admin.get("/api/auth/users").status_code == 200)
    check("a manager opening / is sent to their own dashboard",
          manager.get("/", headers={"Accept": "text/html"}
                      ).headers.get("Location", "").endswith("/evaluations.html"))
    check("an admin opening / gets the reminders dashboard",
          admin.get("/", headers={"Accept": "text/html"}).status_code == 200)

    # --- CSRF -------------------------------------------------------------
    print("\n--- CSRF ---")
    bare = server.app.test_client()
    bare.post("/api/auth/login", json={"email": ADMIN, "password": PASSWORD})
    body = {"submission_id": 1, "status": "pending"}
    check("a POST with no token is refused",
          bare.post("/api/evaluations/decision", json=body).status_code == 403)
    check("a POST with the wrong token is refused",
          bare.post("/api/evaluations/decision", json=body,
                    headers={"X-CSRF-Token": "not-it"}).status_code == 403)

    # --- sessions ---------------------------------------------------------
    print("\n--- sessions ---")
    check("signing out ends the session server-side",
          post(manager, "/api/auth/logout").status_code == 200
          and manager.get("/api/evaluations/roles").status_code == 401)

    open_tab = sign_in(manager_email)
    auth.update_user(manager_email, active=False)
    check("disabling an account cuts the tab it already had open",
          open_tab.get("/api/evaluations/roles").status_code == 401)
    auth.update_user(manager_email, active=True)

    auth.set_password(manager_email, PASSWORD, must_change=True)
    temporary = sign_in(manager_email)
    check("a temporary password cannot read anything",
          temporary.get("/api/evaluations/roles").status_code == 403)
    check("but can still reach the change-password screen",
          temporary.get("/api/auth/me").status_code == 200)

    # --- open redirect ----------------------------------------------------
    print("\n--- where ?next= may point ---")
    check("an absolute URL is refused",
          server._safe_next("https://evil.invalid") == "/")
    check("a protocol-relative URL is refused",
          server._safe_next("//evil.invalid") == "/")
    check("a real path survives",
          server._safe_next("/evaluations.html#role=29") == "/evaluations.html#role=29")

    print()
    if failures:
        print(f"{len(failures)} FAILED: " + "; ".join(failures))
        return 1
    print(f"All checks passed" + (f" ({skipped} skipped)" if skipped else ""))
    return 0


def foreign_role(manager, admin, other: int, post) -> None:
    """
    Every route that takes a job id, against a role the manager does not own.

    ADD TO THIS WHEN YOU ADD A ROUTE. A role-scoped endpoint with no guard is
    invisible everywhere else -- it looks like a working feature.
    """
    reads = {
        "the role itself": f"/api/evaluations/role/{other}",
        "its rubric": f"/api/evaluations/rubric/{other}",
        "its shortlist": f"/api/shortlist/{other}",
        "its spreadsheet": f"/api/shortlist/{other}/xlsx",
        "its review links": f"/api/shortlist/{other}/links",
        "who owns it": f"/api/roles/{other}/managers",
        "its board rows": f"/api/pipeline?job_id={other}",
        "its rejections": f"/api/evaluations/rejected?job_id={other}",
    }
    for what, path in reads.items():
        check(f"{what} is 404", manager.get(path).status_code == 404)

    check("grading it is refused",
          post(manager, "/api/evaluations/grade",
               {"job_id": other}).status_code in (404, 503))
    check("sending its shortlist is refused",
          post(manager, "/api/shortlist/send",
               {"job_id": other}).status_code == 404)

    # The whole point of 404 over 403: the refusal must not confirm the role.
    denied = manager.get(f"/api/evaluations/role/{other}").get_json()["error"]
    invented = manager.get("/api/evaluations/role/99999").get_json()["error"]
    check("a role they cannot see reads exactly like one that does not exist",
          denied.replace(str(other), "N") == invented.replace("99999", "N"),
          f"{denied!r} vs {invented!r}")

    check("the admin can open the same role",
          admin.get(f"/api/evaluations/role/{other}").status_code == 200)


if __name__ == "__main__":
    sys.exit(main())
