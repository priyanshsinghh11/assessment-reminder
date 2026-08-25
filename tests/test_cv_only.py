#!/usr/bin/env python3
"""
Regression test for the CV-only grading path.

    python -m tests.test_cv_only

WHY THIS FILE. Grading a role with no assessment reuses `_parse_verdict`
wholesale, which is what keeps a CV-only card comparable with every other card
in the system. The price of that reuse is a handful of arguments that have to
be right, and every one of them fails SILENTLY -- a wrong value here produces a
well-formed verdict with a plausible number on it, and the only symptom is that
the number is wrong.

The four that matter, and what each one costs when it drifts:

  every row in the background block   Any row outside it grounds its quote
                                      against an assessment answer that does
                                      not exist, and reports unevidenced by
                                      construction.
  has_cv=True                         Arms the no-CV floor, which raises any
                                      background row below 3. On a grid where
                                      every row is a background row, that is a
                                      rule which lifts every weak CV to a pass.
  cv_weight 0.0                       Blends a score computed from the resume
                                      with a second score computed from the
                                      same resume.
  universal_auto_fails off            Ends candidacies on word caps and missing
                                      sections that a resume cannot have.

No network, no model and no database: everything below is checked against the
pack and against `_parse_verdict` with a hand-written reply. Runs in a second.
"""

import json
import sys

from backend.core.config import CV_ONLY_JOBS, CV_ONLY_ID_BASE, cv_only_job, cv_weight_for
from backend.grading import cv_evaluator
from backend.grading import rubric_pack as pack
from backend.grading.evaluator import EvaluationFailed, _parse_verdict

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  PASS  " if condition else "  FAIL  ") + name
          + (f"    {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def _reply(grid: dict, score: int) -> str:
    """A well-formed model reply marking every criterion the same."""
    return json.dumps({
        "criteria": {c["key"]: {"score": score, "quote": "", "missing": "x",
                                "evidence": "e"}
                     for c in grid["criteria"]},
        "triage": {c["key"]: {"pass": True, "note": ""}
                   for c in grid.get("triage") or ()},
        "auto_fails": [], "fraud_tells": [],
        "gia": {"read": "r", "scales": {}},
        "brief": "A brief.",
    })


def main() -> int:
    print("\nCV-only roles are registered and resolvable")
    for shortcode, (label, slug, job_id) in CV_ONLY_JOBS.items():
        grid = pack.for_slug(slug)
        check(f"{shortcode} ({label}) has a grid",
              grid is not None, f"slug {slug!r}")
        if grid is None:
            continue

        check(f"{shortcode} job_id is above the portal's range",
              job_id >= 900, str(job_id))
        check(f"{shortcode} resolves through cv_only_job()",
              (cv_only_job(shortcode) or {}).get("slug") == slug)

        # 1. Every row in the background block.
        outside = [c["key"] for c in grid["criteria"]
                   if c["block"] != "background"]
        check(f"{grid['key']}: every criterion is a background row",
              not outside, f"outside: {outside}")
        check(f"{grid['key']}: the background block is worth all 100",
              pack.block_points_of(grid)["background"] == 100)

        # 2. The blend is off, by slug AND by grid key -- cv_weight_for tries
        #    the slug first and falls through to the key, and a caller with
        #    only one of them in hand must get the same answer.
        for handle, kwargs in (("slug", {"slug": slug}),
                               ("grid key", {"grid": grid})):
            weight, source = cv_weight_for(**kwargs)
            check(f"{grid['key']}: cv_weight is 0.0 by {handle}",
                  weight == 0.0, f"got {weight}")
            check(f"{grid['key']}: and deliberately so, by {handle}",
                  source == "seat", f"source {source!r}")

        # 3. The universal auto-fails are repealed.
        check(f"{grid['key']}: universal auto-fails are off",
              grid.get("universal_auto_fails") is False)
        check(f"{grid['key']}: only its own auto-fails reach the grader",
              set(pack.auto_fails_of(grid)) == set(grid.get("auto_fails") or ()))

        print(f"\n{grid['key']}: the verdict arithmetic")
        record = "Some resume text that no quote below appears in."

        # A grid of 3s is 60.0 out of 100 and nothing may move it.
        verdict = _parse_verdict(_reply(grid, 3), grid, answer="", artefacts="",
                                 resume=record, has_cv=True, cv_weight=0.0,
                                 cv_weight_source="seat", missing=())
        check("a grid of 3s scores 60.0", verdict["score"] == 60.0,
              str(verdict["score"]))
        check("score and rubric_score are the same number",
              verdict["score"] == verdict["rubric_score"])
        check("the CV was not blended in on top",
              verdict["cv_applied"] is False)
        check("every row was marked",
              verdict["grid_marked"] == verdict["grid_of"] == len(grid["criteria"]))

        # 4. The no-CV floor must not fire. A grid of 1s is 20.0; if the floor
        #    were armed every row would be raised to 3 and this would be 60.0.
        floored = _parse_verdict(_reply(grid, 1), grid, answer="", artefacts="",
                                 resume=record, has_cv=True, cv_weight=0.0,
                                 cv_weight_source="seat", missing=())
        check("a grid of 1s scores 20.0, not floored up to 60.0",
              floored["score"] == 20.0, str(floored["score"]))
        check("nothing reports having been floored",
              floored["background_floored"] is None)

        print(f"\n{grid['key']}: quotes ground against the record")
        marks = json.loads(_reply(grid, 4))
        first = grid["criteria"][0]["key"]
        marks["criteria"][first]["quote"] = "resume text that no quote"
        grounded = _parse_verdict(json.dumps(marks), grid, answer="",
                                  artefacts="", resume=record, has_cv=True,
                                  cv_weight=0.0, cv_weight_source="seat",
                                  missing=())
        row = next(r for r in grounded["grid"] if r["key"] == first)
        check("a quote taken from the record verifies", row["grounded"] is True)

        marks["criteria"][first]["quote"] = "words that are not in the record"
        ungrounded = _parse_verdict(json.dumps(marks), grid, answer="",
                                    artefacts="", resume=record, has_cv=True,
                                    cv_weight=0.0, cv_weight_source="seat",
                                    missing=())
        row = next(r for r in ungrounded["grid"] if r["key"] == first)
        check("a quote that is not in it does not", row["grounded"] is False)

    print("\nevaluate_cv refuses what it cannot mark")
    grid = pack.for_slug(next(iter(CV_ONLY_JOBS.values()))[1])
    role = {"slug": "x", "title": "x"}

    try:
        cv_evaluator.evaluate_cv({"resume_text": "", "resume_error": "http_404"},
                                 role, grid)
        check("a candidate with no CV text is refused", False, "no exception")
    except EvaluationFailed as exc:
        check("a candidate with no CV text is refused", True, str(exc)[:60])

    # A grid with work-product rows would have this path marking an assessment
    # that was never submitted.
    mixed = pack.by_key("full_stack")
    try:
        cv_evaluator.evaluate_cv({"resume_text": "text"}, role, mixed)
        check("a grid that is not CV-only is refused", False, "no exception")
    except EvaluationFailed as exc:
        check("a grid that is not CV-only is refused", True, str(exc)[:60])

    print("\nThe id band is clear of the portal's")
    check("CV-only ids start far above any portal submission id",
          CV_ONLY_ID_BASE >= 1_000_000, str(CV_ONLY_ID_BASE))

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
