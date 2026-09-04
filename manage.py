#!/usr/bin/env python3
"""
Every command this project has, behind one name.

    python manage.py <command> [options]
    python manage.py                      # the list below
    python manage.py grade --help         # a command's own options

THIS REPLACED TWELVE FILES. There used to be a launcher at the repository root
per command -- grade.py, ingest.py, reminder.py, and nine more -- each one
seventeen lines of docstring around a single `from ... import main`. Twelve
files is what the root directory looked like before you got to a README, and
none of them held any logic. They are one dispatch table now.

THE COMMAND IS THE ONLY THING THAT CHANGED. Every flag is the flag it was:
`python manage.py reminder --dry-run` runs exactly what `python manage.py reminder
--dry-run` ran. Each command below still owns its own argparse, so `--help`
after a command is that command's help, not this one's, and this module knows
nothing about any of their options.

WHY IT DISPATCHES BY IMPORTING LATE. The import is inside the branch, not at
the top of the file. Importing all eleven up front would pull in Mongo, the
Workable client, the LLM stack and Flask to answer `python manage.py --help`,
and one unconfigured credential anywhere in that graph would turn the help text
into a traceback. Nothing is imported until a command is chosen.

ONE OF THE TWELVE CAME BACK, AND ONLY ONE. server.py is at the root again,
because `serve` is not like the others: it is typed every day rather than
occasionally from notes, it is the first thing run in a fresh terminal, and it
is the only command whose output somebody sits and watches. The other eleven
stay here. `manage.py serve` still works and runs the same main().

RUNNING A MODULE DIRECTLY STILL WORKS -- `python -m backend.pipeline.grade
--job 33` is the same main(). This file is the short way to say it, not a
wrapper around it.
"""

import sys

# command -> (module path, one-line summary). The module's main() takes no
# arguments and reads sys.argv, which is why _run rewrites sys.argv rather than
# passing anything in.
COMMANDS = {
    "serve": ("backend.web.server",
              "run the dashboard -- or just `python server.py`"),
    "reminder": ("backend.mail.reminder",
                 "the assessment nudge: scan, cross-reference, send"),
    "ingest": ("backend.pipeline.ingest",
               "portal -> MongoDB, plus the missing-artefact screen"),
    "grade": ("backend.pipeline.grade",
              "grade a role, or the whole backlog"),
    "regrade": ("backend.pipeline.regrade",
                "re-score submissions that already carry a verdict"),
    "calibrate": ("backend.pipeline.calibrate",
                  "is the grader using the scale, or just spotting gaps?"),
    "cv-role": ("backend.pipeline.cv_role",
                "ingest and grade a posting that has no assessment"),
    "users": ("backend.manage_users",
              "dashboard accounts: the first admin, and the way back in"),
    "migrate-db": ("tools.migrate_db",
                   "copy an older database into the one MONGO_DB names"),
    "migrate-reminder-log": ("tools.migrate_reminder_log",
                             "move state/reminder_log.json into MongoDB"),
    "test-access": ("tests.test_access",
                    "the access-rule regression suite (needs a real database)"),
}


def _usage() -> int:
    print(__doc__.strip().split("\n\n")[0])
    print()
    print("Commands:")
    width = max(len(name) for name in COMMANDS)
    for name, (_, summary) in COMMANDS.items():
        print(f"  {name.ljust(width)}  {summary}")
    print()
    print("  python manage.py <command> --help   for a command's own options")
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        return _usage()

    name = sys.argv[1]
    if name not in COMMANDS:
        print(f"manage.py: unknown command {name!r}\n", file=sys.stderr)
        _usage()
        return 2

    module_path = COMMANDS[name][0]

    # The command's own argparse reads sys.argv, and it must not see the
    # subcommand -- `manage.py grade --job 33` has to look to grade.main()
    # exactly like `grade.py --job 33` did. argv[0] carries both words so its
    # usage line prints the command a person can actually retype.
    sys.argv = [f"{sys.argv[0]} {name}"] + sys.argv[2:]

    module = __import__(module_path, fromlist=["main"])
    return module.main() or 0


if __name__ == "__main__":
    sys.exit(main())
