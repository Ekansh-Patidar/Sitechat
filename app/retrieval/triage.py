"""Decides whether a message needs the knowledge base at all.

Retrieval costs an embedding call plus a vector search, roughly two seconds
from outside the datacentre's region. Spending that on "hi", "ok" and
"thanks" is waste, and it is a meaningful share of real conversations.

The check is deliberately conservative: it only skips when a message is
short AND matches a known conversational pattern. Anything it is unsure
about goes through retrieval, because a missed retrieval produces a wrong
answer while an unnecessary one only costs time.
"""

import re

# Short enough that there is no room for a real question.
MAX_SKIP_LENGTH = 25

# Whole-message patterns only. "thanks, but what about the fees?" must not
# match, so each pattern is anchored at both ends.
CONVERSATIONAL = re.compile(
    r"^(?:"
    r"hi|hey|hello|yo|namaste|"
    r"ok|okay|k|sure|right|fine|got it|understood|i see|"
    r"thanks|thank you|thanks a lot|ty|cheers|"
    r"bye|goodbye|see you|take care|"
    r"yes|yeah|yep|no|nope|"
    r"good morning|good afternoon|good evening|"
    r"great|nice|cool|perfect|awesome|lovely"
    r")"
    r"[\s!.,?]*$",
    re.IGNORECASE,
)


def needs_retrieval(message: str) -> bool:
    """True if this message should be answered from the knowledge base."""
    text = message.strip()

    if not text:
        return False

    if len(text) > MAX_SKIP_LENGTH:
        return True

    return not CONVERSATIONAL.match(text)