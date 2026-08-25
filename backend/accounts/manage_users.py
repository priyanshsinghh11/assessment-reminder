#!/usr/bin/env python3
"""
Dashboard accounts, from the terminal.

The dashboard has an accounts screen and it is the right place for day-to-day
work. This exists for the two moments that screen cannot cover:

  * the first account, before there is one to sign in with;
  * the locked-out admin, when the only way back in is the machine itself.

    python manage_users.py list
    python manage_users.py add priya@ajaia.ai --admin
    python manage_users.py add sam@ajaia.ai --name "Sam Okafor"
    python manage_users.py passwd sam@ajaia.ai
    python manage_users.py promote sam@ajaia.ai
    python manage_users.py demote sam@ajaia.ai
    python manage_users.py disable sam@ajaia.ai
    python manage_users.py remove sam@ajaia.ai
    python manage_users.py roles sam@ajaia.ai

A password typed as an argument lands in the shell history and in `ps`, so
`add` and `passwd` generate one and print it instead. It is printed ONCE --
from that moment it exists only as a PBKDF2 hash, and no screen anywhere can
show it again. Pass --password to type your own; you will be prompted for it
rather than passing it on the command line.

WHICH ROLES A MANAGER SEES IS NOT SET HERE. It is the hiring-manager list on
each role, in the dashboard -- the same list the shortlist email goes to. `add`
creates the account; putting them on a seat is what gives it anything to open.
Run `roles <email>` to see what an account can currently reach.
"""

import argparse
import getpass
import sys

from backend.accounts import auth
from backend.database import mongo_store as store
from backend.core.config import AUTH_ENABLED, PASSWORD_MIN_LENGTH


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _ask_password() -> str:
    """Read a password twice, off the terminal, never from argv."""
    print(f"At least {PASSWORD_MIN_LENGTH} characters. It is not echoed.")
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Again: ")
    if first != second:
        raise auth.AuthError("The two passwords do not match.")
    auth.check_password_policy(first)
    return first


def _show_password(address: str, password: str) -> None:
    print()
    print(f"  {address}")
    print(f"  password: {password}")
    print()
    print("  Shown once. Send it to them over something that is not email if "
          "you can,")
    print("  and they will be asked to change it the first time they sign in.")


def cmd_list(args) -> int:
    users = auth.list_users()
    if not users:
        print("No accounts yet. Create one:  python manage_users.py add "
              "--admin <email>")
        return 0
    width = max(len(u["email"]) for u in users)
    for user in users:
        flags = []
        if not user["active"]:
            flags.append("disabled")
        if user["must_change"]:
            flags.append("must change password")
        roles = ("all roles" if user["is_admin"]
                 else f"{len(user['roles'])} role(s)")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{user['email']:<{width}}  {user['role']:<7}  {roles}{suffix}")
    return 0


def cmd_add(args) -> int:
    role = "admin" if args.admin else "manager"
    try:
        password = _ask_password() if args.password else auth.generate_password()
        user, password = auth.create_user(
            args.email, name=args.name or "", role=role, password=password,
            title=args.title or "", must_change=True)
    except auth.AuthError as exc:
        return _fail(str(exc))

    _show_password(user["email"], password)
    if role == "manager":
        owned = store.roles_by_manager().get(user["email"], [])
        if owned:
            print(f"  Can open {len(owned)} role(s): "
                  f"{', '.join(r['title'] for r in owned[:4])}"
                  f"{' ...' if len(owned) > 4 else ''}")
        else:
            # The single most likely support question after creating one.
            print("  Not a hiring manager on any role yet, so they will sign "
                  "in to an empty")
            print("  dashboard. Add their address under Hiring managers on the "
                  "roles they own.")
    return 0


def cmd_passwd(args) -> int:
    # must_change either way, including a password the admin typed themselves:
    # a reset is done for somebody else, and a password two people know is not
    # that person's password. They replace it at their next sign-in.
    try:
        password = _ask_password() if args.password else auth.generate_password()
        auth.set_password(args.email, password, must_change=True)
    except auth.AuthError as exc:
        return _fail(str(exc))
    if args.password:
        print(f"Password set for {args.email}, and every session it had open "
              f"was signed out. They will be asked to change it at sign-in.")
    else:
        _show_password(args.email, password)
    return 0


def _set_role(email: str, role: str) -> int:
    try:
        user = auth.update_user(email, role=role)
    except auth.AuthError as exc:
        return _fail(str(exc))
    print(f"{user['email']} is now {user['role']}.")
    if role == "manager":
        print("Their sessions were ended, so the change applies to the tab "
              "they have open too.")
    return 0


def cmd_promote(args) -> int:
    return _set_role(args.email, "admin")


def cmd_demote(args) -> int:
    if auth.admin_count() <= 1 and auth.is_admin(auth.get_user(args.email)):
        return _fail("That is the last admin account. Promote somebody else "
                     "first, or nobody can get back in.")
    return _set_role(args.email, "manager")


def cmd_disable(args) -> int:
    if auth.admin_count() <= 1 and auth.is_admin(auth.get_user(args.email)):
        return _fail("That is the last admin account. Promote somebody else "
                     "first.")
    try:
        auth.update_user(args.email, active=False)
    except auth.AuthError as exc:
        return _fail(str(exc))
    print(f"{args.email} is disabled and signed out. Re-enable with: "
          f"python manage_users.py enable {args.email}")
    return 0


def cmd_enable(args) -> int:
    try:
        auth.update_user(args.email, active=True)
    except auth.AuthError as exc:
        return _fail(str(exc))
    print(f"{args.email} is enabled.")
    return 0


def cmd_remove(args) -> int:
    if auth.admin_count() <= 1 and auth.is_admin(auth.get_user(args.email)):
        return _fail("That is the last admin account. Promote somebody else "
                     "first.")
    if not auth.delete_user(args.email):
        return _fail(f"No account for {args.email}.")
    print(f"Removed {args.email}.")
    return 0


def cmd_roles(args) -> int:
    """What this account can actually open, read the way the server reads it."""
    user = auth.get_user(args.email)
    if user is None:
        return _fail(f"No account for {args.email}.")
    if auth.is_admin(user):
        print(f"{args.email} is an admin: every role, plus portal sync, "
              f"reminder sends and accounts.")
        return 0
    owned = store.roles_by_manager().get(user["_id"], [])
    if not owned:
        print(f"{args.email} is not a hiring manager on any role, so they see "
              f"nothing.")
        print("Add their address under Hiring managers on the roles they own.")
        return 0
    print(f"{args.email} can open {len(owned)} role(s):")
    for role in owned:
        print(f"  {role['id']:>5}  {role['title']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create and manage dashboard accounts.",
        epilog="Which roles a manager sees is set by the hiring-manager list "
               "on each role in the dashboard, not here.")
    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser("list", help="every account and what it can reach")

    add = subs.add_parser("add", help="create an account")
    add.add_argument("email")
    add.add_argument("--admin", action="store_true",
                     help="recruiting team: every role, plus the machinery")
    add.add_argument("--name", default="")
    add.add_argument("--title", default="")
    add.add_argument("--password", action="store_true",
                     help="type a password instead of generating one")

    passwd = subs.add_parser("passwd", help="reset somebody's password")
    passwd.add_argument("email")
    passwd.add_argument("--password", action="store_true",
                        help="type it instead of generating one")

    for name, help_text in (("promote", "make an account an admin"),
                            ("demote", "make an admin a manager"),
                            ("disable", "sign out and lock an account"),
                            ("enable", "let a disabled account back in"),
                            ("remove", "delete an account"),
                            ("roles", "what an account can currently open")):
        sub = subs.add_parser(name, help=help_text)
        sub.add_argument("email")

    args = parser.parse_args()

    if not AUTH_ENABLED:
        # Not an error -- accounts can be prepared before the switch is
        # flipped -- but silence here would be the wrong kind of quiet.
        print("note: AUTH_ENABLED=0, so the dashboard is not asking for any "
              "of these yet.\n", file=sys.stderr)

    try:
        store.ping()
    except store.MongoUnavailable as exc:
        return _fail(str(exc))
    auth.ensure_indexes()

    handlers = {
        "list": cmd_list, "add": cmd_add, "passwd": cmd_passwd,
        "promote": cmd_promote, "demote": cmd_demote, "disable": cmd_disable,
        "enable": cmd_enable, "remove": cmd_remove, "roles": cmd_roles,
    }
    try:
        return handlers[args.command](args)
    except auth.AuthError as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
