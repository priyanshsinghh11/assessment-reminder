"""
The pedigree reader: what a CV is taken to be saying about employers and
schools, and -- mostly -- what it is NOT taken to be saying.

The false-positive cases are the point of this file. Half the names on those
lists are also ordinary English words or tools that live in a skills section,
and the failure mode is silent: a spotlight quietly full of people who once
wrote a shell script looks exactly like a spotlight that is working. There is
no screen anywhere that would show that going wrong, so it is checked here.

Pure -- no database, no network, no credentials -- like everything else in the
fast suite.
"""

import pytest

from backend.grading import pedigree


def names(matches):
    return {m["name"] for m in matches}


def sources(matches):
    return {m["name"]: m["source"] for m in matches}


# ---------------------------------------------------------------------------
# The structured path: an ATS that already parsed the file for us
# ---------------------------------------------------------------------------

WORKABLE = {
    "workable_experience": (
        "- Product Manager at Meta (2021-03 to present)\n"
        "  Ran the ranking team.\n"
        "- Associate at Bain & Company (2018-07 to 2021-02)"
    ),
    "workable_education": (
        "- MBA, Business Administration, Harvard Business School\n"
        "- BA, Economics, Brown University"
    ),
    "candidate_headline": "Product Manager at Meta",
    "resume_text": "Skills: Python, shell scripting, MongoDB, Figma, Oracle SQL",
}


def test_reads_employers_and_schools_off_the_parsed_record():
    read = pedigree.read(WORKABLE)
    assert names(read["employers"]) == {"Meta", "Bain & Company"}
    assert names(read["schools"]) == {"Harvard", "Brown"}
    assert set(sources(read["employers"]).values()) == {"record"}


def test_a_skills_list_is_not_an_employment_history():
    """
    Shell, MongoDB, Figma and Oracle are all on the employer list and all four
    are in this candidate's skills line. None of them is a job they held, and
    the record-only tier is what keeps them out.
    """
    read = pedigree.read(WORKABLE)
    assert names(read["employers"]).isdisjoint({"Oracle", "Figma", "Shell"})


def test_bain_capital_beats_bain_the_consultancy():
    """Longest alias first, or the investing arm reports as the consultancy."""
    read = pedigree.read({"workable_experience": "- Associate at Bain Capital"})
    assert names(read["employers"]) == {"Bain Capital"}


# ---------------------------------------------------------------------------
# The fallback path: a plain-text CV with its own headings
# ---------------------------------------------------------------------------

PLAIN_CV = """Jane Doe
Brooklyn, New York

EXPERIENCE
Vice President, J.P. Morgan Chase & Co. — 2020 to 2024
Senior Analyst, Oracle Corporation — 2017 to 2020

EDUCATION
Princeton University, B.S.E. Computer Science, 2017

SKILLS
Shell, block storage, target sizing, visa sponsorship not required, Apple Pay
"""


PLAIN_CV_SUB = {"resume_text": PLAIN_CV}


def test_finds_the_experience_and_education_blocks_in_a_plain_cv():
    """The headings are what make this work; see _sections."""
    read = pedigree.read({"resume_text": PLAIN_CV})
    assert names(read["employers"]) == {"JPMorgan Chase", "Oracle"}
    assert names(read["schools"]) == {"Princeton"}


def test_the_skills_block_below_them_is_not_read_as_either():
    """
    Shell, Block, Target, Visa and Apple are all on the employer list. All five
    appear under SKILLS in that CV as ordinary words, and none of them may show
    up as a job -- the heading ended the employment block above.
    """
    read = pedigree.read({"resume_text": PLAIN_CV})
    assert names(read["employers"]).isdisjoint(
        {"Shell", "Block", "Target", "Visa", "Apple"})


@pytest.mark.parametrize("text", [
    "Worked extensively with metadata pipelines",
    "Business intelligence and competitive intelligence reporting",
    "Volunteered in Apple Valley and Brown County",
    "Managed visa applications and shell companies",
])
def test_word_boundaries_hold(text):
    """Meta, Intel, Apple, Brown, Visa and Shell, all as substrings or nouns."""
    read = pedigree.read({"resume_text": text})
    assert read["employers"] == []
    assert read["schools"] == []


def test_an_empty_record_is_an_answer_rather_than_a_crash():
    read = pedigree.read({})
    assert read["employers"] == [] and read["schools"] == []
    assert read["had_text"] is False
    assert read["version"] == pedigree.VERSION


def test_the_source_fields_are_the_ones_the_store_projects():
    """
    store.PEDIGREE_SOURCE_FIELDS is a hand copy of pedigree.SOURCE_FIELDS --
    the store deliberately imports nothing from the grading package. A field
    added here and not there is a field the scan would silently never see.
    """
    from backend.db import store
    assert set(store.PEDIGREE_SOURCE_FIELDS) == set(pedigree.SOURCE_FIELDS)


# ---------------------------------------------------------------------------
# The corpus cases
# ---------------------------------------------------------------------------
#
# EVERY ONE OF THESE IS A REAL LINE from a real CV in this database, and every
# one of the first group was at some point reported as a job somebody held.
# They are here rather than in a comment because each was found by reading the
# live list by hand, which is expensive and does not repeat itself.
#
# The shape of the mistake is always the same: a line that mentions a company
# without being about working there. A certificate the company issued, a course
# it sponsored, a client it was, a product of its that somebody used. Nothing
# in the string "Amazon" distinguishes "Business Analyst, Amazon, 2023" from
# "deployed on Amazon RDS", so the rules work on the line around it.

NOT_A_JOB = {
    # A vendor's name in front of a job title is the specialism, not the
    # employer -- this person worked at AUNDE Group.
    "vendor-qualified title":
        "Platform Solutions: SAP Consultant, AUNDE Group (Apr 2026 - Present)",
    # Credentials. Every one of these has an organisation and a date on it,
    # which is exactly what a job entry has.
    "issued credential": "Oracle University  |   Issued Feb 2026",
    "skills programme": "IBM SkillsBuild  |   Issued May 2024",
    "vendor certificate": "Salesforce  |   Issued Dec 2025",
    "course by a vendor": "03/2026 - PresentCareer Essentials in Business "
                          "Analysis by Microsoft and LinkedIn",
    "corporate-responsibility programme":
        "AI Internship - TechSaksham (Microsoft & SAP CSR) Sep-Dec 2024",
    "open-source programme": "Google Summer of Code (with Oppia Foundation) | "
                             "Contributor May 2021 - August 2021",
    # Four companies on one line, none of them the employer.
    "a client list": "insights for Mastercard global leadership and senior "
                     "executives at Ikea, Amazon, SAP and Cisco, 2019-2023",
    # Technology somebody used.
    "an API in a stack line": "Python, LangGraph, Anthropic Claude API, MCP  "
                              "June 2026 - July 2026",
    "an AI vendor in a project stack":
        "Call Intelligence Platform | Ringba, AssemblyAI, LEMUR, OpenAI, "
        "Python 2023 - 2025",
    "an AI vendor beside a coding tool":
        "(GitHub Copilot, OpenAI APIs) for coding support, debugging and "
        "documentation, 2022 - 2024",
    "a tool in a tools line": "Adept at using industry-standard tools including, "
                              "Adobe Premiere, Final Cut Pro, 2021-2024",
    "a design tool": "Tools: JIRA, Confluence, ClickUp, Miro, Figma, MS Excel, "
                     "SQL   2020 - 2024",
    "a product in a duty bullet":
        "- Architected Cross-Account Amazon RDS Access - Deployed Amazon RDS "
        "with cross-account VPC peering 2021-2023",
    # The case the dashboard's first reader caught: cloud services in an
    # infrastructure line, read as having worked at Amazon.
    "cloud services in an infrastructure line":
        "Cloud & Infrastructure: AWS, Azure, GCP, Docker, Kubernetes, Git\n"
        "processed large scale data using Spark, Hadoop and Hive on AWS and "
        "Azure infrastructure, 2023-2025",
    # PDF text extracted one letter at a time. "M a na ge r s" contains " ge "
    # with a space either side, which is a word boundary, which was General
    # Electric. Any two-letter name can be conjured out of a line like this.
    "letter-spaced pdf text":
        "H u m a n  R e s o u r c e  M a n a ge m e n t : "
        "H R  f o r  P e o p l e  M a na ge r s  2020 - 2023",
}

IS_A_JOB = {
    "name, dash, title": (
        "IBM - Applied AI Engineer - Enterprise AI United States | Mar 2024 - Present",
        "IBM"),
    "title, pipe, name": (
        "Greenlight Expert II - Operations  |  Uber Technologies Jan 2017 - Aug 2017",
        "Uber"),
    "a named subsidiary": (
        "Amazon Alexa Data Services | Chennai, India | Aug 2020 - Jul 2021", "Amazon"),
    "name, city, dates": (
        "Deloitte, Hyderabad, India | May 2019 to Aug 2020", "Deloitte"),
    "an internship": (
        "Jan 2022 - Jan 2023: Software Engineering Intern | Microsoft - Hyderabad",
        "Microsoft"),
    "title, name, bracketed dates": (
        "Process Advisor, Barclays Shared Services (2010-2012)", "Barclays"),
    "name and bracketed dates alone": (
        "Dell International Services (2008-2009)", "Dell"),
    "shouted, with an ampersand": (
        "2023-Present JPMORGAN CHASE & CO New York, NY", "JPMorgan Chase"),
    "pipe-separated everything": (
        "Consultant | Accenture | Bangalore, India | July 2023 - January 2025",
        "Accenture"),
}


def under(heading, *lines):
    """A CV of one section, so a case is about the line rather than the layout."""
    return {"resume_text": heading + "\n" + "\n".join(lines) + "\nSKILLS\nPython"}


@pytest.mark.parametrize("label", sorted(NOT_A_JOB))
def test_a_mention_is_not_employment(label):
    """
    These sit INSIDE the work-experience section, which is the hard version of
    the test: the section rule cannot save them, so the line rules have to.
    """
    read = pedigree.read(under("WORK EXPERIENCE", NOT_A_JOB[label]))
    assert read["employers"] == [], f"{label}: {names(read['employers'])}"


@pytest.mark.parametrize("label", sorted(IS_A_JOB))
def test_a_real_job_entry_counts(label):
    line, want = IS_A_JOB[label]
    read = pedigree.read(under("WORK EXPERIENCE", line))
    assert want in names(read["employers"]), f"{label}: got {names(read['employers'])}"


@pytest.mark.parametrize("label", sorted(IS_A_JOB))
def test_the_same_line_outside_the_section_does_not(label):
    """
    THE RULE, stated as plainly as it can be: an employer counts because of
    the heading it sits under, not because of how the line is written. Every
    line in IS_A_JOB is a perfectly-formed job entry, and under any other
    heading none of them is a job this candidate held.
    """
    line, want = IS_A_JOB[label]
    for heading in ("PROJECTS", "CERTIFICATIONS", "FELLOWSHIPS & RESEARCH",
                    "VOLUNTEER EXPERIENCE", "LEADERSHIP EXPERIENCE"):
        read = pedigree.read(under(heading, line))
        assert read["employers"] == [], f"{label} under {heading}"


def test_an_unsectioned_cv_yields_no_employers():
    """
    No findable heading, no employers. Intended, not a gap: guessing at
    employment from an unsectioned document is what produced "368 of our
    applicants worked at Google".
    """
    cv = ("Jane Doe\nSoftware Engineer, Google, 2019 to 2023\n"
          "Analyst, McKinsey, 2016 to 2019\n")
    assert pedigree.read({"resume_text": cv})["employers"] == []


def test_the_projects_section_that_broke_the_previous_version():
    """
    A real CV from this database. Its PROJECTS section names Google twice,
    with a place and a date range on the two lines -- every structural signal
    a job entry has -- and the candidate has never worked there. Its EDUCATION
    section names the university they did attend.
    """
    cv = (
        "Moheeb-shaped CV\n"
        "WORK EXPERIENCE\n"
        "BlueWayz Corporation Cleveland, OH\n"
        "Technical Project Lead (AI & Cloud Systems) Oct 2025 - Present\n"
        "PROJECTS\n"
        "Google Hackathon New York, NY\n"
        "Google AI Challenge - Finalist Oct-Nov 2024\n"
        "Salesforce Hackathon New York, NY\n"
        "EDUCATION\n"
        "New York University - SPS, DPB New York, NY\n"
        "M.S. in Project Management Graduation: May 2028\n"
    )
    read = pedigree.read({"resume_text": cv})
    assert names(read["employers"]) == set()
    assert names(read["schools"]) == {"NYU"}


def test_the_hit_flag_agrees_with_the_lists():
    """
    The dashboard's query reads `pedigree.hit` and never looks at the arrays,
    so a flag that disagreed with them would silently empty the panel -- or
    fill it with people who matched nothing.
    """
    for sub in ({"resume_text": WORKABLE["resume_text"]}, WORKABLE, {}, PLAIN_CV_SUB):
        read = pedigree.read(sub)
        assert read["hit"] == bool(read["employers"] or read["schools"])


# ---------------------------------------------------------------------------
# Which seats the school spotlight is for
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("location,expected", [
    ("New York, on-site, full-time, 4 to 7 years, $150,000 to $250,000", "onsite"),
    ("Hybrid New York, 1099 contractor", "hybrid"),
    ("Hybrid, New York. Regular travel to corporate offices", "hybrid"),
    ("Remote, United States", None),
    ("On-site, San Francisco", None),
    ("", None),
    (None, None),
])
def test_new_york_seats(location, expected):
    assert pedigree.new_york_seat(location) == expected


def test_the_pack_still_has_new_york_seats_to_point_at():
    """
    The school spotlight is empty by construction if no grid names a New York
    seat, and a `location` line reworded in the pack would do that without
    anything else on the dashboard changing. This is what notices.
    """
    from backend.grading.rubric_pack import _grids
    seats = [g for g in _grids.GRIDS
             if pedigree.new_york_seat(g.get("location"))]
    assert seats, "no grid in the pack reads as a New York seat any more"
