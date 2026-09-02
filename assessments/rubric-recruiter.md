<!-- The source rubric for the Recruiter seat (portal assignment 40, Workable
     EC24F51CB9), received 2026-08-31 and kept verbatim below. This file is
     the record, not the implementation: the live grid is `recruiting` in
     backend/grading/rubric_pack/, which carries these six rows at these six
     weights and adds the anchors, triage, auto-fails, red flags and reviewer
     path the pack requires around them. Edit the pack to change a score; edit
     this file only if the source document itself is reissued. -->

# 10. Scoring rubric (100 points)

Rate each criterion 1 to 5, multiply by weight over 5, sum to 100. A 5 is
clearly strong and advanceable, not perfect. Use 4 and 2 often. The Experience
row adds, never blocks; score a missing signal 3, never 1. Grade what is in
front of you.

Bands: Advance 75+. Hold 60 to 74. Reject below 60.

| Criterion | Weight | 5 | 3 | 1 |
|---|---|---|---|---|
| Intake questions | 20 | Questions that would prevent a rewrite: scope, must-haves versus preferences, the interview loop, the decision timeline. The years contradiction named. The current-compensation request refused plainly, with what they will provide instead | A competent list, but generic, or it names the contradiction without saying what they would do about it | Questions that restate the notes back, no contradiction noticed, or an intake question that asks what the finalists currently earn |
| The salary calls | 25 | Daniel brought into the band or flagged as under floor with a recommendation. Aditi handled with an early, direct conversation about the range. Marisol assessed against the client-facing requirement, not her years. Marisol's volunteered current pay never used or repeated. Recommendation follows what Renata said she needs | Reasonable calls, but one candidate is handled on the resume rather than the brief, or the recommendation hedges instead of picking | Daniel offered $9,500 because he asked for it, an offer anchored to Marisol's current pay, or Aditi advanced with the range gap unmentioned |
| The posting | 30 | Publish-ready. House style held: one canonical title with alternates listed separately, no hype words, 5 to 7 short requirement lines, an AI fluency line, the compensation range stated. Reads like a person wrote it and a good candidate would finish it | On-format and clean, but generic, or one style rule slipped, or the requirements bloat past 7 lines | "Rockstar" or another banned word survived, the role is posted under three titles, no compensation line, or the requirements are a copy of Renata's paragraph |
| AI Workflow Note | 10 | Names specific tools and uses, and names something AI produced that was rejected, with a reason | Lists tools and uses, but nothing AI gave them was apparently rejected | No note, or a note with no specifics |
| Video | 5 | Clear and direct on why this role and why Ajaia, then walks through real reasoning on the hardest salary call and a choice in the posting, screen plus camera, every link opens | Covers most of what was asked, but the walkthrough narrates the work instead of explaining the thinking behind it | Video absent, salary expectations not stated, or the submission is hard to follow |
| Experience and public presence | 10 | Real, checkable recruiting work: roles filled, a posting or careers page they wrote, a sourcing project, a public profile that holds up | Some relevant evidence, or nothing provided at all (score 3 here, never 1, when there is simply nothing to review) | Links provided that do not open, or shown work that directly contradicts the quality of the submission |

---

## How the pack carries this

The six rows and their weights are reproduced exactly in the `recruiting`
grid. What the pack adds is the block each row sits in, which changes how a
scored card groups and nothing about the arithmetic:

| Source row | Weight | Pack block |
|---|---|---|
| Intake questions | 20 | `work_product` |
| The posting | 30 | `work_product` |
| The salary calls | 25 | `spike` — the assessment names this as the differentiator |
| AI Workflow Note | 10 | `ai_forwardness` |
| Video | 5 | `communication` |
| Experience and public presence | 10 | `background` |

Block split: 50 / 10 / 10 / 5 / 25, against the pack default of 70 / 10 / 10 /
10. Opening the `background` block pins `config.CV_WEIGHT_BY_SEAT["recruiter"]`
to 0.0, so the record is scored once, inside the grid, and not again in the
blend.

The bands are unchanged. The pack splits the advancing band at 85 into "Best"
and "Better" so the top of the queue is visible without opening every card;
that is a presentation split above this document's 75, not a second bar.

The pack's four universal auto-fails are switched off for this grid, because
three of them are cap-and-completeness rules that this rubric turns into
scored rows — an absent video is a 1 on a 5-point row, an absent AI note is a
1 on a 10-point row, and "Grade what is in front of you" is the instruction
above. The fourth, on fabricated data where the task supplied it, is neither
repealed nor contradicted here and is carried forward by hand in the grid's
own `auto_fails`.
