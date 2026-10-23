"""Decides which visitor messages are questions worth recording.

Recorded questions are listed in the lead email, so this errs towards
including: a message is kept if it looks like a question at all, or is long
enough to be about something. What it leaves out is filler ("okay",
"thanks", "no that's it") and messages that only hand over contact details.

No model call: this runs on every turn and must cost nothing.
"""

import re

from app.leads.extraction import EMAIL_PLACEHOLDER, PHONE_PLACEHOLDER

# A message this long is about something even without a question mark,
# e.g. "I did my 200 hours in the UK and want to do the 300 hour one".
SUBSTANTIVE_LENGTH = 40

QUESTION_WORDS = {
    "what", "whats", "what's", "how", "when", "where", "why", "who", "whom",
    "whose", "which", "can", "could", "do", "does", "did", "is", "are",
    "was", "were", "will", "would", "should", "shall", "may", "might",
    "have", "has", "any", "anything", "tell", "explain", "please",
}

QUESTION_PHRASES = (
    "what about", "how about", "want to know", "like to know",
    "need to know", "wondering", "details", "information", "info on",
    "info about",
)

# Question marks in other scripts too: full-width (Chinese, Japanese),
# Arabic, and Spanish's opening mark.
QUESTION_MARKS = ("?", "？", "؟", "¿")

# Words that often open a message before the question itself:
# "okay, what about...", "so is there...", "hi, do you...".
FILLER = {
    "ok", "okay", "k", "so", "and", "also", "but", "then", "hi", "hey",
    "hello", "um", "uh", "well", "great", "thanks", "thank", "you", "cool",
    "right", "sure", "yes", "yeah", "no",
}


def is_visitor_question(message: str) -> bool:
    text = message.replace(EMAIL_PLACEHOLDER, " ").replace(PHONE_PLACEHOLDER, " ")
    text = text.strip().lower()
    if not text:
        return False

    if any(mark in text for mark in QUESTION_MARKS):
        return True

    words = re.findall(r"[a-z']+", text)
    meaningful = [w for w in words if w not in FILLER]
    if meaningful and meaningful[0] in QUESTION_WORDS:
        return True

    if any(phrase in text for phrase in QUESTION_PHRASES):
        return True

    if not text.isascii():
        # Another language, or accented letters: the English word lists do
        # not apply and [a-z] drops its letters, so the whole message's
        # length decides.
        return len(text) >= SUBSTANTIVE_LENGTH

    return len(" ".join(meaningful)) >= SUBSTANTIVE_LENGTH
