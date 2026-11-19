"""
The daily recruiting summary's layout, and the two guards around it.

Pure: build_messages() takes numbers and rows, not a database, so this runs on
every commit with no Mongo, no Workable and no Chat space.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.report import daily

ROOT = Path(__file__).resolve().parents[1]

UNTIL = datetime(2026, 11, 20, 11, 20, tzinfo=timezone.utc)
SINCE = UNTIL - timedelta(hours=24)
TITLES = {1: "Product Manager", 2: "UI/UX Designer", 3: "Recruiter"}

STATS = {
    1: {"on_file": 900, "new_day": 12, "new_week": 49, "started_month": 40,
        "submitted_month": 30, "scored": 300, "at_bar": 80, "at_best": 20,
        "waiting_at_bar": 15},
    2: {"on_file": 40, "new_day": 0, "new_week": 0, "started_month": 20,
        "submitted_month": 5, "scored": 10, "at_bar": 2, "at_best": 1,
        "waiting_at_bar": 2},
    3: {"on_file": 300, "new_day": 3, "new_week": 7, "started_month": 4,
        "submitted_month": 4, "scored": 90, "at_bar": 0, "at_best": 0,
        "waiting_at_bar": 0},
}


def row(sid, job_id, score, name="Alex Doe"):
    return {"_id": sid, "job_id": job_id, "candidate_name": name,
            "evaluation": {"score": score}}


def links_for(*rows, workable=True):
    return {r["_id"]: {"evaluation": f"https://dash/e/{r['_id']}",
                       "submission": f"https://portal/s/{r['_id']}",
                       "workable": f"https://wk/{r['_id']}" if workable else None}
            for r in rows}


def build(**overrides):
    args = dict(since=SINCE, until=UNTIL, titles=TITLES, stats=STATS, scored=[],
                pipeline={"interview": 4, "interview_2": 1, "hired": 2},
                top=[], top_is_today=True, links={})
    args.update(overrides)
    return "\n\n".join(daily.build_messages(**args))


class TestSummary:
    def test_one_message_with_the_headlines_first(self):
        messages = daily.build_messages(
            since=SINCE, until=UNTIL, titles=TITLES, stats=STATS, scored=[],
            pipeline={}, top=[], top_is_today=True, links={})
        assert len(messages) == 1
        assert messages[0].startswith("*Recruiting summary* · Fri 20 Nov 2026\n"
                                      "_Last 24 hours, to 11:20 UTC_")

    def test_the_day_is_set_against_the_week(self):
        # 15 today against 56 over seven days, which is 8 a day.
        assert "*15* new applicants · ▲ above the week's 8 a day" in build()

    def test_a_role_with_nobody_new_is_named(self):
        text = build()
        assert "• Most applicants: Product Manager (12) · Recruiter (3)" in text
        assert "• No new applicants (1 of 3 roles): UI/UX Designer" in text
        assert "• Gone quiet (nobody in 7 days): UI/UX Designer" in text

    def test_roles_are_ranked_both_ways(self):
        text = build()
        assert "• Most crowded: Product Manager (900) · Recruiter (300) · UI/UX Designer (40)" in text
        assert "• Least crowded: UI/UX Designer (40) · Recruiter (300)" in text

    def test_completion_needs_enough_starts_to_rank(self):
        # Recruiter's 4 of 4 is 100%, on too few starts to mean anything.
        text = build()
        assert ("• Most engaging (finish the assessment, 30 days): "
                "Product Manager (75%) · UI/UX Designer (25%)") in text
        assert "Recruiter (100%)" not in text

    def test_pipeline_totals(self):
        text = build(scored=[row(1, 1, 90), row(2, 1, 60)])
        assert "• *1,240* applicants across 3 open roles · *400* scored · *21* at 85+" in text
        assert "• *4* in interview · *1* in round 2 · *2* hired" in text
        assert "• *2* scored · *1* of them 75+" in text
        assert "• *17* candidates at 75+ that nobody has reviewed" in text


class TestCandidates:
    def test_each_candidate_carries_three_links(self):
        top = row(10, 1, 91.25)
        text = build(top=[top], links=links_for(top))
        assert "*Top 1 from the last 24 hours*" in text
        assert "1. *Alex Doe* · Product Manager · *91.2*" in text
        assert ("<https://dash/e/10|Evaluation> · <https://portal/s/10|Submission>"
                " · <https://wk/10|Workable>") in text

    def test_says_when_the_list_is_not_todays(self):
        top = row(10, 1, 80)
        text = build(top=[top], top_is_today=False, links=links_for(top))
        assert "none of today's applicants is scored yet" in text

    def test_a_missing_workable_link_is_said_not_dropped(self):
        top = row(10, 1, 80)
        assert "Workable: not found" in build(top=[top],
                                              links=links_for(top, workable=False))

    def test_a_name_cannot_inject_chat_markup(self):
        top = row(10, 1, 80, name="*Bold* <https://evil|click>")
        text = build(top=[top], links=links_for(top))
        assert "evil|click" not in text
        assert "*Bold https://evilclick*" in text


class TestPacking:
    def test_messages_stay_under_the_limit_and_blocks_are_whole(self):
        blocks = [f"block {i} " + "x" * 900 for i in range(10)]
        messages = daily.pack(blocks, limit=2000)
        assert all(len(m) <= 2000 for m in messages)
        assert "\n\n".join(messages) == "\n\n".join(blocks)


class TestGuards:
    def test_dry_run_refuses_on_actions(self, monkeypatch):
        # --dry-run prints names, and Actions logs on this repository are public.
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setattr("sys.argv", ["report", "--dry-run"])
        assert daily.main() == 1

    def test_the_workflow_never_dry_runs(self):
        workflow = (ROOT / ".github/workflows/report.yml").read_text()
        commands = [line for line in workflow.splitlines()
                    if "manage.py report" in line and not line.strip().startswith("#")]
        assert commands and not any("--dry-run" in line for line in commands)
