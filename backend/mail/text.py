"""
The two string primitives every outbound mail in this package needs.

WHY THEY LIVE HERE. There were four copies of `_first_name` and two of `_esc`
-- in brevo_client, candidate_mail, shortlist and, by alias, rejections. They
were identical, which is exactly the problem: an escaper that is right in three
files and stale in the fourth is an XSS hole in whichever mail nobody looked at
this release, and every one of these mails interpolates free text a candidate
or a manager typed.

candidate_mail already re-exported its pair as `esc_html` / `first_name_of` for
rejections.py to use, and that is the convention this module makes complete.
It could not be extended to brevo_client, which candidate_mail imports -- the
import would have closed a cycle. Nothing in here imports anything, from this
project or outside it, so everything can import from it.

candidate_mail keeps its `esc_html` / `first_name_of` aliases pointing at these,
so rejections.py's spelling is unchanged.
"""


def first_name(full_name) -> str:
    """
    "Viral Chovatiya" -> "Viral".

    The greeting reads as a personal follow-up, so it uses the first name --
    matching the original Workable invite, which opens "Dear Hashir,".
    Falls back to "there" rather than to an empty greeting, because "Hi ,"
    is worse than a generic one.
    """
    name = str(full_name or "").strip()
    return name.split()[0] if name else "there"


def esc(value) -> str:
    """
    Escape for HTML. Names, role titles, candidate notes and manager notes are
    all free text, and all of them reach a mail body through an f-string.

    Not html.escape(): this deliberately leaves the apostrophe alone. Every
    interpolation in this package lands in element text or in a double-quoted
    attribute, where a bare `'` is safe, and O'Brien reading as `O&#x27;Brien`
    in a plaintext part is a real regression. The four that matter are here.
    """
    return (str(value if value is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))
