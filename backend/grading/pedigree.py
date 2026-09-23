"""
Who a candidate has worked for, and where they studied, read off their record.

This is NOT a score and it is not a decision. It answers one narrow question --
"does this CV name an employer or a school from the lists below" -- and it
answers it by string matching, so every hit can be shown to the reader as the
exact name that matched and the part of the record it matched in. A spotlight
row on the dashboard therefore reads "Meta -- from their work history", not
"strong candidate", and a reviewer can disagree with it in one glance.

Kept deliberately separate from `evaluator` and `cv_evaluator`. Those two mark
work against a rubric and their output is a judgement. This one only reports a
fact that was already written on the CV, and it must never be blended into a
score: a pedigree list is a proxy for access, not for ability, and the moment
it moves a number nobody can see which of the two moved a candidate. It sits
beside the grid on screen, above it in no sense at all.

A MENTION IS NOT A JOB. That sentence is the whole design, and it was learned
the expensive way -- see the long note over the matching section, which has the
numbers. Naming a company is something a CV does constantly and for many
reasons: a product you used, a certificate you hold, a client you served, a
course you took, a logo on a slide. Employment is one of them and it is not the
common one.

So nothing here asks "is this name in the document". It asks "is this name in
the employer slot of a job entry", and the unit it works on is a LINE:

  from the record   Workable's own parse, which hands over `Title at Company`
                    as structured data. The strongest evidence there is, and
                    the only one that is not a heuristic. About one candidate
                    in twenty has it.

  from the CV       A line carrying employment structure -- a date range, an
                    `at`/`@` connector, a legal suffix -- and not carrying any
                    of the many tells that it is about something else. Those
                    tells are in `_A_COURSE`, `_THIRD_PARTY`, `_A_PROJECT`,
                    `_LIST_HEAD`, `_BULLET`, `_TITLE_AFTER` and
                    `_PRODUCT_AFTER`, and each one exists because of a real CV
                    in this database that it got wrong.

Every match carries `source`, so the UI can say which of the two found it, and
`line`, the evidence itself -- so a reader who doubts a chip can read the
sentence it came from without opening the CV. That is deliberate: this module
is wrong often enough that hiding its reasoning would be dishonest, and a
reader who can see the line can dismiss a bad row in a glance.

It is tuned for precision over recall, and the trade is one-sided on purpose.
Missing somebody from a panel whose title is "worth a look" costs one look.
Filling it with two hundred people who once used Google Sheets costs the panel.
"""

import re
from typing import Optional


# Bumped whenever the lists or the matching below change in a way that would
# move a candidate in or out of a spotlight. Stored on each submission's cached
# read so a change here re-reads every CV instead of leaving the dashboard
# showing yesterday's lists for everyone who was already scanned.
VERSION = "2026-09-24e"   # job-line matching replaced whole-document matching


# ---------------------------------------------------------------------------
# The lists
# ---------------------------------------------------------------------------
#
# Each entry is (canonical name, category, anywhere aliases, record-only
# aliases). The canonical name is what the chip on screen says; the aliases are
# what is actually looked for, matched case-insensitively on word boundaries.
#
# THESE ARE NOT A RANKING and there is no score attached to being on them. They
# are a list of employers a recruiting team here has said they want surfaced,
# nothing more. Adding or removing a name is a one-line edit and needs no code
# change; bump VERSION when you do.

EMPLOYERS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    # --- Technology -------------------------------------------------------
    ("Apple", "Big tech", ("Apple Inc",), ("Apple",)),
    ("Microsoft", "Big tech", ("Microsoft",), ()),
    ("Google", "Big tech", ("Google", "Alphabet Inc", "DeepMind",), ("Alphabet",)),
    ("Amazon", "Big tech", ("Amazon.com", "Amazon"), ()),
    ("Meta", "Big tech", ("Meta Platforms", "Facebook"), ("Meta",)),
    ("Nvidia", "Big tech", ("Nvidia",), ()),
    ("Netflix", "Big tech", ("Netflix",), ()),
    ("Tesla", "Big tech", ("Tesla",), ()),
    ("Adobe", "Big tech", ("Adobe",), ()),
    ("Salesforce", "Big tech", ("Salesforce",), ()),
    ("Oracle", "Big tech", (), ("Oracle",)),
    ("IBM", "Big tech", ("IBM",), ()),
    ("Intel", "Big tech", ("Intel Corporation",), ("Intel",)),
    ("Cisco", "Big tech", ("Cisco",), ()),
    ("Qualcomm", "Big tech", ("Qualcomm",), ()),
    ("SAP", "Big tech", ("SAP SE",), ("SAP",)),
    ("Uber", "Big tech", ("Uber Technologies",), ("Uber",)),
    ("Airbnb", "Big tech", ("Airbnb",), ()),
    ("Stripe", "Big tech", (), ("Stripe",)),
    ("OpenAI", "Big tech", ("OpenAI",), ()),
    ("Anthropic", "Big tech", ("Anthropic",), ()),
    ("Palantir", "Big tech", ("Palantir",), ()),
    ("Databricks", "Big tech", (), ("Databricks",)),
    ("Snowflake", "Big tech", (), ("Snowflake",)),
    ("ServiceNow", "Big tech", ("ServiceNow",), ()),
    ("Workday", "Big tech", (), ("Workday",)),
    ("Intuit", "Big tech", ("Intuit",), ()),
    ("PayPal", "Big tech", ("PayPal",), ()),
    ("Block", "Big tech", ("Block, Inc",), ("Block", "Square, Inc")),
    ("Shopify", "Big tech", ("Shopify",), ()),
    ("Atlassian", "Big tech", (), ("Atlassian",)),
    ("Spotify", "Big tech", ("Spotify",), ()),
    ("Snap", "Big tech", ("Snap Inc", "Snapchat"), ()),
    ("Pinterest", "Big tech", ("Pinterest",), ()),
    ("DoorDash", "Big tech", ("DoorDash",), ()),
    ("Coinbase", "Big tech", ("Coinbase",), ()),
    ("Datadog", "Big tech", (), ("Datadog",)),
    ("ByteDance", "Big tech", ("ByteDance",), ("TikTok",)),
    ("Samsung", "Big tech", ("Samsung",), ()),
    ("Dell", "Big tech", ("Dell Technologies",), ("Dell",)),
    ("VMware", "Big tech", (), ("VMware",)),
    ("Red Hat", "Big tech", (), ("Red Hat",)),

    # --- Banking and markets ---------------------------------------------
    ("JPMorgan Chase", "Banking",
     ("JPMorgan", "J.P. Morgan", "JP Morgan", "JPMC"), ("Chase",)),
    ("Goldman Sachs", "Banking", ("Goldman Sachs",), ("Goldman",)),
    ("Morgan Stanley", "Banking", ("Morgan Stanley",), ()),
    ("Citi", "Banking", ("Citigroup", "Citibank"), ("Citi",)),
    ("Bank of America", "Banking",
     ("Bank of America", "Merrill Lynch", "BofA Securities"), ("Merrill",)),
    ("Wells Fargo", "Banking", ("Wells Fargo",), ()),
    ("Barclays", "Banking", ("Barclays",), ()),
    ("UBS", "Banking", ("UBS",), ()),
    ("Credit Suisse", "Banking", ("Credit Suisse",), ()),
    ("Deutsche Bank", "Banking", ("Deutsche Bank",), ()),
    ("HSBC", "Banking", ("HSBC",), ()),
    ("Jefferies", "Banking", ("Jefferies",), ()),
    ("Lazard", "Banking", ("Lazard",), ()),
    ("Evercore", "Banking", ("Evercore",), ()),
    ("Centerview Partners", "Banking", ("Centerview",), ()),
    ("Moelis", "Banking", ("Moelis",), ()),
    ("PJT Partners", "Banking", ("PJT Partners",), ()),
    ("Houlihan Lokey", "Banking", ("Houlihan Lokey",), ()),
    ("Rothschild", "Banking", ("Rothschild",), ()),
    ("Nomura", "Banking", ("Nomura",), ()),
    ("BNP Paribas", "Banking", ("BNP Paribas",), ()),
    ("RBC Capital Markets", "Banking", ("RBC Capital Markets",), ()),
    ("Macquarie", "Banking", ("Macquarie",), ()),
    ("Visa", "Banking", ("Visa Inc",), ()),
    ("Mastercard", "Banking", ("Mastercard",), ()),
    ("American Express", "Banking", ("American Express", "Amex"), ()),

    # --- Investing --------------------------------------------------------
    ("BlackRock", "Investing", ("BlackRock",), ()),
    ("Blackstone", "Investing", ("Blackstone",), ()),
    ("KKR", "Investing", ("KKR",), ()),
    ("Carlyle", "Investing", ("The Carlyle Group", "Carlyle Group"), ("Carlyle",)),
    ("Apollo", "Investing", ("Apollo Global Management",), ()),
    ("TPG", "Investing", ("TPG Capital",), ("TPG",)),
    ("Bain Capital", "Investing", ("Bain Capital",), ()),
    ("Warburg Pincus", "Investing", ("Warburg Pincus",), ()),
    ("Vista Equity Partners", "Investing", ("Vista Equity",), ()),
    ("Silver Lake", "Investing", ("Silver Lake",), ()),
    ("General Atlantic", "Investing", ("General Atlantic",), ()),
    ("Bridgewater", "Investing", ("Bridgewater Associates",), ("Bridgewater",)),
    ("Citadel", "Investing", ("Citadel Securities", "Citadel LLC"), ("Citadel",)),
    ("Two Sigma", "Investing", ("Two Sigma",), ()),
    ("Jane Street", "Investing", ("Jane Street",), ()),
    ("Millennium", "Investing", ("Millennium Management",), ()),
    ("Point72", "Investing", ("Point72",), ()),
    ("D. E. Shaw", "Investing", ("D. E. Shaw", "D.E. Shaw", "DE Shaw"), ()),
    ("Renaissance Technologies", "Investing", ("Renaissance Technologies",), ()),
    ("AQR", "Investing", ("AQR Capital",), ("AQR",)),
    ("Sequoia Capital", "Investing", ("Sequoia Capital",), ()),
    ("Andreessen Horowitz", "Investing",
     ("Andreessen Horowitz", "a16z"), ()),
    ("Tiger Global", "Investing", ("Tiger Global",), ()),

    # --- Consulting -------------------------------------------------------
    ("McKinsey", "Consulting", ("McKinsey",), ()),
    ("Bain & Company", "Consulting",
     ("Bain & Company", "Bain and Company"), ("Bain",)),
    ("Boston Consulting Group", "Consulting",
     ("Boston Consulting Group",), ("BCG",)),
    ("Deloitte", "Consulting", ("Deloitte",), ()),
    ("PwC", "Consulting", ("PricewaterhouseCoopers", "PwC"), ()),
    ("EY", "Consulting", ("Ernst & Young", "Ernst and Young"), ("EY",)),
    ("KPMG", "Consulting", ("KPMG",), ()),
    ("Accenture", "Consulting", ("Accenture",), ()),
    ("Oliver Wyman", "Consulting", ("Oliver Wyman",), ()),
    ("Kearney", "Consulting", ("A.T. Kearney", "AT Kearney"), ("Kearney",)),
    ("L.E.K.", "Consulting", ("L.E.K. Consulting", "LEK Consulting"), ()),
    ("Roland Berger", "Consulting", ("Roland Berger",), ()),
    ("Strategy&", "Consulting", ("Strategy&", "Booz & Company"), ()),
    ("Booz Allen Hamilton", "Consulting", ("Booz Allen",), ()),
    ("ZS Associates", "Consulting", ("ZS Associates",), ()),
    ("Alvarez & Marsal", "Consulting",
     ("Alvarez & Marsal", "Alvarez and Marsal"), ()),
    ("FTI Consulting", "Consulting", ("FTI Consulting",), ()),

    # --- Pharma, biotech and health --------------------------------------
    ("Pfizer", "Pharma", ("Pfizer",), ()),
    ("Johnson & Johnson", "Pharma",
     ("Johnson & Johnson", "Johnson and Johnson", "Janssen"), ("J&J",)),
    ("Roche", "Pharma", ("Roche", "Genentech"), ()),
    ("Novartis", "Pharma", ("Novartis",), ()),
    ("Merck", "Pharma", ("Merck",), ()),
    ("AstraZeneca", "Pharma", ("AstraZeneca",), ()),
    ("Eli Lilly", "Pharma", ("Eli Lilly",), ()),
    ("Moderna", "Pharma", ("Moderna",), ()),
    ("Sanofi", "Pharma", ("Sanofi",), ()),
    ("GSK", "Pharma", ("GlaxoSmithKline",), ("GSK",)),
    ("Bristol Myers Squibb", "Pharma",
     ("Bristol Myers Squibb", "Bristol-Myers Squibb"), ("BMS",)),
    ("Amgen", "Pharma", ("Amgen",), ()),
    ("Gilead", "Pharma", ("Gilead Sciences",), ("Gilead",)),
    ("Novo Nordisk", "Pharma", ("Novo Nordisk",), ()),
    ("Medtronic", "Pharma", ("Medtronic",), ()),
    ("UnitedHealth", "Pharma", ("UnitedHealth", "Optum"), ()),

    # --- Consumer and industrial -----------------------------------------
    ("Procter & Gamble", "Consumer",
     ("Procter & Gamble", "Procter and Gamble"), ("P&G",)),
    ("Unilever", "Consumer", ("Unilever",), ()),
    ("Nestle", "Consumer", ("Nestlé", "Nestle"), ()),
    ("PepsiCo", "Consumer", ("PepsiCo",), ()),
    ("Coca-Cola", "Consumer", ("Coca-Cola", "Coca Cola"), ()),
    ("L'Oreal", "Consumer", ("L'Oréal", "L'Oreal"), ()),
    ("LVMH", "Consumer", ("LVMH", "Louis Vuitton"), ()),
    ("Nike", "Consumer", ("Nike, Inc",), ("Nike",)),
    ("Disney", "Consumer", ("The Walt Disney Company", "Walt Disney"), ("Disney",)),
    ("Comcast", "Consumer", ("Comcast", "NBCUniversal"), ()),
    ("Walmart", "Consumer", ("Walmart",), ()),
    ("Target", "Consumer", ("Target Corporation",), ()),
    ("Boeing", "Industrial", ("Boeing",), ()),
    ("Airbus", "Industrial", ("Airbus",), ()),
    ("Lockheed Martin", "Industrial", ("Lockheed Martin",), ()),
    ("RTX", "Industrial", ("Raytheon",), ()),
    ("General Electric", "Industrial", ("General Electric",), ()),
    ("Siemens", "Industrial", ("Siemens",), ()),
    ("3M", "Industrial", ("3M Company",), ()),
    ("Honeywell", "Industrial", ("Honeywell",), ()),
    ("Caterpillar", "Industrial", ("Caterpillar",), ()),
    ("Toyota", "Industrial", ("Toyota",), ()),
    ("BMW", "Industrial", ("BMW",), ()),
    ("Mercedes-Benz", "Industrial", ("Mercedes-Benz", "Daimler"), ()),
    ("ExxonMobil", "Industrial", ("ExxonMobil", "Exxon Mobil"), ("Exxon",)),
    ("Shell", "Industrial", ("Royal Dutch Shell",), ("Shell plc", "Shell Oil")),
    ("Chevron", "Industrial", ("Chevron",), ()),
)


# The top 50 US national universities, plus the elite liberal arts colleges and
# the graduate schools whose own names are what a CV actually prints. "Wharton"
# and "Booth" appear on far more CVs than "University of Pennsylvania" and
# "University of Chicago" do, and a list that only held the parent university
# would miss exactly the candidates it was written to find.
#
# The short forms -- Brown, Duke, Rice, Emory, Michigan -- are record-only for
# the same reason `Shell` is. "Brown" is a surname and a colour before it is a
# university, and the full "Brown University" is what an education section
# prints anyway.
SCHOOLS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("Princeton", "Ivy+", ("Princeton University",), ("Princeton",)),
    ("MIT", "Ivy+",
     ("Massachusetts Institute of Technology", "MIT Sloan"), ("MIT",)),
    ("Harvard", "Ivy+",
     ("Harvard University", "Harvard Business School", "Harvard College",
      "Harvard Law"), ("Harvard", "HBS")),
    ("Stanford", "Ivy+",
     ("Stanford University", "Stanford Graduate School of Business"),
     ("Stanford", "Stanford GSB")),
    ("Yale", "Ivy+", ("Yale University", "Yale School of Management"), ("Yale",)),
    ("Caltech", "Top 50",
     ("California Institute of Technology", "Caltech"), ()),
    ("Duke", "Top 50", ("Duke University", "Fuqua School"), ("Duke",)),
    ("Johns Hopkins", "Top 50", ("Johns Hopkins",), ()),
    ("Northwestern", "Top 50",
     ("Northwestern University", "Kellogg School"), ("Northwestern", "Kellogg")),
    ("Penn", "Ivy+",
     ("University of Pennsylvania", "Wharton School", "UPenn"),
     ("Penn", "Wharton")),
    ("Cornell", "Ivy+", ("Cornell University", "Cornell Tech"), ("Cornell",)),
    ("Chicago", "Top 50",
     ("University of Chicago", "Booth School"), ("UChicago", "Booth")),
    ("Brown", "Ivy+", ("Brown University",), ()),
    ("Columbia", "Ivy+",
     ("Columbia University", "Columbia Business School", "Columbia College"),
     ("Columbia",)),
    ("Dartmouth", "Ivy+",
     ("Dartmouth College", "Tuck School"), ("Dartmouth", "Tuck")),
    ("UCLA", "Top 50",
     ("University of California, Los Angeles", "UCLA", "Anderson School"), ()),
    ("UC Berkeley", "Top 50",
     ("University of California, Berkeley", "UC Berkeley", "Haas School"),
     ("Berkeley", "Haas")),
    ("Rice", "Top 50", ("Rice University",), ()),
    ("Notre Dame", "Top 50", ("University of Notre Dame", "Notre Dame"), ()),
    ("Vanderbilt", "Top 50", ("Vanderbilt University", "Vanderbilt"), ()),
    ("Carnegie Mellon", "Top 50", ("Carnegie Mellon",), ("CMU",)),
    ("Michigan", "Top 50",
     ("University of Michigan", "Ross School"), ("Ross",)),
    ("WashU", "Top 50",
     ("Washington University in St. Louis", "Washington University in St Louis"),
     ("WashU",)),
    ("Emory", "Top 50", ("Emory University",), ("Emory",)),
    ("Georgetown", "Top 50", ("Georgetown University", "Georgetown"), ()),
    ("UVA", "Top 50", ("University of Virginia", "Darden School"), ("UVA",)),
    ("UNC Chapel Hill", "Top 50",
     ("University of North Carolina at Chapel Hill", "UNC Chapel Hill",
      "Kenan-Flagler"), ()),
    ("USC", "Top 50",
     ("University of Southern California", "Marshall School"), ("USC",)),
    ("UC San Diego", "Top 50",
     ("University of California, San Diego", "UC San Diego"), ("UCSD",)),
    ("NYU", "Top 50",
     ("New York University", "NYU Stern", "Stern School", "Courant Institute"),
     ("NYU",)),
    ("Florida", "Top 50", ("University of Florida",), ("UF",)),
    ("UT Austin", "Top 50",
     ("University of Texas at Austin", "UT Austin", "McCombs School"), ()),
    ("Georgia Tech", "Top 50",
     ("Georgia Institute of Technology", "Georgia Tech"), ()),
    ("UC Irvine", "Top 50",
     ("University of California, Irvine", "UC Irvine"), ("UCI",)),
    ("UC Davis", "Top 50", ("University of California, Davis", "UC Davis"), ()),
    ("UIUC", "Top 50",
     ("University of Illinois Urbana-Champaign",
      "University of Illinois at Urbana-Champaign"), ("UIUC",)),
    ("Boston College", "Top 50", ("Boston College",), ()),
    ("Tufts", "Top 50", ("Tufts University",), ("Tufts",)),
    ("Washington", "Top 50", ("University of Washington", "Foster School"), ("UW",)),
    ("Boston University", "Top 50", ("Boston University",), ("BU",)),
    ("Rutgers", "Top 50", ("Rutgers University", "Rutgers"), ()),
    ("Ohio State", "Top 50", ("Ohio State University", "Ohio State"), ()),
    ("Purdue", "Top 50", ("Purdue University",), ("Purdue",)),
    ("Maryland", "Top 50",
     ("University of Maryland", "Smith School of Business"), ()),
    ("Lehigh", "Top 50", ("Lehigh University",), ("Lehigh",)),
    ("Texas A&M", "Top 50", ("Texas A&M",), ()),
    ("Georgia", "Top 50", ("University of Georgia",), ("UGA",)),
    ("Rochester", "Top 50",
     ("University of Rochester", "Simon Business School"), ()),
    ("Virginia Tech", "Top 50", ("Virginia Tech",), ()),
    ("Wisconsin", "Top 50",
     ("University of Wisconsin-Madison", "University of Wisconsin–Madison"), ()),
    ("Case Western", "Top 50", ("Case Western Reserve",), ()),
    ("William & Mary", "Top 50",
     ("College of William & Mary", "William and Mary"), ()),
    # Elite liberal arts. Not on the national-universities table at all -- it
    # is a different table -- so a reader comparing this list against US News
    # should know these five were added on purpose rather than mis-ranked.
    ("Williams", "Liberal arts", ("Williams College",), ()),
    ("Amherst", "Liberal arts", ("Amherst College",), ()),
    ("Swarthmore", "Liberal arts", ("Swarthmore College",), ("Swarthmore",)),
    ("Pomona", "Liberal arts", ("Pomona College",), ()),
    ("Wellesley", "Liberal arts", ("Wellesley College",), ()),
)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
#
# WHAT THE FIRST VERSION OF THIS GOT WRONG, because the same mistake is very
# easy to make again. It looked for a company name anywhere in the CV, with a
# weaker tier for names that double as tools. Run over 971 real resumes it
# reported 368 people as having worked at Google and 294 at Microsoft -- about
# two in five applicants -- and the lines it found them on were these:
#
#     Google Ads (Search, Performance Max, Measurement, Display)
#     Other: Microsoft Office, Google Workspace, Google Sheets
#     Technologies: Next.js, AWS Lambda, Amazon API Gateway, Amazon DynamoDB
#     • Converted Figma designs to production React
#     Ran a $3M renovation during COVID without closing a single room
#
# Products, certifications, ad platforms, repo links and a dollar figure. Not
# one of them is an employer. Presence in a document is simply not evidence of
# employment, and no amount of per-name tuning fixes that, because the string
# "Google" is genuinely identical in "Google Ads" and "Software Engineer,
# Google".
#
# SO THE UNIT OF EVIDENCE IS A LINE THAT LOOKS LIKE A JOB ENTRY, not the
# document. A name counts when it sits on a line that carries employment
# structure -- a date range, or an "at"/"@" connector -- and does not count on
# a bullet of duties, a skills list, or a certification. That is a narrower
# read and it will miss people whose CV is laid out oddly. Missing somebody
# from a panel that exists to say "worth a look" costs a look; filling it with
# two hundred people who once used Google Sheets costs the panel.

def _pattern(aliases: tuple[str, ...]) -> Optional[re.Pattern]:
    """
    One case-insensitive alternation per name, anchored on word boundaries.

    `\\b` on both ends is what keeps "Meta" out of "metadata" and "Intel" out of
    "intelligence". `&` and `.` inside a name are escaped by re.escape, and the
    alternation is sorted longest-first so "Bain Capital" is tried before
    "Bain" -- otherwise the investing arm would report as the consultancy.
    """
    if not aliases:
        return None
    ordered = sorted(aliases, key=len, reverse=True)
    body = "|".join(re.escape(alias) for alias in ordered)
    # A trailing "&" or "." has no word boundary after it, so \b would never
    # match "Strategy&" or "L.E.K.". Make the closing boundary conditional on
    # the alias actually ending in a word character.
    return re.compile(rf"\b(?:{body})(?!\w)", re.IGNORECASE)


_EMPLOYER_RULES = tuple(
    (name, category, _pattern(anywhere + record_only))
    for name, category, anywhere, record_only in EMPLOYERS
)

_SCHOOL_RULES = tuple(
    (name, category, _pattern(anywhere + record_only))
    for name, category, anywhere, record_only in SCHOOLS
)


# Occurrences that are the wrong institution wearing the right name. Keyed by
# canonical name, matched at the start of the hit, and each one is a real
# confusion rather than a hypothetical: Penn State is not Penn, Michigan State
# is not Michigan, and Duke Energy is a utility.
_DENY = {
    "Penn": re.compile(r"Penn\s+State", re.IGNORECASE),
    "Michigan": re.compile(r"Michigan\s+State", re.IGNORECASE),
    "Washington": re.compile(r"Washington\s+State", re.IGNORECASE),
    "Duke": re.compile(r"Duke\s+Energy", re.IGNORECASE),
    "Apple": re.compile(r"Apple\s+Valley", re.IGNORECASE),
    "Meta": re.compile(r"Meta[\s-]*analys", re.IGNORECASE),
    # Collisions the real corpus produced, each one a different institution
    # that happens to contain a listed name.
    "Columbia": re.compile(r"British\s+Columbia", re.IGNORECASE),
    "UC Berkeley": re.compile(r"Berkeley\s+College", re.IGNORECASE),
    "Georgia": re.compile(r"Georgia\s+(?:Tech|Institute|State|Southern)",
                          re.IGNORECASE),
    "MIT": re.compile(r"MIT\s+World\s+Peace", re.IGNORECASE),
    "Maryland": re.compile(r"Maryland\s+Eastern\s+Shore", re.IGNORECASE),
}


# The word after the name that turns an employer into a product. This is the
# second line of defence, behind the job-line rule: a genuine job entry can
# still mention a product -- "Engineer, Acme Corp, 2019-2023, built on Amazon
# S3" is one line -- and without this that line reports Amazon as an employer.
#
# It is a single generic list rather than one per company because the failure
# is generic: every one of these vendors names its products after itself.
_PRODUCT_AFTER = re.compile(r"""\s*(?:
    cloud|ads?|adwords|analytics|workspace|sheets|docs|slides|drive|maps|meet|
    forms|apps\s+script|search\s+console|tag\s+manager|colab|bigquery|looker|
    gemini|firebase|
    azure|office|excel|word|powerpoint|teams|365|dynamics|power\s*bi|copilot|
    outlook|sharepoint|sql\s+server|visual\s+studio|windows|entra|
    web\s+services|rds|ec2|s3|dynamodb|lambda|sagemaker|redshift|bedrock|
    aurora|cloudfront|api\s+gateway|
    pay|music|watch|store|
    pixel|business\s+suite|llama|
    crm|marketing\s+cloud|sales\s+cloud|service\s+cloud|lightning|apex|sfdc|
    watson|cloud\s+pak|maximo|
    hana|abap|fiori|ariba|successfactors|erp|s/?4|b1|bw|basis|sd|mm|fico|
    financials|fusion|netsuite|peoplesoft|
    fba|sellers?|seller\s+central|marketplace|tac|vrp|quest|shop|ads?\s+manager|
    emc|poweredge|isilon|
    learning|recruiter|sales\s+navigator|premium|support|helpdesk|
    graphql|client|server|sdk|api|integration|plugin|checkout|subscriptions|
    actions|pages|repo|repositor|
    certified|certificate|certification|
    designs?|wireframes?|prototypes?|file|board|
    premiere|photoshop|illustrator|acrobat|creative\s+cloud|after\s+effects|
    claude|gpt|whisper|codex
    )\b""", re.IGNORECASE | re.VERBOSE)


# A LINE ABOUT A COURSE, not a job. This is the single biggest false-positive
# class left after the job-line rule, because a certificate line has every
# structural feature a job entry has -- an organisation, a date, no bullet:
#
#     IBM Generative AI for Business Intelligence -- Certificate, Coursera
#     Deloitte Data Analytics Job Simulation - Forage | July 2026
#     Career Essentials in Business Analysis by Microsoft and LinkedIn
#     AWS Certified Machine Learning Foundations (Aug 2025)
#     Oracle University  *  Issued Feb 2026
#
# Also the MOOC problem on the education side -- "CS50, Harvard University",
# "Python for Everybody -- University of Michigan" -- which is the same line
# wearing a university's name instead of a vendor's.
_A_COURSE = re.compile(r"""\b(?:
    certificat|certified|certification|credential|badge|licens|
    coursera|udemy|edx|udacity|forage|simplilearn|datacamp|pluralsight|
    job\s+simulation|virtual\s+experience|specialization|
    specialisation|bootcamp|mooc|online\s+course|nanodegree|
    workshop|webinar|training|course\s*work|coursework|
    academy|virtual\s+(?:programme|program|experience)|essentials|
    devfest|developers?\s+group|meetup|hackathon|
    issued|skill\s*build|summer\s+of\s+code|\bcsr\b|sponsored\s+by
    )""", re.IGNORECASE | re.VERBOSE)

# A LINE ABOUT SOMETHING THEY BUILT, not somewhere they worked. A portfolio
# project names companies freely -- an "Airbnb Clone", an "Amazon Inc. Equity
# Valuation" written for a class -- and reads structurally like a job entry.
_A_PROJECT = re.compile(
    r"\b(?:clone|capstone|case\s+study|side\s+project|personal\s+project|"
    r"portfolio|equity\s+valuation|mock|sample\s+app|toy\s+project)\b",
    re.IGNORECASE)

# A LINE ABOUT SOMEBODY ELSE'S COMPANY. Clients, partners and logos on a
# slide. "Presenting to stakeholders at Amazon, IKEA, SAP and Cisco" is a
# sentence about four companies the candidate did not work for, and it carries
# an "at" connector, so nothing else catches it.
#
# THE WORDS ARE NOT ENOUGH ON THEIR OWN, and matching them bare was a bug that
# cost a real candidate her real job: "Operations Manager -- Customer Service |
# Amazon India  Sep 2020 - Aug 2025" was thrown away because `customers?`
# matched "Customer". Half the job titles in customer support, customer success
# and account management contain one of these nouns.
#
# So each one needs the grammar that makes it a list of other people's
# companies -- a colon, a "such as", an "including", or a preposition after
# "stakeholders"/"executives". A noun sitting in a job title has none of those.
_THIRD_PARTY = re.compile(r"""
      \b(?:clients?|customers?|partners?|accounts?|brands?|logos?|vendors?)
        \s*(?::|\s+(?:such\s+as|including|like|served|serviced|serving|
                       spanning))
    | \b(?:client|account|brand)\s+(?=[A-Z])
    | \bserving\s+(?:clients?|customers?|brands?)
    | \bagency\s+(?:for|serving)
    | \b(?:stakeholders?|executives?|leadership|leaders?)\s+(?:at|across|from)
    | \bon\s+behalf\s+of\b
    | \bsuch\s+as\b
    """, re.IGNORECASE | re.VERBOSE)


# A VENDOR NAME IN FRONT OF A JOB TITLE names the specialism, not the
# employer: "SAP Consultant, AUNDE Group" worked at AUNDE, "Salesforce
# Trainee" and "ServiceNow Developer" are the same shape. Only when the title
# follows IMMEDIATELY -- "IBM - Applied AI Engineer" puts a separator between
# the two and really is a job at IBM.
_TITLE_AFTER = re.compile(
    r"\s+(?:consultant|developer|engineer|analyst|administrator|admin|"
    r"specialist|architect|trainee|intern|practitioner|technician|"
    r"programmer|tester|expert|professional)\b", re.IGNORECASE)


# A date range, which is the strongest single tell that a line is a job entry.
#
# Four shapes, because CVs write dates four ways and the corpus contains all
# of them. The last two were added after an audit found real jobs being
# dropped for no reason but their punctuation:
#
#     Apple, New York, NY            10/2011 - 10/2025     month/year
#     Microsoft / Nokia (2012-14)  . Bank of America (2006-09)   short end year
#
# The end year is `(?:19|20)?\d\d` so that "2016-17" closes as well as
# "2016-2020"; the start is always four digits, which is what stops a price or
# a headcount reading as a date.
_DATED = re.compile(r"""(?:
      \d{1,2}\s*/\s*(?:19|20)\d\d
    | (?:19|20)\d\d\s*(?:[-\u2012-\u2015/]|\bto\b|\bthrough\b|\buntil\b)\s*
      (?:\d{1,2}\s*/\s*)?(?:(?:19|20)?\d\d|present|current|now|date|ongoing)
    | (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*,?\s*
      (?:19|20)\d\d
    | \b(?:19|20)\d\d\s*[-\u2012-\u2015]\s*(?:19|20)\d\d\b
    )""", re.IGNORECASE | re.VERBOSE)

# "Senior Engineer at Acme", "PM @ Acme". A connector between a role and a
# name, which is how a headline and many one-line job entries are written.
_AT_CONNECTOR = re.compile(r"\s(?:at|@)\s", re.IGNORECASE)

# The corporate suffixes that make a name an organisation on their own, for a
# job entry that carries neither dates nor a connector.
_CORPORATE = re.compile(
    r"\b(?:inc|llc|l\.l\.c|ltd|limited|corp|corporation|plc|gmbh|pvt|"
    r"private\s+limited|&\s*co)\b\.?",
    re.IGNORECASE)

# A line that is a list of things rather than a statement about a job. Either
# it is introduced as one, or it is mostly separators -- "React | Node | AWS |
# Docker | Figma" carries four pipes and no sentence.
_LIST_HEAD = re.compile(
    r"^\W*(?:technical\s+)?(?:skills?|tools?|technolog(?:y|ies)|tech\s+stack|"
    r"stack|languages?|frameworks?|platforms?|software|proficienc(?:y|ies)|"
    r"competenc(?:y|ies)|certifications?|courses?|awards?|interests?|"
    r"expertise|other|productivity|databases?|libraries)\b(?:\s*[:–-]|\s+(?:used|include[ds]?))",
    re.IGNORECASE)

# The bullet characters a duties line starts with. A job HEADER is not a
# bullet; the bullets underneath it are what the person did there, and that is
# exactly where product names live.
_BULLET = re.compile(
    r"^\s*(?:[•●▪▸►‣⁃·*−–—-]|o(?=\s))\s*")

_URL = re.compile(r"\b(?:https?://|www\.)\S+|\b\S+\.(?:com|io|dev|ai|org|net)/\S*",
                  re.IGNORECASE)

# A degree, which is what makes a line an education entry rather than a
# mention of a city that happens to share a university's name.
_DEGREE = re.compile(
    r"\b(?:b\.?\s?(?:s|a|sc|e|eng|tech|com)\b|bachelor|"
    r"m\.?\s?(?:s|a|sc|eng|tech|phil)\b|master|mba|m\.?b\.?a|"
    r"ph\.?\s?d|doctora|j\.?d\b|ll\.?m|m\.?d\b|"
    r"undergraduate|postgraduate|graduated|diploma|"
    r"university|college|institute|school\s+of)\b",
    re.IGNORECASE)


# NAMES WE WILL ONLY BELIEVE FROM THE ATS.
#
# For most of the list, a job-shaped line is evidence enough. For these it is
# not, because their product IS their name and this applicant pool mentions
# those products constantly -- these are AI and data seats, and a CV here names
# OpenAI or Anthropic the way a CV in 2010 named Excel:
#
#     Call Intelligence Platform | Ringba, AssemblyAI, LEMUR, OpenAI, Python
#     (GitHub Copilot, OpenAI APIs) for coding support, debugging, testing
#     Python, LangGraph, Anthropic Claude API, MCP
#
# All three sit on lines with dates and no bullet, which is every structural
# signal a job entry has. Rather than keep inventing rules to tell "used the
# API" from "worked there", these are accepted only from Workable's own parse,
# where a company is a company because the ATS put it in the company field.
#
# The cost is recall: somebody who genuinely worked at OpenAI and applied
# through the portal is missed. That is the right way round for a panel headed
# "worth a look" -- and it is exactly the trade the whole module makes.
STRUCTURED_ONLY = frozenset({
    "OpenAI", "Anthropic", "Databricks", "Snowflake", "Datadog",
})


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
#
# THE SECTION IS THE RULE. A company name counts when it is inside the work
# experience section, and it does not count anywhere else -- not under
# PROJECTS, not under CERTIFICATIONS, not under FELLOWSHIPS, not in a skills
# list, not in the header. The same for schools and the education section.
#
# This replaced a version that looked at every line of the CV and tried to
# judge each one on its own shape. That version was beaten by a real CV in
# this database whose PROJECTS section reads:
#
#     PROJECTS
#     Google Hackathon                                 New York, NY
#     Google AI Challenge - Finalist                   Oct-Nov 2024
#
# Two lines with an organisation, a place and a date range: every structural
# signal a job entry has, and the candidate has never worked at Google. No
# per-line rule can tell those apart from a job, because as lines they are not
# different. What is different is which heading they sit under.
#
# The cost is that a CV whose headings cannot be found contributes nothing,
# and that is the intended behaviour rather than a gap to be filled in later.
# Guessing at employment from an unsectioned document is exactly the thing
# that produced "368 of our applicants worked at Google".

# A heading is recognised by WHAT IT SAYS, not by how it is typeset.
#
# Shape alone -- short, title-case, no digits, no comma -- was tried first and
# it fails in the direction that costs the most: "Amazon Development Centre"
# and "Kent State University" have exactly that shape, so they were read as
# headings and CLOSED the section they were sitting in. A third of the corpus
# lost its work-experience block to an employer's own name.
#
# So shape narrows the candidates and the vocabulary decides. The cost is a
# heading nobody has listed here -- it will not close a section, and the block
# runs on into whatever follows. That is the cheaper mistake: the line rules
# are still there underneath, and a section that runs long is a few extra
# candidates to check rather than a third of the corpus contributing nothing.

_HEADING_TOKENS = re.compile(r"[\s&/|,\-\u2013\u2014]+")

# Opens the block employers may be read from, and nothing else may.
_HEAD_EXPERIENCE = re.compile(
    r"^(?:work|professional|employment|career|relevant|industry)?\s*"
    r"(?:experience|employment|history|background)"
    r"(?:\s+(?:history|background|summary))?$", re.IGNORECASE)

_HEAD_EDUCATION = re.compile(
    r"^(?:academic\s+)?(?:education|academics|qualifications|"
    r"educational\s+background|academic\s+background|"
    r"education\s+and\s+training)$", re.IGNORECASE)

# Everything else that ends a block. A heading Claude has not thought of will
# not appear here, which is why the list is generous.
_HEAD_OTHER = re.compile(
    r"^(?:.*\b(?:project|certificat|licen[sc]|skill|competenc|award|honou?r|"
    r"publication|research|fellowship|volunteer|voluntary|leadership|"
    r"activit|interest|hobb|reference|summary|profile|objective|language|"
    r"patent|conference|course|training|achievement|extracurricular|"
    r"membership|affiliation|portfolio|contact|tool|technolog|framework|"
    r"software|platform|strength|highlight|accomplishment)\w*\b.*)$",
    re.IGNORECASE)

# A qualifier that turns an experience heading into something else.
# "LEADERSHIP EXPERIENCE" and "VOLUNTEER EXPERIENCE" are not employment.
_NOT_EMPLOYMENT_HEAD = re.compile(
    r"\b(?:project|volunteer|voluntary|leadership|extracurricular|activit|"
    r"fellowship|research|award|publication|certificat|training|course)",
    re.IGNORECASE)


def _heading_shape(line: str) -> Optional[str]:
    """The heading text of `line`, or None when it cannot be one at all."""
    text = line.strip().rstrip(":").strip()
    if not text or len(text) > 64 or "," in text:
        return None
    if any(ch.isdigit() for ch in text) or text.endswith((".", ";")):
        return None
    tokens = [t for t in _HEADING_TOKENS.split(text) if t]
    if not tokens or len(tokens) > 5:
        return None
    if sum(1 for ch in text if ch.isalpha()) < 3:
        return None
    return " ".join(tokens)


def _heading_kind(line: str) -> Optional[str]:
    """"experience", "education", "other", or None for an ordinary line."""
    text = _heading_shape(line)
    if text is None:
        return None
    if _NOT_EMPLOYMENT_HEAD.search(text):
        return "other"
    if _HEAD_EXPERIENCE.match(text):
        return "experience"
    if _HEAD_EDUCATION.match(text):
        return "education"
    if _HEAD_OTHER.match(text):
        return "other"
    return None


def _sections(text: str) -> dict[str, str]:
    """
    The work-experience and education blocks of a CV, each possibly empty.

    A heading opens a block and the next heading of any kind closes it, so a
    PROJECTS or CERTIFICATIONS heading ends the experience section. Two
    experience headings in a row -- "PROFESSIONAL EXPERIENCE" then "ADDITIONAL
    EXPERIENCE" -- both collect, which is why this accumulates rather than
    taking the first block and stopping.
    """
    blocks: dict[str, list[str]] = {"experience": [], "education": []}
    current: Optional[str] = None
    for raw in (text or "").splitlines():
        kind = _heading_kind(raw)
        if kind is not None:
            current = kind if kind in blocks else None
            continue
        if current:
            blocks[current].append(raw)
    return {name: "\n".join(lines) for name, lines in blocks.items()}


# Bullet glyphs that came out of a PDF as control characters or
# private-use code points. A real line in this corpus begins
# "\x7f Managed ServiceNow queues..." -- a duty, and an obvious one, but
# str.strip() does not strip \x7f, so the line was not a bullet, did not
# start lower case, and none of the duty rules ever saw it.
_GLYPH = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f"
                    "\u007f-\u009f\ue000-\uf8ff]")


def _clean(text: str) -> str:
    """
    URLs out, and PDF glyph rubbish turned into the bullet it was drawing.

    A github.com link and a linkedin.com/in/ link are not jobs, and nothing
    under a bullet is one either -- so the two substitutions are the same
    job: making a line say what it looked like on the page it came from.
    """
    return _GLYPH.sub("\u2022", _URL.sub(" ", text or ""))


# HOW FAR INTO THE LINE THE EMPLOYER CAN BE.
#
# PDF extraction routinely runs a job header and its first duty bullet
# together into one line, and the duties are where the clients live:
#
#     ACCENTURE | Bengaluru  Trust and Safety Operations Analyst (Meta & YouTube
#     Shell Recharge Solutions  Senior Product Manager  * Launched ... with BMW
#     Virtual Reality Docent  Lincoln Center  * Facilitated VR experiences  Meta
#
# In every one of those the employer is at the front and the company that is
# NOT the employer is a long way in. An employer name is part of the header,
# so it is near the start; past this, the line has stopped being a header.
_EMPLOYER_WITHIN = 60

# A job header does not begin with a verb and does not begin in lower case.
# Both of those are a sentence -- a duty, or the tail of the line above it.
_DUTY_OPENER = re.compile(
    r"^\s*(?:managed|led|built|developed|designed|implemented|created|"
    r"architected|spearheaded|owned|delivered|coordinated|facilitated|"
    r"analy[sz]ed|automated|drove|ran|supported|maintained|collaborated|"
    r"partnered|worked|served|provided|performed|conducted|assisted|"
    r"responsible|utilized|utilised|leveraged|spearheading|handling|"
    r"managing|leading|building|working|insourced|launched|oversaw|directed|"
    r"executed|produced|generated|negotiated|restructured|championed|"
    r"introduced|established|scaled|grew|reduced|increased|improved|"
    r"optimi[sz]ed|streamlined|migrated|integrated|deployed|rebuilt|"
    r"overseeing|delivering|driving|owning|reporting)\b", re.IGNORECASE)

# A name in brackets beside a job is the account, the programme or the
# platform -- "(Cisco TAC)", "(Amazon FBA)", "(Meta & YouTube Ecosystems)",
# "(Amazon.com Agency)". The employer is whatever sits outside them.
_BRACKETED = re.compile(r"\([^)]*\)")


def _in_brackets(segment: str, at: int) -> bool:
    return any(m.start() < at < m.end() for m in _BRACKETED.finditer(segment))


def _spaced_out(line: str) -> bool:
    """
    Whether a line is PDF text that extracted one letter at a time.

    "H u m a n  R e s o u r c e  M a n a ge r s" is a real line from a real CV
    in this database, and it matched General Electric -- because "ge" in
    "M a na ge r s" has a space on either side, which is a word boundary. Any
    two-letter name on these lists can be conjured out of a line like this, so
    the line is dropped rather than the names being made longer.
    """
    tokens = line.split()
    if len(tokens) < 8:
        return False
    # Single LETTERS only. Counting every one-character token made
    # "NVIDIA  2018 - 2023  *  5 yrs 5 mos" look letter-spaced -- the dash, the
    # bullet and the two 5s are four of its nine tokens -- and threw away a
    # five-year job at Nvidia.
    singles = sum(1 for t in tokens if len(t) == 1 and t.isalpha())
    return singles / len(tokens) > 0.4


def _job_lines(text: str) -> list[str]:
    """
    The lines of a CV that look like an employment entry.

    Three shapes are accepted and everything else is dropped:

        Senior Engineer, Acme Corp, New York -- 2019 to 2023      (dates)
        Product Manager at Acme                                   (connector)
        Acme Technologies Pvt Ltd                                 (corporate)

    A bullet is never one. The bullets under a job header are the duties, and
    duties are where "migrated to Amazon RDS" and "converted Figma designs"
    live -- the two sentences that made the first version of this module
    useless. A list line is never one either, however it is punctuated.
    """
    lines = _clean(text).splitlines()
    # Which lines carry a date range. A CV very often puts the employer on one
    # line and the dates on the next --
    #
    #     Amazon Development Center - Risk Analyst (TRMS)
    #     Bangalore, India                     Jun 2019 - Mar 2022
    #
    # -- and requiring both on one line threw away the largest single group of
    # real jobs in this corpus. Read as a two-line header instead. This only
    # relaxes WHERE the date may be; everything else still has to hold, and it
    # only applies inside the work-experience section to begin with.
    dated = [bool(_DATED.search(l)) for l in lines]

    out = []
    for index, raw in enumerate(lines):
        line = " ".join(raw.split())
        # Collapsed before measuring: PDF text pads lines out with runs of
        # spaces, and a 90-character header was being rejected as a
        # 230-character one.
        if not line or len(line) > 220:
            continue
        near_date = (dated[index]
                     or any(dated[j] for j in (index - 1, index + 1)
                            if 0 <= j < len(lines)))
        if _BULLET.match(raw) or _LIST_HEAD.match(line) or _spaced_out(line):
            continue
        # A header starts with a name, a title or a date -- never with a verb,
        # and never in lower case, which means the line above wrapped.
        if _DUTY_OPENER.match(line) or line[:1].islower():
            continue
        if (_A_COURSE.search(line) or _THIRD_PARTY.search(line)
                or _A_PROJECT.search(line)):
            continue
        # A line carrying four or more separators is an inventory, not a
        # sentence about a job, whatever it calls itself.
        if sum(line.count(sep) for sep in ("|", "•", "·", ";")) >= 4:
            continue
        if near_date or _AT_CONNECTOR.search(line) or _CORPORATE.search(line):
            out.append(line)
    return out


def _education_lines(text: str) -> list[str]:
    """
    The lines that look like an education entry: they name a degree, or an
    institution word. Much less fraught than the employment side -- nobody
    lists a university in their tech stack -- so the bar is lower.
    """
    out = []
    for raw in _clean(text).splitlines():
        line = raw.strip()
        if not line or len(line) > 220 or not _DEGREE.search(line):
            continue
        # A MOOC is not a degree, and a list of organisations is not a school.
        if _A_COURSE.search(line):
            continue
        if sum(line.count(sep) for sep in ("|", "\u2022", "\u00b7", ";")) >= 4:
            continue
        out.append(line)
    return out


def _workable_companies(text: str) -> list[str]:
    """
    The company out of each of Workable's parsed experience entries.

    The format is `- {title} at {company} ({dates})`, built by
    scraping/workable_candidates._experience_summary, with the summary
    underneath indented by two spaces. Split on the LAST " at ", because a
    title can contain one -- "Director at Large at Acme" -- and the company
    cannot be the first half. A title-less entry is the company alone.
    """
    out = []
    for raw in (text or "").splitlines():
        if not raw.startswith("- "):
            continue
        head = raw[2:].split(" (")[0].strip()
        out.append(head.rsplit(" at ", 1)[-1].strip() if " at " in head else head)
    return [c for c in out if c]


def _hits(rules, segments: list[str], source: str,
          employment: bool = False) -> dict[str, dict]:
    """
    Which names appear in `segments`, minus the denied and the productised.

    Returned keyed by name so the caller can merge two sources and let the
    stronger one win without matching on list position.
    """
    found: dict[str, dict] = {}
    for segment in segments:
        for name, category, pattern in rules:
            if name in found or pattern is None:
                continue
            deny = _DENY.get(name)
            for m in pattern.finditer(segment):
                # Only for the employment side -- an education entry is one
                # line and a school can sit anywhere on it.
                if employment and m.start() > _EMPLOYER_WITHIN:
                    break
                if employment and _in_brackets(segment, m.start()):
                    continue
                # A window rather than a forward match: half of these
                # confusions put the disqualifying word BEFORE the name --
                # "University of British Columbia" is not Columbia.
                if deny and deny.search(segment[max(0, m.start() - 30):m.end() + 30]):
                    continue
                if _PRODUCT_AFTER.match(segment, m.end()):
                    continue
                if _TITLE_AFTER.match(segment, m.end()):
                    continue
                found[name] = {
                    "name": name, "category": category, "source": source,
                    # Whitespace collapsed before it is stored. PDF text
                    # extraction leaves runs of spaces and trailing padding
                    # in almost every line, and this string is shown to a
                    # reader in a tooltip -- where "Optum, CA | Product
                    # Manager" followed by ninety spaces reads as a bug.
                    "line": " ".join(segment.split())[:180],
                }
                break
    return found


def _settle(found: dict[str, dict], rules) -> list[dict]:
    """
    Drop a name that is only ever the first word of a longer one on the list.

    Sorting a single rule's aliases longest-first settles collisions inside one
    name -- "Bain Capital" before "Bain" -- but says nothing about a collision
    BETWEEN two names, and that is where the worst pair lives: one candidate at
    Bain Capital would otherwise report as having worked at the consultancy
    too, because `\\bBain\\b` is perfectly happy with the first word of "Bain
    Capital". Settled on the evidence line rather than on the whole document,
    since that is where both were found.
    """
    out = []
    for name, hit in found.items():
        pattern = next(p for n, _, p in rules if n == name)
        line = hit["line"]
        shadowed = False
        for other, other_hit in found.items():
            if other == name or other_hit["line"] != line:
                continue
            other_pattern = next(p for n, _, p in rules if n == other)
            mine = {m.span() for m in pattern.finditer(line)}
            theirs = [m.span() for m in other_pattern.finditer(line)]
            if mine and all(any(t[0] <= s[0] and s[1] <= t[1] and t != s
                                for t in theirs) for s in mine):
                shadowed = True
                break
        if not shadowed:
            out.append({k: hit[k] for k in ("name", "category", "source", "line")})
    return out


def read(sub: dict) -> dict:
    """
    One candidate's employers and schools, as a cacheable sub-document.

    Two sources, and the stronger one wins per name. Workable's own parse is
    structured data from the ATS that read the file; the CV path is a
    heuristic over lines that look like job entries. Both are reported, and
    `source` says which -- along with `line`, the evidence itself, so a reader
    who doubts a chip can see the sentence it came from without opening the CV.

    Pure: it touches nothing but the dict it is given, so it can be run over a
    batch out of a projection without a second database round trip per row.

    An empty `employers` and `schools` is a real answer and is stored as one.
    Without that, every candidate who matched nothing would be re-read on every
    page load forever.
    """
    cv = sub.get("resume_text") or ""

    employers = _hits(_EMPLOYER_RULES,
                      _workable_companies(sub.get("workable_experience")),
                      "record")
    schools = _hits(_SCHOOL_RULES,
                    [line for line in (sub.get("workable_education") or "").splitlines()
                     if line.strip() and not _A_COURSE.search(line)],
                    "record")

    # The CV path reads the two sections and nothing outside them. A document
    # with no findable work-experience heading contributes no employers --
    # see the note over _sections. The candidate's headline is not consulted
    # either: "AI Consultant at Google Cloud Partner" is a self-description,
    # not a work history.
    blocks = _sections(cv)
    for name, hit in _hits(_EMPLOYER_RULES, _job_lines(blocks["experience"]),
                           "cv", employment=True).items():
        if name in STRUCTURED_ONLY:
            continue
        employers.setdefault(name, hit)
    for name, hit in _hits(_SCHOOL_RULES,
                           _education_lines(blocks["education"]), "cv").items():
        schools.setdefault(name, hit)

    found_employers = _settle(employers, _EMPLOYER_RULES)
    found_schools = _settle(schools, _SCHOOL_RULES)
    return {
        "employers": found_employers,
        "schools": found_schools,
        # Denormalised so the dashboard's query can be an index lookup. The
        # honest version of this predicate is `employers != [] OR schools !=
        # []`, which is two multikey comparisons against an array and reads
        # every document in the collection -- and the collection is 10,000
        # submissions of which about 150 are ever in either list.
        "hit": bool(found_employers or found_schools),
        # What the read was made from, so a row with nothing on it can say
        # "no CV text to read" rather than "no notable employers", which are
        # very different facts about a candidate.
        "had_text": bool(cv or sub.get("workable_experience")
                         or sub.get("workable_education")),
        "version": VERSION,
    }


# The fields `read` looks at. Named here so the store can project exactly these
# and nothing else: `resume_text` alone is up to 8 KB a row, and a scan that
# also dragged the answer markdown along would move megabytes per refresh.
SOURCE_FIELDS = (
    "resume_text", "workable_experience", "workable_education",
    "candidate_headline",
)

# ---------------------------------------------------------------------------
# Which seats the school spotlight is for
# ---------------------------------------------------------------------------

_NEW_YORK = re.compile(r"\bnew york\b|\bnyc\b|\bmanhattan\b", re.IGNORECASE)
_ONSITE = re.compile(r"\bon-?site\b|\bin-?person\b", re.IGNORECASE)
_HYBRID = re.compile(r"\bhybrid\b", re.IGNORECASE)


def new_york_seat(location: Optional[str]) -> Optional[str]:
    """
    "onsite", "hybrid", or None for a seat that is not a New York one.

    Read off the rubric pack's `location` line, which is the only place this
    codebase records where a seat actually is -- the portal crawl does not
    carry one. A role with no pack grid therefore has no location and answers
    None, which keeps it out of the school spotlight rather than guessing it in.

    Hybrid counts. The spotlight exists because a seat needs somebody who can
    be in the building, and "hybrid New York" needs that too -- the caller is
    handed the word so the panel can label which of the two a row is.
    """
    if not location or not _NEW_YORK.search(location):
        return None
    if _HYBRID.search(location):
        return "hybrid"
    if _ONSITE.search(location):
        return "onsite"
    return None
