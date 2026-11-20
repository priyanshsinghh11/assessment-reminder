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
    def test_the_whole_summary_is_the_first_message(self):
        messages = daily.build_messages(since=SINCE, until=UNTIL, titles=TITLES,
                                        stats=STATS, scored=[])
        assert messages[0].startswith("*Recruiting summary* · Fri 20 Nov 2026\n"
                                      "_Last 24 hours, to 11:20 UTC_")
        assert "✅ *What's going well*" in messages[0]

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


def candidate(sid, job_id, score, name="Alex Doe", **extra):
    return {"_id": sid, "job_id": job_id, "candidate_name": name,
            "evaluation": {"score": score}, **extra}


class TestTopNotInterviewed:
    def test_the_summary_stays_the_first_message(self):
        top = {1: [candidate(i, 1, 90 - i, admin_url=f"https://portal/s/{i}")
                   for i in range(5)]}
        messages = daily.build_messages(since=SINCE, until=UNTIL, titles=TITLES,
                                        stats=STATS, scored=[], top=top)
        assert "🏆" not in messages[0]
        assert messages[1].startswith("🏆 *Top 5 not yet interviewed, by role*")

    def test_each_role_lists_its_own_with_both_links(self):
        top = {1: [candidate(10, 1, 97.25, admin_url="https://portal/s/10")],
               3: [candidate(11, 3, 88, name="Sam Roe")]}
        text = build(top=top)
        assert ("*Product Manager*\n1. *Alex Doe* · *97.2* · "
                f"<{daily.REPORT_DASHBOARD_URL}/evaluations.html#role=1&candidate=10"
                "|Evaluation> · <https://portal/s/10|Submission>") in text
        assert "*Recruiter*\n1. *Sam Roe* · *88* · " in text
        assert "_Nobody scored is waiting for an interview in:_ UI/UX Designer" in text
        assert "Workable" not in text

    def test_roles_never_split_across_messages(self):
        top = {j: [candidate(j * 10 + i, j, 90, admin_url="https://portal/" + "x" * 300)
                   for i in range(5)] for j in STATS}
        for message in daily.build_messages(since=SINCE, until=UNTIL, titles=TITLES,
                                            stats=STATS, scored=[], top=top):
            assert len(message) <= daily.MESSAGE_LIMIT
            for title in TITLES.values():
                if f"*{title}*\n" in message:
                    assert message.split(f"*{title}*\n", 1)[1].count("\n5. ") <= 1

    def test_a_cv_only_candidate_says_so(self):
        assert "CV only" in build(top={1: [candidate(10, 1, 90, cv_only=True)]})

    def test_a_name_cannot_inject_chat_markup(self):
        text = build(top={1: [candidate(10, 1, 90,
                                        name="*Bold* <https://evil|click>")]})
        assert "evil|click" not in text
        assert "*Bold https://evilclick*" in text


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
