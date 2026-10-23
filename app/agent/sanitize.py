"""Remove characters a person cannot see from what a visitor typed.

Unicode has characters that render as nothing: tag characters (U+E0000
block), zero-width spaces and joiners, bidirectional controls, variation
selectors. Text can be written entirely in them, so a message that looks
like "What courses do you have?" can carry instructions only the model
reads (OWASP LLM01, 2026). A visitor never needs them, so they are removed
before the message reaches the model, the logs or storage.

Kept: the zero-width joiner and the emoji variation selectors, which real
emoji (family, coloured hearts) are built from, and the zero-width
non-joiner, which Persian and some Indic scripts need.
"""

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

_KEEP = {
    "\u200c",  # zero-width non-joiner: part of correct Persian and Indic text
    "\u200d",  # zero-width joiner: joins emoji sequences
    "\ufe0e",  # text presentation selector
    "\ufe0f",  # emoji presentation selector
}


def _invisible(char: str) -> bool:
    if char in _KEEP:
        return False
    code = ord(char)
    if 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF:
        return True  # variation selectors, used to hide bytes in text
    # Cf ("format"): tag characters, zero-width space, word
    # joiner, bidirectional overrides and isolates, soft hyphen, BOM.
    return unicodedata.category(char) == "Cf"


def strip_invisible(text: str) -> str:
    """The text without invisible characters; logs how many were removed."""
    cleaned = strip_invisible_quietly(text)
    removed = len(text) - len(cleaned)
    if removed:
        logger.warning("Removed %d invisible characters from a message", removed)
    return cleaned


# Phrases that address a language model rather than a reader. They have no
# business in a yoga school's documents or facts sheet, and a planted one is
# indirect prompt injection (OWASP LLM01, and LLM04/05 for the knowledge
# base). Kept narrow, so ordinary text such as "you must bring a mat" is not
# caught.
_INSTRUCTION_PATTERNS = [
    r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier|all|your)\b.{0,20}\b(instructions?|rules|prompts?|guidelines)\b",
    r"\byou are now\b",
    r"\bfrom now on,? you\b",
    r"\bsystem (prompt|message|instructions?)\b",
    r"\b(new|updated) instructions? for the (assistant|ai|bot|model)\b",
    r"\b(the )?(assistant|chatbot|ai model|language model)\s+(must|should) (always|never|now)\b",
    r"\bdo not (tell|reveal to|mention to) the (user|visitor)\b",
    r"<\|im_start\|>|<\|im_end\|>|\[/?INST\]|<<SYS>>|</?system>",
]
_INSTRUCTION_RE = re.compile("|".join(_INSTRUCTION_PATTERNS), re.IGNORECASE | re.DOTALL)


def instruction_like(text: str) -> str | None:
    """The first phrase in text that reads like an instruction to a model,
    or None. Also flags invisible characters, which have no place in
    content and are how instructions are hidden from a human reviewer."""
    if strip_invisible_quietly(text) != text:
        return "invisible characters"
    match = _INSTRUCTION_RE.search(text)
    return match.group(0) if match else None


def strip_invisible_quietly(text: str) -> str:
    return "".join(c for c in text if not _invisible(c))
