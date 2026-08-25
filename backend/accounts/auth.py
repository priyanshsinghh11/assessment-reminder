"""
Dashboard accounts, sessions and what each account is allowed to see.

WHY THIS EXISTS. The dashboard was one undivided surface: whoever reached it
read every role, every candidate's email address, and every button that mails
hundreds of real people. That is the right shape for the recruiting team, who
own the whole funnel, and the wrong shape for a hiring manager, who owns one
seat. A manager opening the page for their own shortlist should not be able to
read another team's pipeline, and should not have to be trusted not to.

So there are two kinds of account:

    admin     the recruiting team. Every role, plus the machinery.
    manager   ONLY the roles whose `hiring_managers` list carries their
              address. Everything else is a 403 -- and the roles endpoint does
              not mention those roles at all, so there is nothing to guess at.

A MANAGER'S ROLES ARE NOT STORED ON THEIR ACCOUNT. They are derived, on every
request, from the per-role hiring-manager list the recruiter already maintains
-- the same list the shortlist email goes to. One place to say who owns a seat
means access can never drift away from ownership: take somebody off a role and
they lose the role in that same click, with no second screen to remember. See
`visible_job_ids()`, which is the whole rule.

Passwords are PBKDF2-HMAC-SHA256 out of the standard library -- no new
dependency for the one thing in this repo that must not be improvised. The
session token is `secrets.token_urlsafe`, and only its SHA-256 is stored, so a
dump of the sessions collection is a list of expired-looking hashes rather than
a drawer full of live keys.
"""

import base64
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo import ASCENDING

from backend.core.config import (
    AUTH_ENABLED,
    LOGIN_IP_MAX_ATTEMPTS,
    LOGIN_IP_WINDOW_MINUTES,
    LOGIN_LOCKOUT_MINUTES,
    LOGIN_MAX_ATTEMPTS,
    PASSWORD_MIN_LENGTH,
    PORTAL_ADMIN_PASSWORD,
    PORTAL_ADMINS,
    SESSION_IDLE_HOURS,
    SESSION_TTL_HOURS,
)
from backend.database import mongo_store as store

log = logging.getLogger(__name__)

ROLES = ("admin", "manager")

# Cost of one password check. High enough to make an offline attack on a stolen
# hash expensive, low enough that a login is not a visible pause.
PBKDF2_ITERATIONS = 240_000


class AuthError(RuntimeError):
    """A login or account change that cannot be allowed, with a sayable reason."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value) -> Optional[datetime]:
    """Mongo hands back naive UTC; comparisons here are all tz-aware."""
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def ensure_indexes() -> None:
    """Indexes for the two collections this module owns. Idempotent."""
    db = store.get_db()
    db.users.create_index([("role", ASCENDING)])
    db.sessions.create_index([("email", ASCENDING)])
    # Sessions are checked on every single request, and dead ones are swept by
    # Mongo itself rather than by a cron job somebody has to remember to keep
    # running. expireAfterSeconds=0 means "when the date in this field passes".
    try:
        db.sessions.create_index([("expires_at", ASCENDING)],
                                 expireAfterSeconds=0)
    except Exception as exc:                       # standalone without TTL support
        log.warning("No TTL index on sessions (%s); expiry is still enforced "
                    "on read.", exc)

    # Failed sign-ins, per source address. Swept by Mongo the same way sessions
    # are -- the window is enforced on read as well, so a missing TTL index
    # leaves stale rows behind but never widens the throttle.
    db.login_attempts.create_index([("ip", ASCENDING)])
    try:
        db.login_attempts.create_index([("expires_at", ASCENDING)],
                                       expireAfterSeconds=0)
    except Exception as exc:
        log.warning("No TTL index on login_attempts (%s); the window is still "
                    "enforced on read.", exc)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """
    "pbkdf2_sha256$<iterations>$<salt>$<hash>", the format Django popularised.

    The parameters travel with the hash rather than living in a constant, so
    raising PBKDF2_ITERATIONS later keeps every existing password verifiable
    instead of locking out everyone who set theirs before the change.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 PBKDF2_ITERATIONS)
    return "$".join([
        "pbkdf2_sha256",
        str(PBKDF2_ITERATIONS),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    ])


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of `password` against a hash from hash_password()."""
    try:
        scheme, iterations, salt_b64, digest_b64 = str(stored).split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest_b64)
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            base64.b64decode(salt_b64), int(iterations))
    except (ValueError, AttributeError, TypeError):
        # A malformed or missing hash is a failed login, never an exception
        # that a caller could mistake for a server fault and retry past.
        return False
    return hmac.compare_digest(candidate, expected)


def check_password_policy(password: str) -> None:
    """Raise AuthError unless `password` is long enough and not obvious."""
    password = str(password or "")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise AuthError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    # Length alone lets "passwordpassword" through, and that is the one a
    # person picks when the only rule they were given was a number.
    flat = re.sub(r"[^a-z]", "", password.lower())
    if flat and flat in ("password" * 6, "qwerty" * 6, "abcdefghijklmnop"):
        raise AuthError("Password is too easy to guess. Pick another.")
    if len(set(password)) < 5:
        raise AuthError("Password repeats too few characters. Pick another.")


def generate_password() -> str:
    """A temporary password for an account an admin is creating for someone."""
    return secrets.token_urlsafe(12)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def _clean_email(email) -> str:
    address = str(email or "").strip().lower()
    if "@" not in address or " " in address:
        raise AuthError(f"Not an email address: {email!r}")
    return address


def public_user(user: Optional[dict]) -> Optional[dict]:
    """
    The account as the browser is allowed to see it -- never the hash.

    Built by naming the fields that go out rather than by deleting the ones
    that must not: a field added to the document later is private by default,
    which is the direction a mistake here should fail in.
    """
    if not user:
        return None
    return {
        "email": user["_id"],
        "name": user.get("name") or user["_id"].split("@")[0],
        "title": user.get("title") or "",
        "role": user.get("role") or "manager",
        "is_admin": (user.get("role") or "manager") == "admin",
        "active": bool(user.get("active", True)),
        "must_change": bool(user.get("must_change")),
        "last_login_at": (_aware(user.get("last_login_at")).isoformat()
                          if user.get("last_login_at") else None),
        "created_at": (_aware(user.get("created_at")).isoformat()
                       if user.get("created_at") else None),
    }


def get_user(email: str) -> Optional[dict]:
    try:
        address = _clean_email(email)
    except AuthError:
        return None
    return store.get_db().users.find_one({"_id": address})


def list_users() -> list[dict]:
    """Every account, with the roles each manager can currently reach."""
    owned = store.roles_by_manager()
    users = []
    for user in store.get_db().users.find().sort("_id", ASCENDING):
        entry = public_user(user)
        entry["roles"] = ([] if entry["is_admin"]
                          else owned.get(entry["email"], []))
        users.append(entry)
    return users


def create_user(email: str, name: str = "", role: str = "manager",
                password: str = "", title: str = "",
                must_change: bool = True) -> tuple[dict, str]:
    """
    Create an account. Returns (public user, password) -- the password is
    returned because a generated one is shown to the admin exactly once and
    then only ever exists as a hash.
    """
    address = _clean_email(email)
    if role not in ROLES:
        raise AuthError(f"Unknown account type: {role}")
    if get_user(address) is not None:
        raise AuthError(f"{address} already has an account.")

    password = password or generate_password()
    check_password_policy(password)

    store.get_db().users.insert_one({
        "_id": address,
        "name": str(name or "").strip() or address.split("@")[0].replace(".", " ").title(),
        "title": str(title or "").strip(),
        "role": role,
        "password": hash_password(password),
        "active": True,
        "must_change": bool(must_change),
        "created_at": now(),
        "updated_at": now(),
        "last_login_at": None,
        "failed": 0,
        "locked_until": None,
    })
    log.info("Created %s account for %s", role, address)
    return public_user(get_user(address)), password


def set_password(email: str, password: str, must_change: bool = False) -> None:
    """
    Replace an account's password and end every session it has open.

    The sign-out is the point. A password is changed either because it leaked
    or because an admin is handing the account back to its owner, and both mean
    whoever is holding an old cookie should stop being logged in.
    """
    address = _clean_email(email)
    check_password_policy(password)
    result = store.get_db().users.update_one(
        {"_id": address},
        {"$set": {"password": hash_password(password),
                  "must_change": bool(must_change),
                  "failed": 0, "locked_until": None,
                  "updated_at": now()}},
    )
    if not result.matched_count:
        raise AuthError(f"No account for {address}.")
    end_all_sessions(address)


def change_own_password(email: str, current: str, new: str) -> None:
    """A person changing their own password, which needs the old one."""
    user = get_user(email)
    if user is None:
        raise AuthError("No such account.")
    if not verify_password(current, user.get("password") or ""):
        raise AuthError("Current password is not right.")
    if current == new:
        raise AuthError("New password must be different from the old one.")
    set_password(email, new, must_change=False)


def update_user(email: str, name=None, title=None, role=None,
                active=None) -> dict:
    """Edit an account's profile. Password changes go through set_password()."""
    address = _clean_email(email)
    user = get_user(address)
    if user is None:
        raise AuthError(f"No account for {address}.")

    fields: dict = {"updated_at": now()}
    if name is not None:
        fields["name"] = str(name).strip() or user.get("name")
    if title is not None:
        fields["title"] = str(title).strip()
    if role is not None:
        if role not in ROLES:
            raise AuthError(f"Unknown account type: {role}")
        fields["role"] = role
    if active is not None:
        fields["active"] = bool(active)

    store.get_db().users.update_one({"_id": address}, {"$set": fields})

    # Demoting or disabling somebody has to reach the browser they already have
    # open, not just the next time they log in. Their sessions carry no copy of
    # their permissions -- see session_user() -- but ending them is what makes
    # "you are not an admin any more" true on the current screen too.
    if fields.get("active") is False or fields.get("role") == "manager":
        end_all_sessions(address)
    return public_user(get_user(address))


def delete_user(email: str) -> bool:
    """Remove an account outright, and every session it holds."""
    address = _clean_email(email)
    end_all_sessions(address)
    return bool(store.get_db().users.delete_one({"_id": address}).deleted_count)


def admin_count() -> int:
    return store.get_db().users.count_documents({"role": "admin",
                                                 "active": True})


def seed_admins() -> None:
    """
    Create the bootstrap admins named in PORTAL_ADMINS, if they are missing.

    The chicken-and-egg fix: accounts are created from the dashboard, and the
    dashboard needs an account to open. Runs on every start and is a no-op
    after the first -- an address that already exists is left exactly as it is,
    so this never resets a password somebody changed or re-promotes an account
    that was deliberately demoted.
    """
    if not PORTAL_ADMINS:
        return
    if not PORTAL_ADMIN_PASSWORD:
        if not admin_count():
            log.warning(
                "PORTAL_ADMINS is set but PORTAL_ADMIN_PASSWORD is not, and "
                "there is no admin account yet. Nobody can log in. Set both in "
                ".env for one start, or run: python manage_users.py add "
                "--admin <email>")
        return
    for address in PORTAL_ADMINS:
        if get_user(address) is not None:
            continue
        try:
            create_user(address, role="admin",
                        password=PORTAL_ADMIN_PASSWORD, must_change=True)
            log.info("Bootstrap admin created for %s -- it must change its "
                     "password at first login.", address)
        except AuthError as exc:
            log.error("Could not create bootstrap admin %s: %s", address, exc)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
#
# The cookie holds a random token; Mongo holds its SHA-256. A read of the
# sessions collection therefore hands over nothing that can be pasted into a
# browser. There is no signed cookie carrying the account's role either: a
# session is a pointer to the account, and the account is re-read on every
# request, so revoking an admin takes effect on the next click rather than
# whenever their cookie happens to expire.

def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def start_session(email: str, ip: str = "", agent: str = "") -> tuple[str, str]:
    """Open a session for an account. Returns (session token, CSRF token)."""
    address = _clean_email(email)
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    store.get_db().sessions.insert_one({
        "_id": _token_hash(token),
        "email": address,
        "csrf": csrf,
        "created_at": now(),
        "last_seen": now(),
        "expires_at": now() + timedelta(hours=SESSION_TTL_HOURS),
        "ip": str(ip or "")[:64],
        "agent": str(agent or "")[:200],
    })
    return token, csrf


def session_user(token: str) -> Optional[dict]:
    """
    The live account behind a session cookie, or None.

    None covers every way a session can be no good -- unknown, expired, idle
    too long, or belonging to an account that has since been disabled or
    deleted -- because the caller's answer to all of them is the same: send
    them to the login page.
    """
    if not token:
        return None
    db = store.get_db()
    session = db.sessions.find_one({"_id": _token_hash(token)})
    if session is None:
        return None

    moment = now()
    expires = _aware(session.get("expires_at"))
    seen = _aware(session.get("last_seen")) or _aware(session.get("created_at"))
    idle_cutoff = moment - timedelta(hours=SESSION_IDLE_HOURS)
    if (expires and moment >= expires) or (seen and seen < idle_cutoff):
        db.sessions.delete_one({"_id": session["_id"]})
        return None

    user = get_user(session["email"])
    if user is None or not user.get("active", True):
        db.sessions.delete_one({"_id": session["_id"]})
        return None

    # Written back only about once a minute. The idle clock needs a recent
    # timestamp; it does not need a write on every poll of a dashboard that
    # refreshes itself.
    if not seen or (moment - seen) > timedelta(minutes=1):
        db.sessions.update_one({"_id": session["_id"]},
                               {"$set": {"last_seen": moment}})

    user["_session"] = session
    return user


def end_session(token: str) -> None:
    if token:
        store.get_db().sessions.delete_one({"_id": _token_hash(token)})


def end_all_sessions(email: str) -> int:
    address = str(email or "").strip().lower()
    return store.get_db().sessions.delete_many({"email": address}).deleted_count


def list_sessions(email: str) -> list[dict]:
    """Open sessions for one account, for the admin screen. No tokens."""
    rows = []
    for session in store.get_db().sessions.find(
            {"email": str(email or "").strip().lower()}):
        rows.append({
            "created_at": _aware(session["created_at"]).isoformat(),
            "last_seen": (_aware(session.get("last_seen")).isoformat()
                          if session.get("last_seen") else None),
            "ip": session.get("ip") or "",
            "agent": session.get("agent") or "",
        })
    return rows


# ---------------------------------------------------------------------------
# Logging in
# ---------------------------------------------------------------------------

class RateLimited(AuthError):
    """
    Too many failed sign-ins from one source address.

    A distinct type rather than a plain AuthError because the caller answers it
    with 429 and a Retry-After, and because it must NOT be reported the way a
    wrong password is: it says nothing about whether the account exists, only
    that this source has been asked to stop.
    """

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


def _throttle_key(ip: str) -> str:
    """The bucket a failed attempt counts against. Blank collapses to one."""
    return (ip or "").strip() or "unknown"


def check_ip_throttle(ip: str) -> None:
    """
    Refuse this source if it has already failed too many times in the window.

    RUNS BEFORE THE PASSWORD IS CHECKED, and that ordering is the entire point.
    An attempt refused here never reaches the account, so it never increments
    the account's `failed` counter and never contributes to locking a real
    person out. Without that ordering the throttle would merely be a second
    place the denial of service gets recorded.

    Fails OPEN on a database error. A throttle is a rate limit, not the lock --
    the password check behind it is the lock -- and taking sign-in down for
    everyone because the counter could not be read is the worse failure.
    """
    window_start = now() - timedelta(minutes=LOGIN_IP_WINDOW_MINUTES)
    try:
        recent = store.get_db().login_attempts.count_documents({
            "ip": _throttle_key(ip),
            "at": {"$gte": window_start},
        })
    except Exception as exc:
        log.warning("Could not read the login throttle (%s); allowing.", exc)
        return

    if recent >= LOGIN_IP_MAX_ATTEMPTS:
        log.warning("Throttled sign-in from %s: %d failures in %d minutes.",
                    _throttle_key(ip), recent, LOGIN_IP_WINDOW_MINUTES)
        raise RateLimited(
            "Too many sign-in attempts from this address. Wait a few minutes "
            "and try again.",
            retry_after=LOGIN_IP_WINDOW_MINUTES * 60)


def record_ip_failure(ip: str) -> None:
    """One failed attempt, kept for the length of the window and then swept."""
    moment = now()
    try:
        store.get_db().login_attempts.insert_one({
            "ip": _throttle_key(ip),
            "at": moment,
            "expires_at": moment + timedelta(minutes=LOGIN_IP_WINDOW_MINUTES),
        })
    except Exception as exc:
        log.warning("Could not record a failed sign-in from %s: %s",
                    _throttle_key(ip), exc)


def clear_ip_failures(ip: str) -> None:
    """
    Forget this source's failures after a success.

    So the colleague who mistyped their password four times, then got it right,
    is not still carrying those four against them for the rest of the window --
    and so an office behind one NAT address does not accumulate everybody's
    typos into a shared lockout.
    """
    try:
        store.get_db().login_attempts.delete_many({"ip": _throttle_key(ip)})
    except Exception as exc:
        log.warning("Could not clear failed sign-ins for %s: %s",
                    _throttle_key(ip), exc)


def login(email: str, password: str, ip: str = "",
          agent: str = "") -> tuple[dict, str, str]:
    """
    Check a password and open a session. Returns (public user, token, csrf).

    Raises AuthError with a message that is the SAME for an unknown address and
    a wrong password. Saying which one was wrong turns the login form into a
    way of asking whether a given person works here.
    """
    generic = AuthError("That email and password do not match an account.")

    # BEFORE the address is even resolved. Everything below this line either
    # touches an account or reveals timing about one, and an attempt from a
    # source that has already exhausted the window should do neither.
    check_ip_throttle(ip)

    try:
        address = _clean_email(email)
    except AuthError:
        record_ip_failure(ip)
        raise generic from None

    user = store.get_db().users.find_one({"_id": address})
    if user is None:
        # Spend roughly what a real check costs, so the reply to an unknown
        # address is not visibly faster than the reply to a wrong password.
        verify_password(str(password or ""), hash_password("timing-floor"))
        record_ip_failure(ip)
        raise generic

    locked_until = _aware(user.get("locked_until"))
    if locked_until and now() < locked_until:
        minutes = max(1, int((locked_until - now()).total_seconds() // 60) + 1)
        raise AuthError(
            f"Too many failed attempts. Try again in {minutes} minute(s), or "
            f"ask an admin to reset your password.")

    if not user.get("active", True):
        raise AuthError("That account has been disabled. Ask an admin.")

    if not verify_password(str(password or ""), user.get("password") or ""):
        record_ip_failure(ip)

        # THE COUNTER DECAYS. It used to be a lifetime tally that only ever
        # reset on a successful sign-in, which had two consequences. A person
        # who mistyped their password four times last month walked around at
        # four-of-eight for ever, and reached a lockout on their fourth typo
        # today. And an attacker throttled down to a handful of attempts per
        # window could still accumulate across windows until the account
        # locked -- slower, but the same denial of service, because nothing
        # ever gave the count back.
        #
        # Counting only failures inside one window makes both go away: a real
        # person's old typos expire, and a source that has been throttled below
        # the lockout threshold can never reach it however long it keeps
        # trying. The lockout is left to answer the case it is for, which is
        # many addresses failing against one account at once.
        last_failed = _aware(user.get("last_failed_at"))
        within_window = (
            last_failed is not None
            and now() - last_failed < timedelta(minutes=LOGIN_LOCKOUT_MINUTES))
        failed = (int(user.get("failed") or 0) + 1) if within_window else 1

        fields: dict = {"failed": failed, "last_failed_at": now()}
        if failed >= LOGIN_MAX_ATTEMPTS:
            fields["locked_until"] = now() + timedelta(
                minutes=LOGIN_LOCKOUT_MINUTES)
            fields["failed"] = 0
            log.warning("Locked %s after %s failed logins in %s minutes "
                        "(most recent from %s)",
                        address, failed, LOGIN_LOCKOUT_MINUTES,
                        ip or "unknown")
        store.get_db().users.update_one({"_id": address}, {"$set": fields})
        raise generic

    store.get_db().users.update_one(
        {"_id": address},
        {"$set": {"failed": 0, "locked_until": None, "last_failed_at": None,
                  "last_login_at": now()}},
    )
    clear_ip_failures(ip)
    token, csrf = start_session(address, ip=ip, agent=agent)
    log.info("Login: %s (%s) from %s", address, user.get("role"),
             ip or "unknown")
    return public_user(get_user(address)), token, csrf


# ---------------------------------------------------------------------------
# What an account can see
# ---------------------------------------------------------------------------

def is_admin(user: Optional[dict]) -> bool:
    return bool(user) and (user.get("role") or "manager") == "admin"


def visible_job_ids(user: Optional[dict]) -> Optional[set[int]]:
    """
    The roles this account may touch. None means "all of them".

    None rather than a set-of-everything on purpose: an admin's answer is not a
    list that could go stale between the check and the query, it is the absence
    of a filter. Every caller reads it the same way --

        allowed = visible_job_ids(user)
        if allowed is not None and job_id not in allowed: 403

    -- so a route that forgets the `is not None` half fails closed for admins
    (they see nothing) rather than open for managers, which is the direction a
    mistake here has to fall.

    For a manager the answer is computed from the roles collection on every
    call, never cached and never stored on the account. Removing them from a
    role's hiring-manager list removes their access with the same click.
    """
    if not AUTH_ENABLED:
        return None
    if is_admin(user):
        return None
    if not user:
        return set()
    return store.job_ids_for_manager(user["_id"])


def can_see_job(user: Optional[dict], job_id) -> bool:
    allowed = visible_job_ids(user)
    if allowed is None:
        return True
    try:
        return int(job_id) in allowed
    except (TypeError, ValueError):
        return False
