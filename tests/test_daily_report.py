"""
The daily recruiting report's layout, and the two guards around it.

Pure: build_messages() takes rows, not a database, so this runs on every commit
with no Mongo, no Workable and no Chat space.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.report import daily

ROOT = Path(__file__).resolve().parents[1]

UNTIL = datetime(2026, 11, 20, 11, 20, tzinfo=timezone.utc)
SINCE = UNTIL - timedelta(hours=24)
TITLES = {1: "Product Manager", 2: "UI/UX Designer"}


def row(sid, job_id, score=None, name="Alex Doe", **extra):
    doc = {"_id": sid, "job_id": job_id, "candidate_name": name,
           "started_at": "2026-11-19T15:00:00.000Z", **extra}
    if score is not None:
        doc["evaluation"] = {"score": score}
    return doc


def links_for(*rows, workable=True):
    return {r["_id"]: {"evaluation": f"https://dash/e/{r['_id']}",
                       "submission": f"https://portal/s/{r['_id']}",
                       "workable": f"https://wk/{r['_id']}" if workable else None}
            for r in rows}


def build(**overrides):
    args = dict(since=SINCE, until=UNTIL, titles=TITLES, roles={1, 2},
                applicants=[], scored=[], top_today=[], unreviewed={},
                links={})
    args.update(overrides)
    return "\n\n".join(daily.build_messages(**args))


class TestEmptySectionsSaySo:
    def test_a_quiet_day_still_reports_every_section(self):
        text = build()
        assert "*New applicants: 0*" in text
        assert "*Newly scored: 0*" in text
        assert "No applicant from this window has been scored yet" in text
        assert "No unreviewed scored candidates for:_ Product Manager, UI/UX Designer" in text

    def test_a_role_with_nobody_new_is_named(self):
        text = build(applicants=[row(10, 1)])
        assert "• Product Manager: 1" in text
        assert "_No new applicants for:_ UI/UX Designer" in text


class TestCandidates:
    def test_each_candidate_carries_three_links(self):
        top = row(10, 1, 91.25)
        text = build(top_today=[top], links=links_for(top))
        assert "1. *Alex Doe* · Product Manager · Score *91.2*" in text
        assert ("<https://dash/e/10|Evaluation> · <https://portal/s/10|Submission>"
                " · <https://wk/10|Workable>") in text

    def test_a_missing_workable_link_is_said_not_dropped(self):
        top = row(10, 1, 80)
        text = build(top_today=[top], links=links_for(top, workable=False))
        assert "Workable: not found" in text

    def test_per_role_lines_carry_the_application_date(self):
        top = row(10, 2, 88)
        text = build(unreviewed={2: [top]}, links=links_for(top))
        assert "*UI/UX Designer*\n1. *Alex Doe* · Score *88* · applied 19 Nov" in text

    def test_a_name_cannot_inject_chat_markup(self):
        top = row(10, 1, 80, name="*Bold* <https://evil|click>")
        text = build(top_today=[top], links=links_for(top))
        assert "evil|click" not in text
        assert "*Bold https://evilclick*" in text


class TestPacking:
    def test_messages_stay_under_the_limit_and_blocks_are_whole(self):
        blocks = [f"block {i} " + "x" * 900 for i in range(10)]
        messages = daily.pack(blocks, limit=2000)
        assert all(len(m) <= 2000 for m in messages)
        assert "\n\n".join(messages) == "\n\n".join(blocks)

    def test_the_counts_open_the_first_message(self):
        first = daily.build_messages(
            since=SINCE, until=UNTIL, titles=TITLES, roles={1, 2},
            applicants=[], scored=[], top_today=[], unreviewed={}, links={})[0]
        assert first.startswith("*Daily recruiting report* · Fri 20 Nov 2026")
        assert "_Last 24 hours, to 11:20 UTC_" in first


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
