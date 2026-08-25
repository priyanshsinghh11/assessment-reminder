"""
The scoring engine.

    rubric_pack     the Ajaia Assessment Scoring Rubrics as data: every grid,
                    criterion, weight and behavioural anchor
    evaluator       grid resolution, anchor scoring, auto-fails and triage
    tier_resolver   which of two postings a candidate applied to, where that
                    decides which tier of a family's rubric marks them

This package decides what a submission is worth. It does not decide when to
score one -- that is `pipeline`.
"""
