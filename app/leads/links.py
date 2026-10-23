"""Links a visitor typed, made unclickable in the lead email (backlog 108).

The lead email shows the visitor's own words, so whatever link they type
reaches the team inside an email from us, where it looks more trustworthy
than it is: a way to phish the business through its own chat. Links in
visitor-written text (and in the model's summary of it) are shown as
"hxxp://evil[.]example/login": still readable, but not clickable, and
email apps do not turn them back into links.

Bare domains are matched only with common top-level domains, so text such
as "Hatha.Ashtanga" is left alone.
"""

import re

LINK_CAUTION = (
    "A message from this visitor contains a link. It is shown unclickable "
    "(hxxp, [.]); check it is genuine before visiting it."
)

_TLDS = (
    "com|net|org|info|biz|io|co|in|uk|us|de|fr|es|it|nl|ru|cn|jp|au|ca|"
    "app|dev|xyz|top|site|online|link|live|shop|me|ly|gl|to|cc|tk|ml|ga|cf|gq"
)
_LINK = re.compile(
    r"\b(?:https?://|www\.)[^\s<>\"']+"
    rf"|\b(?:[a-z0-9-]+\.)+(?:{_TLDS})\b(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)


def _defang_one(match: re.Match) -> str:
    link = match.group(0)
    link = re.sub(r"^https?(?=://)", lambda m: "hxxp" + m.group(0)[4:].lower(),
                  link, flags=re.IGNORECASE)
    scheme, sep, rest = link.partition("://")
    if not sep:
        scheme, rest = "", link
    host, slash, path = rest.partition("/")
    return f"{scheme}{sep}{host.replace('.', '[.]')}{slash}{path}"


def defang(text: str) -> str:
    """text with every link made unclickable."""
    return _LINK.sub(_defang_one, text)


def has_link(text: str) -> bool:
    return bool(_LINK.search(text))
