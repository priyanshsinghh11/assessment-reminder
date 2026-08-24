#!/usr/bin/env python3
"""
Grade one named submission, rather than a whole role.

    python regrade.py 6998            grade it
    python regrade.py 6998 --dry-run  print the prompt, call nothing

grade.py works a role at a time, which is right for a queue and wrong for the
single candidate a reviewer is arguing about: job 33 holds 76 ungraded rows, so
re-marking one of them the usual way costs 76 gradings, and --limit takes the
oldest rather than the one you meant.

--dry-run is the cheap half of this. It builds the exact prompt evaluate()
would send -- grid, artefacts, CV and all -- and prints it instead of spending
the tokens, which is the fastest way to see what the model is actually being
shown about a candidate.
"""

import argparse
import logging
import sys

import evaluator
import mongo_store as store
from reminder import setup_logging

log = logging.getLogger("regrade")


class _Captured(Exception):
    """Carries the prompt out of the stubbed _chat under --dry-run."""

    def __init__(self, prompt: str):
        self.prompt = prompt


def _dry_run(submission: dict, role: dict, grid: dict) -> int:
    real_chat = evaluator._chat

    def capture(messages, **kwargs):
        raise _Captured(messages[0]["content"])

    evaluator._chat = capture
    try:
        evaluator.evaluate(submission, role, grid)
    except _Captured as captured:
        print(captured.prompt)
        return 0
    except evaluator.EvaluationFailed as exc:
        log.error("%s", exc)
        return 2
    finally:
        evaluator._chat = real_chat
    log.error("evaluate() returned without calling the model.")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("submission_id", type=int)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the prompt instead of grading")
    parser.add_argument("--force-rubric", action="store_true",
                        help="regenerate the role's rubric first")
    args = parser.parse_args()

    setup_logging()

    if not args.dry_run and not evaluator.is_configured():
        log.error("No LLM credentials. Set LLM_API_KEY in .env.")
        return 1

    try:
        store.ping()
    except store.MongoUnavailable as exc:
        log.error("%s", exc)
        return 1

    submission = store.get_submission(args.submission_id)
    if submission is None:
        log.error("No submission %s.", args.submission_id)
        return 1

    role = store.get_role(submission.get("job_id"))
    if role is None:
        log.error("Submission %s belongs to job %s, which is not in the "
                  "database. Run `python ingest.py --roles-only`.",
                  args.submission_id, submission.get("job_id"))
        return 1

    name = (submission.get("candidate_name")
            or submission.get("candidate_email") or "?")

    # Said out loud because this command exists to re-mark an already-scored
    # candidate, and overwriting a verdict someone is mid-argument about
    # without mentioning it is how the argument gets lost.
    existing = submission.get("evaluation") or {}
    if existing:
        log.info("%s already scored %.1f (%s) -- regrading overwrites it.",
                 name, existing.get("score", 0), existing.get("recommendation"))

    # Resolved for this one candidate rather than for the role, because a role
    # whose two postings are graded at different tiers has no single grid and a
    # dry run has to print the anchors this candidate will actually be marked
    # against.
    grid = evaluator.grid_for_submission(
        submission, role, evaluator.derive_grid(role, force=args.force_rubric),
    )
    tier = grid.get("tier")
    if tier:
        log.info("Marking against the %s tier (%s).", tier, grid.get("key"))

    if args.dry_run:
        return _dry_run(submission, role, grid)

    log.info("Grading %s (submission %s, %s) against the %s grid...",
             name, args.submission_id, role.get("title"), grid.get("unit"))
    try:
        verdict = evaluator.evaluate_and_store(submission, role, grid)
    except evaluator.QuotaExhausted as exc:
        log.warning("Daily token budget reached. %s", exc)
        return 3
    except evaluator.EvaluationFailed as exc:
        log.error("Evaluation failed: %s", exc)
        return 2

    grounding = verdict.get("grounding") or {}
    triage = verdict.get("triage") or {}
    print(f"\n{name}  ->  {verdict['score']:.1f}  {verdict['recommendation']}")

    # The split, whenever the CV carries weight. A reviewer looking at a score
    # needs to know which half produced it -- a 63 built from a weak answer and
    # a strong CV is a different candidate from a 63 built the other way round.
    weight = verdict.get("cv_weight") or 0
    if weight:
        cv = verdict.get("cv_assessment") or {}
        rubric = verdict.get("rubric_score", verdict["score"])
        # Named, because the split is this seat's rather than the company's
        # since 2026-08-15 and a run that regrades two families side by side
        # would otherwise look inconsistent.
        source = verdict.get("cv_weight_source", "default")
        print(f"  split {1-weight:.0%}/{weight:.0%} assessment/experience"
              f"{' (fallback -- seat not weighted)' if source == 'default' else ''}")
        print(f"  rubric {rubric:.1f} x {1-weight:.0%}", end="")
        if verdict.get("cv_applied"):
            print(f"  +  CV {cv['score']:.1f} x {weight:.0%}")
        elif verdict.get("cv_unmarked"):
            print("  +  CV NOT MARKED by the grader -- the CV was readable and "
                  "sent. Scored on the rubric alone; nothing forfeited.")
        else:
            print(f"  +  CV forfeited ({verdict.get('cv_missing_policy')}) "
                  f"-- no readable CV, ceiling {(1-weight)*100:.0f}")

    print(f"  triage {triage.get('passed')}/{triage.get('of')}   "
          f"quotes verified {grounding.get('verified')}/{grounding.get('checked')}")
    print(f"  {verdict['brief']}\n")

    for row in verdict.get("grid") or []:
        flag = {True: "ok ", False: "UNEVIDENCED", None: "-  "}[row.get("grounded")]
        print(f"  {row['key']:<20} {str(row.get('score')):>2}/5  "
              f"{row.get('points', 0):>5.1f} pts  {flag}")
        if row.get("missing") and row.get("missing") != "nothing":
            print(f"      missing: {str(row['missing'])[:110]}")

    cv_marks = verdict.get("cv_assessment") or {}
    if weight and cv_marks.get("criteria"):
        print(f"\n  CV ({cv_marks['score'] if cv_marks.get('scored') else 'not scored'})")
        for row in cv_marks["criteria"]:
            print(f"  {row['key']:<20} {str(row.get('score')):>2}/5   "
                  f"{str(row.get('evidence'))[:80]}")

    cv = verdict.get("cv_check") or {}
    if cv.get("verdict"):
        print(f"\n  cv_check: {cv['verdict']} -- {cv.get('note', '')}")
    for finding in verdict.get("auto_fails") or []:
        print(f"  AUTO-FAIL {finding.get('rule')}: {finding.get('evidence')}")
    for finding in verdict.get("fraud_tells") or []:
        print(f"  FRAUD {finding.get('tell')}: {finding.get('evidence')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
