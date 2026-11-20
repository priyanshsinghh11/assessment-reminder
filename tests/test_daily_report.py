"""
The daily recruiting summary's layout.

Pure: build_messages() takes numbers, not a database, so this runs on every
commit with no Mongo and no Chat space.
"""

from datetime import datetime, timedelta, timezone

from backend.report import daily

UNTIL = datetime(2026, 11, 20, 11, 20, tzinfo=timezone.utc)
SINCE = UNTIL - timedelta(hours=24)
TITLES = {1: "Product Manager", 2: "UI/UX Designer", 3: "Recruiter"}

STATS = {
    # Busy, deep, finishing, but nobody in interview and a review backlog.
    1: {"on_file": 900, "new_day": 12, "new_week": 49, "started_month": 40,
        "submitted_month": 30, "scored": 300, "at_bar": 80, "at_best": 20,
        "waiting_at_bar": 15, "awaiting_score": 6, "in_interview": 0},
    # Quiet, thin, and losing people on the assessment.
    2: {"on_file": 40, "new_day": 0, "new_week": 0, "started_month": 20,
        "submitted_month": 5, "scored": 10, "at_bar": 0, "at_best": 0,
        "waiting_at_bar": 0, "awaiting_score": 0, "in_interview": 0},
    # Interviewing, with too few starts to be judged on completion.
    3: {"on_file": 300, "new_day": 3, "new_week": 7, "started_month": 4,
        "submitted_month": 4, "scored": 90, "at_bar": 9, "at_best": 2,
        "waiting_at_bar": 0, "awaiting_score": 1, "in_interview": 4, "hired": 1},
}


def scored(job_id, score):
    return {"job_id": job_id, "evaluation": {"score": score}}


def build(**overrides):
    args = dict(since=SINCE, until=UNTIL, titles=TITLES, stats=STATS, scored=[])
    args.update(overrides)
    return "\n\n".join(daily.build_messages(**args))


def section(text, heading):
    return text.split(heading, 1)[1].split("\n\n", 1)[0]


class TestSummary:
    def test_one_message_with_the_headlines_first(self):
        messages = daily.build_messages(since=SINCE, until=UNTIL, titles=TITLES,
                                        stats=STATS, scored=[])
        assert len(messages) == 1
        assert messages[0].startswith("*Recruiting summary* · Fri 20 Nov 2026\n"
                                      "_Last 24 hours, to 11:20 UTC_")

    def test_the_last_24_hours(self):
        text = build(scored=[scored(1, 90), scored(1, 60)])
        assert "• *15* new applicants" in text
        assert "• *2* scored · *1* of them 75+" in text
        assert "• Most applicants: Product Manager (12), Recruiter (3)" in text
        assert "• No new applicants (1 of 3 roles): UI/UX Designer" in text

    def test_pipeline_totals(self):
        text = build()
        assert "• *1,240* applicants across 3 open roles · *400* scored · *22* at 85+" in text
        assert "• *4* in interview · *1* hired" in text

    def test_roles_are_ranked_both_ways(self):
        text = build()
        assert "• Most crowded: Product Manager (900), Recruiter (300), UI/UX Designer (40)" in text
        assert "• Least crowded: UI/UX Designer (40), Recruiter (300), Product Manager (900)" in text

    def test_excluded_roles_are_not_open(self):
        for job_id in daily.REPORT_EXCLUDED_ROLES:
            assert job_id not in daily.open_roles()


class TestTopNotInterviewed:
    def test_each_candidate_carries_evaluation_and_submission(self):
        top = [{"_id": 10, "job_id": 1, "candidate_name": "Alex Doe",
                "evaluation": {"score": 97.25},
                "admin_url": "https://portal/s/10"}]
        text = build(top=top)
        assert "🏆 *Top 5 not yet interviewed* _(best in each role)_\n" in text
        assert "1. *Alex Doe* · Product Manager · *97.2*" in text
        assert (f"<{daily.REPORT_DASHBOARD_URL}/evaluations.html#role=1&candidate=10"
                "|Evaluation> · <https://portal/s/10|Submission>") in text
        assert "Workable" not in text

    def test_a_cv_only_candidate_says_so(self):
        top = [{"_id": 10, "job_id": 1, "candidate_name": "Alex Doe",
                "evaluation": {"score": 90}, "cv_only": True}]
        assert "No assessment (CV only)" in build(top=top)

    def test_a_name_cannot_inject_chat_markup(self):
        top = [{"_id": 10, "job_id": 1, "evaluation": {"score": 90},
                "candidate_name": "*Bold* <https://evil|click>"}]
        text = build(top=top)
        assert "evil|click" not in text
        assert "*Bold https://evilclick*" in text

    def test_says_so_when_nobody_is_waiting(self):
        assert "• Every scored candidate has had an interview." in build()


class TestGuards:
    def test_dry_run_refuses_on_actions(self, monkeypatch):
        # --dry-run prints names, and Actions logs on this repository are public.
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setattr("sys.argv", ["report", "--dry-run"])
        assert daily.main() == 1


class TestAreasToFocus:
    def test_lists_what_needs_acting_on(self):
        focus = section(build(), "🎯 *Areas to focus*")
        assert "*Unreviewed strong candidates:* 15 at 75+ that nobody has looked at. Most in Product Manager (15)" in focus
        assert "*Strong pools with nobody in interview:* Product Manager (20 at 85+)" in focus
        assert "*Candidates dropping off the assessment:* UI/UX Designer (25% finish)" in focus
        assert "*No applicants in 7 days:* UI/UX Designer" in focus
        assert "*Waiting to be scored:* 7 submissions. Most in Product Manager (6), Recruiter (1)" in focus
        assert "*Thin pools, nobody at 75+ yet:* UI/UX Designer (40 applicants)" in focus

    def test_completion_needs_enough_starts_to_judge(self):
        # Recruiter's 4 of 4 is 100%, on too few starts to mean anything.
        assert "Recruiter (100% finish)" not in build()

    def test_a_falling_day_is_a_focus_not_a_success(self):
        quiet = {j: {**s, "new_day": 0} for j, s in STATS.items()}
        text = build(stats=quiet)
        assert "*Applications down:* 0 in the last 24 hours against 8 a day" in section(
            text, "🎯 *Areas to focus*")
        assert "Applications" not in section(text, "✅ *What's going well*")


class TestGoingWell:
    def test_lists_what_is_working(self):
        well = section(build(scored=[scored(1, 92), scored(3, 88), scored(1, 70)]),
                       "✅ *What's going well*")
        assert "*Applications up:* 15 in the last 24 hours against 8 a day this week" in well
        assert "*Strong new scores:* 2 candidates scored 85+ in the last 24 hours, in Product Manager (1), Recruiter (1)" in well
        assert "*Candidates finishing the assessment:* Product Manager (75% finish)" in well
        assert "*Moving forward:* 4 in interview and 1 hired. Most in Recruiter (4)" in well
        assert "*Deepest talent pools:* Product Manager (20 at 85+), Recruiter (2 at 85+)" in well

    def test_an_empty_section_still_says_it_ran(self):
        flat = {1: {"on_file": 5}}
        text = build(stats=flat, titles={1: "Recruiter"})
        assert "✅ *What's going well*\n• Nothing stands out today." in text


class TestPacking:
    def test_messages_stay_under_the_limit_and_blocks_are_whole(self):
        blocks = [f"block {i} " + "x" * 900 for i in range(10)]
        messages = daily.pack(blocks, limit=2000)
        assert all(len(m) <= 2000 for m in messages)
        assert "\n\n".join(messages) == "\n\n".join(blocks)
