"""Retrieval metrics.

Pure functions, no network, no model, no randomness, so the same inputs
always give the same numbers. That is what makes them usable for comparing
two configurations.

Retrieved results are identified by section key, not chunk id:
"<document_id> | <heading path below the title>", for example
"pyramid-yogshala-website | 300-Hour ... (Rishikesh) > Curriculum". Chunk
ids change whenever a document is re-chunked; section keys only change when
the document's headings do, so labels survive re-ingesting.

A question's expected answer is a list of groups. Each group is a list of
labels that answer the same thing interchangeably ("either the old 200-hour
document's Certification section or the website's"). Every group must be
found for full recall, so a comparison question needs both sides.

The key and label formats are defined in app/documents/sections.py.
"""


from app.documents.sections import matches, section_key  # noqa: F401


def is_relevant(key: str, groups: list[list[str]]) -> bool:
    return any(matches(key, label) for group in groups for label in group)


def recall_at_k(keys: list[str], groups: list[list[str]], k: int) -> float:
    """Fraction of the answer groups found in the top k.

    The headline metric: a section that is never retrieved cannot be used,
    however good the model is.
    """
    if not groups:
        return 1.0  # Nothing to find, so nothing was missed.
    top = keys[:k]
    found = sum(
        1 for group in groups
        if any(matches(key, label) for key in top for label in group)
    )
    return found / len(groups)


def precision_at_k(keys: list[str], groups: list[list[str]], k: int) -> float:
    """Fraction of the top k that is relevant."""
    top = keys[:k]
    if not top:
        return 0.0
    return sum(1 for key in top if is_relevant(key, groups)) / len(top)


def reciprocal_rank(keys: list[str], groups: list[list[str]]) -> float:
    """1 / position of the first relevant result, or 0 if none appear."""
    if not groups:
        return 1.0
    for position, key in enumerate(keys, start=1):
        if is_relevant(key, groups):
            return 1.0 / position
    return 0.0


def noise_at_k(keys: list[str], groups: list[list[str]], k: int) -> int:
    """How many results were retrieved for a question with no answer.

    For unanswerable questions the other metrics say nothing useful, so this
    counts what got pulled in anyway. Every irrelevant section is context
    the model might answer from.
    """
    if groups:
        return 0
    return len(keys[:k])
