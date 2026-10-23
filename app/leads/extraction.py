"""Pulls contact details out of a message and removes them from the text.

Two reasons to do this before the message reaches the model.

First, personal data would otherwise sit in the conversation history and be
re-sent on every subsequent turn, and appear in any logging of that history.
Extracting it once and storing it separately keeps it in one place that can
be deleted.

Second, the bot may ask for an email, but visitors also volunteer details
unprompted, mid-sentence, in messages about something else entirely. A rule
that only works when the bot asked first would miss most of them.
"""

import re

# Deliberately permissive. The aim is to catch what a person would recognise
# as an email, not to enforce RFC 5322. A false positive costs a redaction;
# a false negative costs a lead.
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# International numbers, written however people write them. Requires at least
# 8 digits to avoid matching prices, dates and course hours.
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,5}\d{2,4}")

MIN_PHONE_DIGITS = 8

EMAIL_PLACEHOLDER = "[email provided]"
PHONE_PLACEHOLDER = "[phone provided]"


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def extract_contact(message: str) -> tuple[str, str, str]:
    """Find an email and phone number, and return the message with them removed.

    Returns (email, phone, redacted_message). Empty strings when not found.
    """
    email = ""
    phone = ""
    redacted = message

    email_match = EMAIL_RE.search(redacted)
    if email_match:
        typed = email_match.group(0)
        redacted = redacted.replace(typed, EMAIL_PLACEHOLDER)
        # Stored lowercase. Mail providers ignore case, but Resend compares
        # recipients exactly: "Sam@x.com" was rejected as a different
        # address from "sam@x.com" in test mode.
        email = typed.lower()

    for match in PHONE_RE.finditer(redacted):
        candidate = match.group(0)
        if len(_digits(candidate)) >= MIN_PHONE_DIGITS:
            phone = candidate.strip()
            redacted = redacted.replace(phone, PHONE_PLACEHOLDER)
            break

    return email, phone, redacted


def looks_like_valid_email(email: str) -> bool:
    """A stricter check, used before storing.

    Extraction is permissive so nothing is missed. This is the gate before
    something is kept and later emailed, where a malformed address means a
    lead the team cannot reach.
    """
    if not email or len(email) > 254:
        return False
    if email.count("@") != 1:
        return False

    local, _, domain = email.partition("@")
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    if domain.startswith(".") or domain.endswith("."):
        return False
    if ".." in email:
        return False

    return True


# Common disposable-inbox services. Not exhaustive: a flag for the team,
# not a block, since a real visitor might use one for privacy.
THROWAWAY_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "sharklasers.com",
    "10minutemail.com", "tempmail.com", "temp-mail.org", "tempmail.net",
    "yopmail.com", "trashmail.com", "getnada.com", "dispostable.com",
    "maildrop.cc", "throwawaymail.com", "fakeinbox.com", "emailondeck.com",
    "mintemail.com", "mohmal.com", "tempinbox.com", "burnermail.io",
}


def looks_like_throwaway(email: str) -> bool:
    """Whether the address is at a known disposable-inbox service."""
    domain = email.rpartition("@")[2].lower()
    return domain in THROWAWAY_DOMAINS
